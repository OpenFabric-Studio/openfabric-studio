"""The folder that holds OpenFabric Studio's own files.

Songs, voices, videos, singing models, logs, and the catalog database live
here. The choice is stored outside that folder, in
~/.openfabric-studio/config.json, so moving it does not lose the pointer. ACE-Step's trainer
only accepts files inside its own checkout, so LoRA datasets and ACE weights
stay there.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias
from uuid import uuid4

logger = logging.getLogger(__name__)

LIBRARY_FILES = ("aicollector.db", "aicollector.db-wal", "aicollector.db-shm")
LIBRARY_DIRS = ("files", "voices", "videos", "models", "logs", "audiobooks", "voice-profiles", "speech-clone-trials")
_PATH_COLUMNS = ("audio_path", "abc_path", "captured_source_path")
_METADATA_ROOTS = (Path('voices'), Path('files') / '_exports', Path('audiobooks'), Path('voice-profiles'))
_DATABASES = (Path('aicollector.db'), Path('voice-profiles') / 'profiles.db', Path('audiobooks') / 'audiobooks.db')
_JSON_COLUMNS = ("stems_json", "midi_json")
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
_CONFIG_LOCK = threading.RLock()
_MIGRATION_LOCK = threading.RLock()
_JOURNAL = ".openfabric-migration.json"
_SOURCE_JOURNAL = ".openfabric-migration-source.json"
_BACKUPS = ".openfabric-migration"
_LAYOUT = (
    ("files", "tracks"),
    ("voices", "voices"),
    ("videos", "videos"),
    ("audiobooks", "audiobooks"),
    ("voice-profiles", "speechProfiles"),
    ("speech-clone-trials", "speechTrials"),
    ("models/seed-vc", "models"),
    ("logs", "logs"),
    ("aicollector.db", "database"),
)


class DataDirError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def config_path() -> Path:
    override = _env_first("OPENFABRIC_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".openfabric-studio" / "config.json"


def read_config() -> dict[str, JsonValue]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        data = _json_value(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, DataDirError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_text(path: Path, text: str) -> None:
    """Replace a small metadata file without leaving a truncated live file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_config(data: dict[str, JsonValue]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, json.dumps(data, indent=2))


def configured_data_dir() -> Path | None:
    raw = read_config().get("data_dir")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def has_library(path: Path) -> bool:
    return any((path / database).is_file() for database in _DATABASES) or any(
        (path / folder).is_dir() for folder in ('files', 'voices', 'audiobooks', 'voice-profiles', 'speech-clone-trials')
    )


def _occupied(path: Path) -> bool:
    return any((path / name).exists() or (path / name).is_symlink() for name in (*LIBRARY_DIRS, *LIBRARY_FILES))


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return path.resolve() != parent.resolve()


def _database_path(root: Path, relative: Path) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise DataDirError('invalid_metadata')
    return path


def validate_data_dir(raw: str, blocked: list[Path]) -> Path:
    text = (raw or "").strip()
    if not text:
        raise DataDirError("absolute")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise DataDirError("absolute")
    if path.exists() and not path.is_dir():
        raise DataDirError("not_a_folder")
    # Reject an engine path before creating any folders along it.
    resolved = path.resolve()
    for parent in blocked:
        try:
            parent_resolved = parent.resolve()
        except OSError:
            continue
        if resolved == parent_resolved or _is_inside(path, parent):
            raise DataDirError("inside_engine")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".openfabric-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataDirError("not_writable") from exc
    return path


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DataDirError("invalid_metadata")
            result[key] = _json_value(item)
        return result
    raise DataDirError("invalid_metadata")


def _rewrite_path(value: str, src: Path, dest: Path) -> str:
    # Decode JSON before reaching this boundary. Windows JSON stores doubled
    # backslashes, and substring replacement also corrupts sibling root names.
    variants = (
        (str(src), str(dest.resolve())),
        (str(src.resolve()), str(dest.resolve())),
        (src.as_posix(), dest.resolve().as_posix()),
        (src.resolve().as_posix(), dest.resolve().as_posix()),
    )
    for old, new in variants:
        if value == old:
            return new
        if value.startswith(old + "/") or value.startswith(old + "\\"):
            return new + value[len(old):]
    return value


def _rewrite_json(value: JsonValue, src: Path, dest: Path) -> JsonValue:
    if isinstance(value, str):
        return _rewrite_path(value, src, dest)
    if isinstance(value, list):
        return [_rewrite_json(item, src, dest) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_json(item, src, dest) for key, item in value.items()}
    return value


def rewrite_library_paths(db_path: Path, src: Path, dest: Path) -> None:
    if not db_path.is_file() or src.resolve() == dest.resolve():
        return
    with closing(sqlite3.connect(db_path)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, candidates in (("tracks", (*_PATH_COLUMNS, *_JSON_COLUMNS)), ("projects", ("data_json",)),
                                  ('audio_versions', ('audio_path', 'captured_source_path')),
                                  ('voice_profiles', ('reference_audio_path',)),
                                  ('audiobook_books', ('export_path', 'mp3_export_path', 'm4b_export_path', 'cover_path')),
                                  ('audiobook_jobs', ('output_path',)),
                                  ('audiobook_sections', ('output_path', 'snapshot_json')),
                                  ('audiobook_workflows', ('payload',))):
            if table not in tables:
                continue
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column in candidates:
                if column not in columns:
                    continue
                # All identifiers come from the fixed lists above.
                for rowid, value in connection.execute(f"SELECT rowid, {column} FROM {table}").fetchall():
                    if not isinstance(value, str) or not value:
                        continue
                    if column not in (*_JSON_COLUMNS, 'data_json', 'snapshot_json', 'payload'):
                        rewritten = _rewrite_path(value, src, dest)
                    else:
                        try:
                            parsed = _json_value(json.loads(value))
                        except json.JSONDecodeError as exc:
                            raise DataDirError("invalid_metadata") from exc
                        changed = _rewrite_json(parsed, src, dest)
                        rewritten = json.dumps(changed, ensure_ascii=False) if changed != parsed else value
                    if rewritten != value:
                        connection.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (rewritten, rowid))


_TEXT_SUFFIXES = {".json", ".yml", ".yaml", ".txt"}
_YAML_SCALAR = re.compile(
    r"^(?P<prefix>\s*(?:[\w.-]+:\s*|-\s+))"
    r"(?P<value>\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'|[^#\r\n]+?)"
    r"(?P<suffix>[ \t]*(?:#.*)?)(?P<newline>\r?\n)?$"
)


def _rewrite_yaml_paths(text: str, src: Path, dest: Path) -> str:
    """Rewrite path scalars in the generated engine config, retaining comments."""
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        match = _YAML_SCALAR.fullmatch(line)
        if match is None:
            lines.append(line)
            continue
        scalar = match["value"]
        if scalar.startswith('"'):
            try:
                value: object = json.loads(scalar)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if not isinstance(value, str):
                lines.append(line)
                continue
            changed = _rewrite_path(value, src, dest)
            replacement = json.dumps(changed, ensure_ascii=False)
        elif scalar.startswith("'"):
            value = scalar[1:-1].replace("''", "'")
            changed = _rewrite_path(value, src, dest)
            replacement = "'" + changed.replace("'", "''") + "'"
        else:
            value = scalar
            changed = _rewrite_path(value, src, dest)
            replacement = changed
        lines.append(
            match["prefix"] + replacement + match["suffix"] + (match["newline"] or "")
            if changed != value else line
        )
    return "".join(lines)


def _metadata_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        if path.stat().st_size <= 5_000_000:
            result.append(path)
    return result


def rewrite_text_prefixes(root: Path, src: Path, dest: Path) -> None:
    """Rewrite structured job metadata, without changing incidental prose."""
    for path in _metadata_files(root):
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            try:
                parsed = _json_value(json.loads(text))
            except json.JSONDecodeError as exc:
                raise DataDirError("invalid_metadata") from exc
            changed = _rewrite_json(parsed, src, dest)
            rewritten = json.dumps(changed, indent=2, ensure_ascii=False) if changed != parsed else text
        elif path.suffix.lower() in {".yml", ".yaml"}:
            rewritten = _rewrite_yaml_paths(text, src, dest)
        else:
            rewritten = "".join(_rewrite_path(line.rstrip("\r\n"), src, dest) + line[len(line.rstrip("\r\n")):]
                                for line in text.splitlines(keepends=True))
        if rewritten != text:
            _atomic_text(path, rewritten)


@dataclass(frozen=True)
class _Migration:
    src: Path
    dest: Path
    names: tuple[str, ...]
    phase: Literal["preparing", "moving", "complete"]
    token: str


def _write_journal(migration: _Migration) -> None:
    _atomic_text(migration.dest / _JOURNAL, json.dumps({
        "source": str(migration.src), "destination": str(migration.dest),
        "names": migration.names, "phase": migration.phase, "token": migration.token,
    }))


def _source_migration(src: Path) -> tuple[Path, str] | None:
    path = src / _SOURCE_JOURNAL
    if not path.exists():
        return None
    try:
        data = _json_value(json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(data, dict):
            raise DataDirError("migration_incomplete")
        destination, token = data.get("destination"), data.get("token")
        if not isinstance(destination, str) or not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise DataDirError("migration_incomplete")
        dest = Path(destination)
        if not dest.is_absolute() or dest.resolve() == src.resolve() or _is_inside(dest, src) or _is_inside(src, dest):
            raise DataDirError("migration_incomplete")
        return dest, token
    except (ValueError, json.JSONDecodeError) as exc:
        raise DataDirError("migration_incomplete") from exc


def _read_journal(dest: Path, expected_source: Path) -> _Migration | None:
    path = dest / _JOURNAL
    if not path.exists():
        return None
    try:
        value = _json_value(json.loads(path.read_text(encoding="utf-8")))
        if not isinstance(value, dict):
            raise DataDirError("migration_incomplete")
        source, target, names, phase, token = (value.get(key) for key in ("source", "destination", "names", "phase", "token"))
        if not isinstance(source, str) or not isinstance(target, str) or not isinstance(names, list):
            raise DataDirError("migration_incomplete")
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise DataDirError("migration_incomplete")
        src = Path(source)
        if not src.is_absolute() or Path(target).resolve() != dest.resolve() or src.resolve() == dest.resolve():
            raise DataDirError("migration_incomplete")
        if _is_inside(src, dest) or _is_inside(dest, src):
            raise DataDirError("migration_incomplete")
        if expected_source.resolve() not in {src.resolve(), dest.resolve()}:
            raise DataDirError("migration_incomplete")
        checked: list[str] = []
        for name in names:
            if not isinstance(name, str) or name not in (*LIBRARY_DIRS, *LIBRARY_FILES) or name in checked:
                raise DataDirError("migration_incomplete")
            checked.append(name)
        if phase == "preparing":
            checked_phase: Literal["preparing", "moving", "complete"] = "preparing"
        elif phase == "moving":
            checked_phase = "moving"
        elif phase == "complete":
            checked_phase = "complete"
        else:
            raise DataDirError("migration_incomplete")
        # A destination can be an imported folder. Its journal alone must
        # never authorize removing data from a claimed source directory.
        confirmation = _source_migration(src)
        if checked_phase != "preparing" and confirmation != (dest.resolve(), token):
            raise DataDirError("migration_incomplete")
        return _Migration(src, dest.resolve(), tuple(checked), checked_phase, token)
    except (ValueError, json.JSONDecodeError) as exc:
        raise DataDirError("migration_incomplete") from exc


def _remove_item(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _clear_migration(migration: _Migration) -> None:
    backups = migration.dest / _BACKUPS
    if backups.exists():
        shutil.rmtree(backups)
    (migration.dest / _JOURNAL).unlink(missing_ok=True)
    if _source_migration(migration.src) == (migration.dest, migration.token):
        (migration.src / _SOURCE_JOURNAL).unlink(missing_ok=True)


def _restore_metadata(migration: _Migration) -> None:
    backups = migration.dest / _BACKUPS
    for relative in _DATABASES:
        database = backups / relative
        if not database.is_file():
            continue
        # The backup includes committed WAL contents. Sidecars from the
        # rewritten database must not be replayed over that original catalog.
        for suffix in ("-wal", "-shm"):
            (migration.src / relative.parent / (relative.name + suffix)).unlink(missing_ok=True)
        target = migration.src / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name('.' + target.name + '.restore')
        shutil.copy2(database, temporary)
        temporary.replace(target)
    for metadata_root in _METADATA_ROOTS:
        for saved in _metadata_files(backups / metadata_root):
            target = migration.src / saved.relative_to(backups)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name("." + target.name + ".restore")
            shutil.copy2(saved, temporary)
            temporary.replace(target)


def _rollback_migration(migration: _Migration) -> None:
    migration.src.mkdir(parents=True, exist_ok=True)
    for name in reversed(migration.names):
        original, target = migration.src / name, migration.dest / name
        if not target.exists() and not target.is_symlink():
            continue
        if original.exists() or original.is_symlink():
            # Cross-volume copies retain their complete originals until the
            # commit marker. A partial destination copy is safe to discard.
            _remove_item(target)
        else:
            target.rename(original)
    _restore_metadata(migration)
    _clear_migration(migration)


def _finish_migration(migration: _Migration) -> None:
    # Cross-volume source cleanup may itself be interrupted. The complete
    # marker makes the destination authoritative before any source deletion.
    persistent = (name for name in migration.names if name not in {"aicollector.db-wal", "aicollector.db-shm"})
    if not all((migration.dest / name).exists() or (migration.dest / name).is_symlink() for name in persistent):
        raise DataDirError("migration_incomplete")
    for name in migration.names:
        _remove_item(migration.src / name)
    _clear_migration(migration)


def _recover_migration(dest: Path, expected_source: Path) -> None:
    migration = _read_journal(dest, expected_source)
    if migration is None:
        return
    if migration.phase == "preparing":
        # No live files have moved yet, and backups may be partial.
        _clear_migration(migration)
    elif migration.phase == "complete":
        try:
            _finish_migration(migration)
        except OSError:
            logger.exception("library moved; old copy cleanup will be retried")
    else:
        _rollback_migration(migration)


def _recover_source(src: Path) -> Path:
    """Find a pending move even when the requested destination changed."""
    pending = _source_migration(src)
    if pending is None:
        return src
    dest, _ = pending
    migration = _read_journal(dest, src)
    if migration is None:
        # Cleanup removes the destination marker before its source marker.
        (src / _SOURCE_JOURNAL).unlink(missing_ok=True)
        return dest if has_library(dest) and not has_library(src) else src
    _recover_migration(dest, src)
    return dest if migration.phase == "complete" else src


def _copy_library_file(src: str | Path, dest: str | Path, *, follow_symlinks: bool = True) -> None:
    try:
        shutil.copy2(src, dest, follow_symlinks=follow_symlinks)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            # Raising a domain error here prevents copytree from turning
            # ENOSPC into a collection of opaque error-message strings.
            raise DataDirError("insufficient_space") from exc
        raise


def migrate_library(src: Path, dest: Path) -> None:
    """Move without merging; journal moves and retain original path metadata."""
    with _MIGRATION_LOCK:
        src = _recover_source(src)
        if src.resolve() == dest.resolve():
            return
        if _is_inside(dest, src) or _is_inside(src, dest):
            raise DataDirError("nested")
        _recover_migration(dest, src)
        if has_library(dest):
            return
        if not has_library(src):
            dest.mkdir(parents=True, exist_ok=True)
            return
        if _occupied(dest):
            raise DataDirError("destination_not_empty")
        dest.mkdir(parents=True, exist_ok=True)
        backups = dest / _BACKUPS
        if backups.exists():
            raise DataDirError("migration_incomplete")
        names = tuple(name for name in (*LIBRARY_DIRS, *LIBRARY_FILES) if (src / name).exists() or (src / name).is_symlink())
        token = uuid4().hex
        preparing = _Migration(src.resolve(), dest.resolve(), names, "preparing", token)
        _write_journal(preparing)
        try:
            _atomic_text(src / _SOURCE_JOURNAL, json.dumps({"destination": str(dest.resolve()), "token": token}))
            backups.mkdir()
            for relative in _DATABASES:
                database = _database_path(src, relative)
                if not database.is_file():
                    continue
                target = backups / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(target)) as saved:
                    source.backup(saved)
            for metadata_root in _METADATA_ROOTS:
                for metadata in _metadata_files(src / metadata_root):
                    target = backups / metadata.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(metadata, target)
            # Closing SQLite may checkpoint and remove its sidecars.
            names = tuple(name for name in names if (src / name).exists() or (src / name).is_symlink())
            migration = _Migration(src.resolve(), dest.resolve(), names, "moving", token)
            _write_journal(migration)
        except Exception as exc:
            _clear_migration(preparing)
            if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                raise DataDirError("insufficient_space") from exc
            raise
        try:
            for name in names:
                item, target = src / name, dest / name
                try:
                    item.rename(target)
                except OSError as exc:
                    if exc.errno != errno.EXDEV:
                        raise
                    if item.is_dir() and not item.is_symlink():
                        shutil.copytree(item, target, symlinks=True, copy_function=_copy_library_file)
                    else:
                        _copy_library_file(item, target, follow_symlinks=False)
            for relative in _DATABASES:
                rewrite_library_paths(_database_path(dest, relative), src, dest)
            for metadata_root in _METADATA_ROOTS:
                rewrite_text_prefixes(dest / metadata_root, src, dest)
            completed = _Migration(migration.src, migration.dest, names, "complete", token)
            _write_journal(completed)
        except Exception as exc:
            _rollback_migration(migration)
            if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                raise DataDirError("insufficient_space") from exc
            raise
        try:
            _finish_migration(completed)
        except OSError:
            logger.exception("library moved; old copy cleanup will be retried")


def parse_chosen_folder(text: str) -> str:
    path = (text or "").strip()
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _saved_path(value: JsonValue) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else None


def resolve_data_dir(default: Path) -> Path:
    """Resolve the requested folder using the durable active-library pointer."""
    with _CONFIG_LOCK:
        data = read_config()
        source = _saved_path(data.get("active_data_dir")) or default
        chosen = _saved_path(data.get("data_dir")) or source
        try:
            chosen.mkdir(parents=True, exist_ok=True)
            with _MIGRATION_LOCK:
                source = _recover_source(source)
                # A partial move may already have files/a database in the new
                # folder. Recover it before treating it as an existing library.
                _recover_migration(chosen, source)
                if chosen.resolve() != source.resolve() and not has_library(chosen):
                    # Older config files have only data_dir. First startup can
                    # still move the original default; selecting again records
                    # the running active directory via save_data_dir.
                    migrate_library(source, chosen)
        except (OSError, DataDirError, sqlite3.Error, UnicodeError) as exc:
            code = exc.code if isinstance(exc, DataDirError) else "not_writable"
            logger.exception("could not move the library to %s", chosen)
            data["error"] = code
            data["active_data_dir"] = str(source)
            try:
                write_config(data)
            except OSError:
                logger.exception("could not record the library move error")
            return source
        data["active_data_dir"] = str(chosen)
        data["error"] = ""
        try:
            write_config(data)
        except OSError:
            logger.exception("could not record the active library folder")
        return chosen


def adopt_legacy_logs(legacy: Path, dest: Path) -> None:
    """Move logs that were stored beside the backend into the data folder."""
    try:
        if not legacy.is_dir() or legacy.resolve() == dest.resolve():
            return
    except OSError:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in list(legacy.iterdir()):
        target = dest / item.name
        if target.exists():
            continue
        try:
            shutil.move(str(item), str(target))
        except OSError:
            logger.exception("could not move log %s", item)
    try:
        legacy.rmdir()
    except OSError:
        pass


def ensure_layout(data_dir: Path, legacy_log_dir: Path | None = None) -> None:
    """Create the app folders, including the empty videos folder."""
    for name in LIBRARY_DIRS:
        try:
            (data_dir / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.exception("could not create %s", data_dir / name)
    if legacy_log_dir is not None:
        adopt_legacy_logs(legacy_log_dir, data_dir / "logs")


def library_status(active: Path) -> dict[str, JsonValue]:
    chosen = configured_data_dir()
    pending = ""
    restart = False
    if chosen is not None and chosen.resolve() != active.resolve():
        pending = str(chosen)
        restart = True
    return {
        "data_dir": str(active),
        "pending_data_dir": pending,
        "restart_required": restart,
        "error": str(read_config().get("error") or ""),
        "folders": [{"path": path, "key": key} for path, key in _LAYOUT],
    }


def save_data_dir(path: Path, active: Path | None = None) -> None:
    with _CONFIG_LOCK:
        data = read_config()
        if active is not None:
            data["active_data_dir"] = str(active)
        data["data_dir"] = str(path)
        data["error"] = ""
        write_config(data)


def _link_checkpoints(legacy: Path, dest: Path) -> None:
    """Make the engine's checkpoints path follow the library copy.

    A real directory is left alone. Replacing one would hide downloaded weights.
    A stale link, including one left behind after the library itself moved, is
    pointed at the library copy.
    """
    if legacy.is_symlink():
        try:
            if legacy.resolve() == dest.resolve():
                return
        except OSError:
            pass
        try:
            legacy.unlink()
        except OSError:
            logger.exception("could not replace the singing-model link")
            return
    elif legacy.exists():
        return
    try:
        legacy.symlink_to(dest, target_is_directory=True)
    except OSError:
        logger.exception("could not link the singing-model folder")


def place_seed_models(data_dir: Path, seed_vc_dir: Path) -> Path:
    """Keep singing-model downloads in the library, and leave a link the engine already uses."""
    dest = data_dir / "models" / "seed-vc"
    legacy = seed_vc_dir / "checkpoints"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("could not create the singing-model folder")
        return dest
    if not legacy.is_symlink() and legacy.is_dir() and not dest.exists():
        try:
            shutil.move(str(legacy), str(dest))
        except OSError:
            logger.exception("could not move singing models into the library")
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("could not create the singing-model folder")
        return dest
    # Optional engines are absent on a fresh minimal installation. Keep the
    # library ready, but do not create a checkout or log an expected absence.
    if seed_vc_dir.is_dir():
        _link_checkpoints(legacy, dest)
    return dest
