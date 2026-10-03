"""Private, bounded MOBI conversion and durable editable ebook drafts."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import posixpath
import re
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from pydantic import ValidationError

from . import audiobooks
from .audiobook_contracts import (
    AudiobookChapterInput, AudiobookCreateResponse, CreateAudiobookFromDraftRequest,
    CreateAudiobookRequest, EbookChapterDraft, EbookDraft, EbookDraftSummary, EbookImportWarning, PatchEbookDraftRequest,
)
from .job_lifecycle import await_cleanup, kill_process_tree
from .video_process import WorkerOutputError, read_owned_output, spawn_owned

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
MAX_BOOK_CHARS = 2_000_000
MAX_DRAFTS = 100
MAX_WORKSPACE_BYTES = MAX_UPLOAD_BYTES + MAX_EXPANDED_BYTES
CONVERSION_TIMEOUT = 120.0
_ID = re.compile(r"^[0-9a-f]{32}$")
_DELETED_SOURCE = re.compile(r"^\.deleted-([0-9a-f]{32})-[0-9a-f]{32}$")
_LOG = logging.getLogger(__name__)
_TASKS: set[asyncio.Task[EbookDraft]] = set()
_SHUTTING_DOWN = False


class EbookImportError(Exception):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ExtractedEbook:
    title: str
    chapters: list[EbookChapterDraft]
    warnings: list[EbookImportWarning]
    author: str = ""


def converter_path() -> Path | None:
    """Only backend configuration and known installations select executables."""
    override = os.getenv("OPENFABRIC_EBOOK_CONVERT", "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    executable = shutil.which("ebook-convert")
    if executable:
        return Path(executable)
    candidates = [Path("/Applications/calibre.app/Contents/MacOS/ebook-convert")]
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.getenv(variable)
            if root:
                candidates.append(Path(root) / "Calibre2" / "ebook-convert.exe")
    return next((path for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def validate_mobi(raw: bytes) -> None:
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise EbookImportError("ebook_too_large", 413)
    if len(raw) < 86 or raw[60:68] != b"BOOKMOBI":
        raise EbookImportError("invalid_mobi")
    record_count = struct.unpack_from(">H", raw, 76)[0]
    if record_count < 1 or 78 + record_count * 8 > len(raw):
        raise EbookImportError("invalid_mobi")
    first_record = struct.unpack_from(">I", raw, 78)[0]
    if first_record < 78 + record_count * 8 or first_record + 16 > len(raw):
        raise EbookImportError("invalid_mobi")
    if struct.unpack_from(">H", raw, first_record + 12)[0] != 0:
        raise EbookImportError("ebook_encrypted")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml(raw: bytes) -> ET.Element:
    # Reject entities/DTDs before the stdlib XML parser can expand them. Normal
    # EPUB XHTML may use a DOCTYPE, but no external resources are needed here.
    if (len(raw) > MAX_MEMBER_BYTES or b"\x00" in raw
            or re.search(br"<!\s*(DOCTYPE|ENTITY)", raw, re.IGNORECASE)):
        raise EbookImportError("unsafe_ebook")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EbookImportError("invalid_ebook") from exc
    pending = [(root, 0)]
    count = 0
    while pending:
        element, depth = pending.pop()
        count += 1
        if depth > 100 or count > 100_000:
            raise EbookImportError("ebook_too_large", 413)
        pending.extend((child, depth + 1) for child in element)
    return root


def _member_path(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query:
        raise EbookImportError("unsafe_ebook")
    value = unquote(parsed.path)
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise EbookImportError("unsafe_ebook")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), value))
    if path in (".", "..") or path.startswith("../"):
        raise EbookImportError("unsafe_ebook")
    return path


class _TextReader(HTMLParser):
    """Emit plain paragraph blocks and section markers without executing HTML."""
    def __init__(self, anchors: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors = anchors
        self.blocks: list[tuple[bool, str]] = []
        self.parts: list[str] = []
        self.heading = False
        self.ignored: list[str] = []
        self.non_narrative = False

    def _flush(self) -> None:
        value = re.sub(r"\s+", " ", "".join(self.parts)).strip()
        if value:
            self.blocks.append((self.heading, value))
        self.parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.ignored:
            self.ignored.append(tag)
            return
        attributes = dict(attrs)
        if tag == "head":
            # Page metadata (especially <title>) is not body prose or a
            # chapter boundary. Converted XHTML may retain this metadata.
            self.ignored.append(tag)
            return
        if tag in {"script", "style", "svg", "nav", "noscript"} or attributes.get("hidden") is not None:
            self.ignored.append(tag)
            self.non_narrative = True
            return
        anchor = attributes.get("id") or attributes.get("name")
        if anchor and anchor in self.anchors:
            self._flush()
            self.blocks.append((True, self.anchors[anchor]))
        if tag in {"h1", "h2"}:
            self._flush()
            self.heading = True
        elif tag in {"p", "div", "li", "blockquote", "pre", "br", "tr"}:
            self._flush()
        elif tag in {"img", "audio", "video", "table"}:
            self.non_narrative = True

    def handle_endtag(self, tag: str) -> None:
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag in {"h1", "h2"}:
            self._flush()
            self.heading = False
        elif tag in {"p", "div", "li", "blockquote", "pre", "tr"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.parts.append(data)

    def finish(self) -> None:
        self.close()
        self._flush()


def split_chapter(title: str, text: str) -> list[EbookChapterDraft]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > audiobooks.MAX_CHAPTER_CHARS:
        window = remaining[:audiobooks.MAX_CHAPTER_CHARS]
        boundary = window.rfind("\n\n")
        if boundary < len(window) // 2:
            boundary = window.rfind(" ")
        if boundary < len(window) // 2:
            boundary = len(window)
        else:
            boundary += 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    if remaining:
        chunks.append(remaining)
    if len(title) > 180:
        raise EbookImportError("ebook_title_too_long")
    return [EbookChapterDraft(title=title if len(chunks) == 1 else f"{title} (part {index + 1})", text=chunk)
            for index, chunk in enumerate(chunks)]


def extract_epub(raw: bytes) -> ExtractedEbook:
    warnings: list[EbookImportWarning] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_MEMBERS or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise EbookImportError("ebook_too_large", 413)
            names: set[str] = set()
            for entry in entries:
                name = entry.filename
                if (name in names or "\\" in name or name.startswith("/") or ".." in name.split("/")
                        or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1):
                    raise EbookImportError("unsafe_ebook")
                names.add(name)

            def read(name: str) -> bytes:
                info = archive.getinfo(name)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise EbookImportError("ebook_too_large", 413)
                with archive.open(info) as handle:
                    value = handle.read(MAX_MEMBER_BYTES + 1)
                if len(value) > MAX_MEMBER_BYTES:
                    raise EbookImportError("ebook_too_large", 413)
                return value

            container = _xml(read("META-INF/container.xml"))
            rootfile = next((element.get("full-path") for element in container.iter() if _local(element.tag) == "rootfile"), None)
            if not rootfile:
                raise EbookImportError("invalid_ebook")
            package_path = _member_path("container", rootfile)
            package = _xml(read(package_path))
            title = next(("".join(element.itertext()).strip() for element in package.iter() if _local(element.tag) == "title"), "Imported book") or "Imported book"
            if len(title) > 200:
                raise EbookImportError("ebook_title_too_long")
            author = next((" ".join("".join(element.itertext()).split()) for element in package.iter() if _local(element.tag) == "creator"), "")
            author = author[:200]
            manifest: dict[str, ET.Element] = {element.get("id", ""): element for element in package.iter() if _local(element.tag) == "item"}
            toc: dict[str, dict[str, str]] = {}
            for item in manifest.values():
                media = item.get("media-type")
                if "nav" in item.get("properties", "").split() or media == "application/x-dtbncx+xml":
                    toc_path = _member_path(package_path, item.get("href", ""))
                    toc_root = _xml(read(toc_path))
                    for element in toc_root.iter():
                        if _local(element.tag) == "a" and element.get("href"):
                            href = element.get("href", "")
                            label = "".join(element.itertext()).strip()
                            if label:
                                toc.setdefault(_member_path(toc_path, href), {})[unquote(urlsplit(href).fragment)] = label
                        elif _local(element.tag) == "navpoint":
                            content_node = next((node for node in element if _local(node.tag) == "content"), None)
                            label_node = next((node for node in element if _local(node.tag) == "navlabel"), None)
                            if content_node is not None and label_node is not None:
                                href = content_node.get("src", "")
                                toc.setdefault(_member_path(toc_path, href), {})[unquote(urlsplit(href).fragment)] = "".join(label_node.itertext()).strip()
            blocks: list[tuple[bool, str]] = []
            seen: set[str] = set()
            non_narrative = False
            nonlinear = False
            for element in package.iter():
                if _local(element.tag) != "itemref":
                    continue
                spine_item = manifest.get(element.get("idref", ""))
                if spine_item is None or spine_item.get("media-type") != "application/xhtml+xml":
                    raise EbookImportError("unsupported_ebook_content")
                path = _member_path(package_path, spine_item.get("href", ""))
                if path in seen:
                    raise EbookImportError("invalid_ebook")
                seen.add(path)
                nonlinear = nonlinear or element.get("linear") == "no"
                content = read(path)
                _xml(content)
                reader = _TextReader(toc.get(path, {}))
                reader.feed(content.decode("utf-8-sig"))
                reader.finish()
                if "" in toc.get(path, {}):
                    blocks.append((True, toc[path][""]))
                blocks.extend(reader.blocks)
                non_narrative = non_narrative or reader.non_narrative
            chapters: list[EbookChapterDraft] = []
            chapter_title = "Chapter 1"
            paragraphs: list[str] = []
            for heading, value in blocks:
                if heading:
                    if paragraphs:
                        chapters.extend(split_chapter(chapter_title, "\n\n".join(paragraphs)))
                        paragraphs = []
                    chapter_title = value
                else:
                    paragraphs.append(value)
            if paragraphs:
                chapters.extend(split_chapter(chapter_title, "\n\n".join(paragraphs)))
            if not chapters:
                raise EbookImportError("ebook_text_empty")
            if len(chapters) > audiobooks.MAX_CHAPTERS or sum(len(chapter.text) for chapter in chapters) > MAX_BOOK_CHARS:
                raise EbookImportError("ebook_too_large", 413)
            warnings.append(EbookImportWarning(code="chapter_detection", message="Chapter boundaries are inferred from navigation and headings. Review the text and chapter selection before narration."))
            if any("(part " in chapter.title for chapter in chapters):
                warnings.append(EbookImportWarning(code="chapter_split", message="Long chapters were split into parts to fit narration limits; no text was truncated."))
            if non_narrative:
                warnings.append(EbookImportWarning(code="non_narrative_content", message="Images, scripts and navigation are not narrated. Tables retain their text, but complex layouts may need editing."))
            if nonlinear:
                warnings.append(EbookImportWarning(code="nonlinear_content", message="The source includes supplementary content. It is included for review; deselect material you do not want narrated."))
            return ExtractedEbook(title, chapters, warnings, author)
    except EbookImportError:
        raise
    except (OSError, UnicodeError, KeyError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise EbookImportError("invalid_ebook") from exc


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS ebook_drafts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")


def _draft_dir(identifier: str) -> Path:
    if not _ID.fullmatch(identifier):
        raise EbookImportError("ebook_draft_not_found", 404)
    root = audiobooks.books_root().resolve()
    path = root / "imports" / identifier
    if (root / "imports").is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise EbookImportError("ebook_storage_unavailable", 503)
    return path


_HEADING = re.compile(r"^(?:#{1,6}\s+\S.*|(?:chapter|part|book|prologue|epilogue)\b.*)$", re.IGNORECASE)


def chapters_from_plain_text(raw: str, book_title: str) -> ExtractedEbook:
    """Split pasted or .txt prose on markdown headings and chapter lines."""
    title = " ".join(book_title.split()) or "Imported text"
    if len(title) > 200:
        raise EbookImportError("ebook_title_too_long")
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise EbookImportError("ebook_text_empty")
    if len(text) > MAX_BOOK_CHARS:
        raise EbookImportError("ebook_too_large", 413)
    chapters: list[EbookChapterDraft] = []
    chapter_title = "Chapter 1"
    paragraphs: list[str] = []
    saw_heading = False

    def flush() -> None:
        body = "\n".join(paragraphs).strip()
        if body:
            chapters.extend(split_chapter(chapter_title, body))

    for line in text.split("\n"):
        stripped = line.strip().lstrip("\ufeff")
        if stripped and _HEADING.match(stripped):
            if paragraphs or saw_heading:
                flush()
                paragraphs = []
            chapter_title = stripped.lstrip("#").strip()[:180] or chapter_title
            saw_heading = True
            continue
        paragraphs.append(line)
    flush()
    warnings: list[EbookImportWarning] = []
    if not chapters:
        chapters = split_chapter("Chapter 1", text)
        warnings.append(EbookImportWarning(code="chapter_detection", message="No chapter headings were found, so this text is one chapter. Lines such as 'Chapter 1' or markdown headings start a new chapter."))
    else:
        warnings.append(EbookImportWarning(code="chapter_detection", message="Chapter boundaries were inferred from headings. Review them before narration."))
    if any("(part " in chapter.title for chapter in chapters):
        warnings.append(EbookImportWarning(code="chapter_split", message="Long chapters were split into parts to fit narration limits; no text was truncated."))
    if len(chapters) > audiobooks.MAX_CHAPTERS:
        raise EbookImportError("ebook_too_large", 413)
    return ExtractedEbook(title, chapters, warnings)


def extract_uploaded(filename: str, source: bytes) -> ExtractedEbook:
    suffix = Path(filename).suffix.lower()
    if suffix == ".epub":
        return extract_epub(source)
    if suffix != ".txt":
        raise EbookImportError("unsupported_ebook_format")
    if not source or len(source) > MAX_UPLOAD_BYTES or b"\x00" in source:
        raise EbookImportError("ebook_too_large" if source and len(source) > MAX_UPLOAD_BYTES else "invalid_text", 413 if source and len(source) > MAX_UPLOAD_BYTES else 400)
    try:
        text = source.decode("utf-8-sig")
    except UnicodeError as exc:
        raise EbookImportError("invalid_text") from exc
    stem = Path(filename).stem.replace("_", " ").strip() or "Imported text"
    return chapters_from_plain_text(text, stem)


def save_draft(filename: str, source: bytes, extracted: ExtractedEbook) -> EbookDraft:
    safe_name = Path(filename.replace("\\", "/")).name
    if not safe_name or len(safe_name) > 240:
        raise EbookImportError("invalid_ebook_filename")
    identifier = uuid.uuid4().hex
    stamp = audiobooks._now()
    draft = EbookDraft(id=identifier, title=extracted.title, chapters=extracted.chapters, source_filename=safe_name,
                      source_sha256=hashlib.sha256(source).hexdigest(), warnings=extracted.warnings, revision=1,
                      author=extracted.author, created_at=stamp, updated_at=stamp)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT COUNT(*) FROM ebook_drafts").fetchone()[0] >= MAX_DRAFTS:
            raise EbookImportError("ebook_draft_limit", 409)
        directory = _draft_dir(identifier)
        directory.mkdir(parents=True)
        try:
            temporary = directory / ".source.tmp"
            temporary.write_bytes(source)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            temporary.replace(directory / "source.mobi")
            _ensure_schema(connection)
            connection.execute("INSERT INTO ebook_drafts VALUES (?, ?)", (draft.id, draft.model_dump_json()))
            connection.commit()
        except BaseException:
            shutil.rmtree(directory)
            raise
    return draft


def get_draft(identifier: str) -> EbookDraft:
    _draft_dir(identifier)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        _ensure_schema(connection)
        row = connection.execute("SELECT payload FROM ebook_drafts WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise EbookImportError("ebook_draft_not_found", 404)
        try:
            return EbookDraft.model_validate_json(str(row[0]))
        except ValidationError as exc:
            raise EbookImportError("ebook_storage_unavailable", 503) from exc


def list_drafts() -> list[EbookDraftSummary]:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        _ensure_schema(connection)
        rows = connection.execute("""SELECT id, json_extract(payload, '$.title') AS title,
            json_extract(payload, '$.source_filename') AS source_filename,
            json_array_length(payload, '$.chapters') AS chapter_count,
            json_extract(payload, '$.revision') AS revision,
            json_extract(payload, '$.created_at') AS created_at,
            json_extract(payload, '$.updated_at') AS updated_at
            FROM ebook_drafts ORDER BY rowid DESC""").fetchall()
        try:
            return [EbookDraftSummary.model_validate(dict(row)) for row in rows]
        except ValidationError as exc:
            raise EbookImportError("ebook_storage_unavailable", 503) from exc


def patch_draft(identifier: str, body: PatchEbookDraftRequest) -> EbookDraft:
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        _ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        current = get_draft(identifier)
        if current.revision != body.revision:
            raise EbookImportError("ebook_draft_conflict", 409)
        changes: dict[str, object] = {"title": body.title.strip(), "chapters": body.chapters,
                                      "revision": current.revision + 1, "updated_at": audiobooks._now()}
        if "author" in body.model_fields_set and body.author is not None:
            changes["author"] = body.author.strip()
        if "pronunciations" in body.model_fields_set and body.pronunciations is not None:
            from .audiobook_pronounce import PronunciationError, ensure_unique
            try:
                ensure_unique(body.pronunciations)
            except PronunciationError as exc:
                raise EbookImportError(exc.code) from exc
            changes["pronunciations"] = body.pronunciations
        updated = current.model_copy(update=changes)
        connection.execute("UPDATE ebook_drafts SET payload = ? WHERE id = ?", (updated.model_dump_json(), identifier))
        connection.commit()
        return updated


def source_path(identifier: str) -> Path:
    get_draft(identifier)
    path = _draft_dir(identifier) / "source.mobi"
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(_draft_dir(identifier).resolve()):
        raise EbookImportError("ebook_source_unavailable", 404)
    return path


def delete_draft(identifier: str) -> None:
    """Explicit deletion only; drafts supplying a saved book retain provenance."""
    directory = _draft_dir(identifier)
    with audiobooks._LOCK, closing(audiobooks._connect()) as connection:
        audiobooks._ensure_schema(connection)
        get_draft(identifier)
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM audiobook_books WHERE source_import_id = ? LIMIT 1", (identifier,)).fetchone():
            raise EbookImportError("ebook_draft_in_use", 409)
        staging = directory.with_name(f".deleted-{identifier}-{uuid.uuid4().hex}")
        try:
            directory.rename(staging)
            connection.execute("DELETE FROM ebook_drafts WHERE id = ?", (identifier,))
            connection.commit()
        except BaseException:
            connection.rollback()
            if staging.is_dir():
                staging.rename(directory)
            raise
    try:
        shutil.rmtree(staging)
    except OSError as exc:
        _LOG.exception("Deleted ebook draft cleanup failed")
        raise EbookImportError("ebook_storage_unavailable", 503) from exc


def _recover_deleted_sources() -> None:
    """Reconcile interrupted file deletion against the committed draft row.

    A source staged before the transaction commits still belongs to its draft.
    A stage without a row is the remainder of an explicitly committed deletion.
    Unknown names and ambiguous copies are never selected for destruction.
    """
    try:
        with audiobooks._LOCK:
            root = audiobooks.books_root().resolve()
            imports = root / "imports"
            if imports.is_symlink() or (imports.exists() and not imports.is_dir()):
                raise EbookImportError("ebook_storage_unavailable", 503)
            if not imports.exists():
                return
            pending: dict[str, list[Path]] = {}
            for path in imports.iterdir():
                match = _DELETED_SOURCE.fullmatch(path.name)
                if match is None:
                    continue
                if path.is_symlink() or not path.is_dir() or path.resolve().parent != imports.resolve():
                    raise EbookImportError("ebook_storage_unavailable", 503)
                pending.setdefault(match[1], []).append(path)
            if not pending:
                return
            if not audiobooks._db_path().is_file():
                raise EbookImportError("ebook_storage_unavailable", 503)
            with closing(audiobooks._connect()) as connection:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ebook_drafts'").fetchone() is None:
                    raise EbookImportError("ebook_storage_unavailable", 503)
                connection.execute("BEGIN IMMEDIATE")
                restorations: list[tuple[Path, Path]] = []
                removals: list[Path] = []
                # Validate every candidate before changing any source. A
                # recreated destination or duplicate stage needs human review.
                for identifier, paths in pending.items():
                    target = _draft_dir(identifier)
                    if len(paths) != 1 or target.exists() or target.is_symlink():
                        raise EbookImportError("ebook_storage_unavailable", 503)
                    row = connection.execute("SELECT 1 FROM ebook_drafts WHERE id = ?", (identifier,)).fetchone()
                    if row is None:
                        removals.append(paths[0])
                    else:
                        restorations.append((paths[0], target))
                for staged, target in restorations:
                    staged.rename(target)
                for staged in removals:
                    shutil.rmtree(staged)
                connection.commit()
    except (OSError, sqlite3.Error) as exc:
        _LOG.exception("Interrupted ebook source deletion recovery failed")
        raise EbookImportError("ebook_storage_unavailable", 503) from exc


def create_from_draft(identifier: str, body: CreateAudiobookFromDraftRequest) -> AudiobookCreateResponse:
    with audiobooks._LOCK:
        draft = get_draft(identifier)
        if draft.revision != body.revision:
            raise EbookImportError("ebook_draft_conflict", 409)
        chapters = [AudiobookChapterInput(title=chapter.title, text=chapter.text) for chapter in draft.chapters if chapter.included]
        if not chapters:
            raise EbookImportError("ebook_chapters_required")
        return audiobooks.create_book(CreateAudiobookRequest(
            title=draft.title, profile_id=body.profile_id, chapters=chapters,
            author=draft.author, pronunciations=draft.pronunciations),
            source_import_id=identifier, source_import_revision=draft.revision)


async def _convert(filename: str, source: bytes) -> EbookDraft:
    validate_mobi(source)
    converter = converter_path()
    if converter is None:
        raise EbookImportError("ebook_converter_missing", 503)
    with tempfile.TemporaryDirectory(prefix="openfabric-ebook-") as temporary:
        workspace = Path(temporary)
        input_path, output_path = workspace / "source.mobi", workspace / "converted.epub"
        input_path.write_bytes(source)
        environment = dict(os.environ)
        # Calibre's conversion workspace also includes its config/cache/temp
        # writes. Keep all three within the directory monitored below.
        for variable, folder in (("CALIBRE_CONFIG_DIRECTORY", "config"),
                                 ("CALIBRE_TEMP_DIR", "temp"),
                                 ("CALIBRE_CACHE_DIRECTORY", "cache")):
            directory = workspace / folder
            directory.mkdir()
            environment[variable] = str(directory)
        process: asyncio.subprocess.Process | None = None
        try:
            process = await spawn_owned([sys.executable, str(Path(__file__).with_name("ebook_import_worker.py")),
                                        str(MAX_UPLOAD_BYTES), str(converter), str(input_path), str(output_path)],
                                       receipt_path=workspace / "worker.json", cwd=workspace, env=environment,
                                       stdout=asyncio.subprocess.PIPE)
            capture = asyncio.create_task(read_owned_output(process, max_bytes=65536, timeout=CONVERSION_TIMEOUT))
            monitor = asyncio.create_task(_watch_workspace(workspace))
            try:
                completed, _ = await asyncio.wait((capture, monitor), return_when=asyncio.FIRST_COMPLETED)
                if monitor in completed:
                    await monitor
                output = await capture
            finally:
                for task in (capture, monitor):
                    if not task.done():
                        task.cancel()
                await await_cleanup(asyncio.gather(capture, monitor, return_exceptions=True))
            if process.returncode != 0 or not output_path.is_file():
                code = "ebook_encrypted" if b"drm" in output.lower() else "ebook_conversion_failed"
                raise EbookImportError(code)
            if output_path.is_symlink() or output_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise EbookImportError("ebook_too_large", 413)
            extracted = await await_cleanup(asyncio.to_thread(extract_epub, output_path.read_bytes()))
            return save_draft(filename, source, extracted)
        except (TimeoutError, WorkerOutputError) as exc:
            raise EbookImportError("ebook_conversion_timeout") from exc
        except OSError as exc:
            _LOG.exception("Ebook converter launch failed")
            raise EbookImportError("ebook_conversion_failed") from exc
        finally:
            if process is not None:
                await await_cleanup(kill_process_tree(process))


async def _watch_workspace(workspace: Path) -> None:
    while True:
        total, count = 0, 0
        for directory, directories, filenames in os.walk(workspace, followlinks=False):
            for name in (*directories, *filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    raise EbookImportError("unsafe_ebook")
                count += 1
                if count > MAX_ARCHIVE_MEMBERS * 4:
                    raise EbookImportError("ebook_too_large", 413)
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    continue
                if total > MAX_WORKSPACE_BYTES:
                    raise EbookImportError("ebook_too_large", 413)
        await asyncio.sleep(0.02)


def import_pasted(title: str, text: str, author: str = "") -> EbookDraft:
    if _SHUTTING_DOWN or len(_TASKS) >= 2:
        raise EbookImportError("ebook_import_busy", 409)
    extracted = chapters_from_plain_text(text, title)
    if author.strip():
        extracted = ExtractedEbook(extracted.title, extracted.chapters, extracted.warnings, author.strip()[:200])
    return save_draft("pasted.txt", text.encode("utf-8"), extracted)


async def import_document(filename: str, source: bytes) -> EbookDraft:
    suffix = Path(filename).suffix.lower()
    if suffix == ".mobi":
        return await import_mobi(filename, source)
    if suffix not in {".epub", ".txt"}:
        raise EbookImportError("unsupported_ebook_format")
    if _SHUTTING_DOWN or len(_TASKS) >= 2:
        raise EbookImportError("ebook_import_busy", 409)

    async def _run() -> EbookDraft:
        extracted = await asyncio.to_thread(extract_uploaded, filename, source)
        if _SHUTTING_DOWN:
            raise EbookImportError("ebook_import_cancelled", 499)
        return save_draft(filename, source, extracted)

    task = asyncio.create_task(_run(), name="ebook-import")
    _TASKS.add(task)
    try:
        return await task
    finally:
        _TASKS.discard(task)


async def import_mobi(filename: str, source: bytes) -> EbookDraft:
    if _SHUTTING_DOWN or len(_TASKS) >= 2:
        raise EbookImportError("ebook_import_busy", 409)
    task = asyncio.create_task(_convert(filename, source), name="ebook-import")
    _TASKS.add(task)
    try:
        return await task
    finally:
        _TASKS.discard(task)


def start() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    _recover_deleted_sources()
    _SHUTTING_DOWN = False


async def shutdown() -> None:
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    tasks = tuple(_TASKS)
    for task in tasks:
        task.cancel()
    await await_cleanup(asyncio.gather(*tasks, return_exceptions=True))
