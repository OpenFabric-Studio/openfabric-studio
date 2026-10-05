"""Cloud TTS adapter; local renderer and provider billing ownership stay separate."""
from __future__ import annotations

from .voice_profile_contracts import CloudSpeechConfiguration
from .voice_profiles import VoiceProfileError


def validate_configuration(configuration: CloudSpeechConfiguration) -> None:
    from . import openrouter_catalog
    from .openrouter_errors import OpenRouterError
    try:
        model = openrouter_catalog.require_model(configuration.model, "speech")
        if configuration.clone_reference:
            if not configuration.reference_transfer_confirmed:
                raise VoiceProfileError("cloud_reference_permission_required", 403)
            if not model.supports_voice_cloning:
                raise VoiceProfileError("cloud_voice_cloning_unavailable")
        elif configuration.voice not in model.supported_voices:
            raise VoiceProfileError("cloud_voice_unavailable")
    except OpenRouterError as exc:
        raise VoiceProfileError(exc.code, exc.status) from exc


def model_fingerprint(model_id: str) -> str:
    from .openrouter_catalog import require_model
    return require_model(model_id, "speech").fingerprint


def normalize_cloud_language(configuration: CloudSpeechConfiguration, value: str) -> str:
    # TTS has no language field. Kokoro selects language through its voice ID;
    # the other curated models detect language from text. No fake language flag.
    code = (value or "en").strip().lower().split("-", 1)[0]
    if configuration.model == "hexgrad/kokoro-82m":
        voice = configuration.voice or ""
        language = {"a": "en", "b": "en", "e": "es", "f": "fr", "h": "hi", "i": "it", "j": "ja", "p": "pt", "z": "zh"}.get(voice[:1])
        if language != code:
            raise VoiceProfileError("cloud_voice_language_mismatch")
    elif configuration.model == "mistralai/voxtral-mini-tts-2603":
        if code not in {"en", "fr", "de", "es", "nl", "pt", "it", "hi", "ar"}:
            raise VoiceProfileError("speech_language_unsupported")
    elif configuration.model == "fish-audio/s2.1-pro":
        # Conservative verified core languages; the model's broader quality is
        # not certified by this application.
        if code not in {"en", "zh", "ja", "ko", "fr", "de", "es", "ar", "ru", "pt", "it"}:
            raise VoiceProfileError("speech_language_unsupported")
    else:
        raise VoiceProfileError("cloud_speech_model_unavailable")
    return code

import asyncio
import base64
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
import wave
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from .openrouter_contracts import OpenRouterSpeechRequest
from .speech_references import SpeechRenderSnapshot, capture, file_digest
from .voice_profile_contracts import CloudSpeechApproval, CloudSpeechQuote, CloudSpeechProvenance, CloudSpeechTrial

if TYPE_CHECKING:
    from .speech_clone import SynthesisOutcome

_LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()
MAX_REFERENCE_BYTES = 15 * 1024 * 1024
MAX_MP3_BYTES = 32 * 1024 * 1024
MAX_PCM_BYTES = 600 * 24000 * 2


class _QuoteState(BaseModel):
    public: CloudSpeechQuote
    inputs: dict[str, int]
    estimates: dict[str, float]
    activated: bool = False


def _root() -> Path:
    from . import audiobooks
    from .audiobook_narration import _contained
    root = _contained(audiobooks.books_root() / "_cloud_speech", audiobooks.books_root())
    root.mkdir(exist_ok=True)
    return root


def _connect() -> sqlite3.Connection:
    path = _root() / "speech.db"
    if path.is_symlink():
        raise VoiceProfileError("cloud_speech_storage_unavailable", 503)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    connection.execute("""CREATE TABLE IF NOT EXISTS renders(
        id TEXT PRIMARY KEY,approval_id TEXT NOT NULL,input_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL,normalized INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(approval_id) REFERENCES quotes(id))""")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def request_body(snapshot: SpeechRenderSnapshot, text: str) -> OpenRouterSpeechRequest:
    cloud = snapshot.cloud
    if cloud is None:
        raise VoiceProfileError("cloud_speech_configuration_required")
    reference: str | None = None
    if cloud.clone_reference:
        if not cloud.reference_transfer_confirmed:
            raise VoiceProfileError("cloud_reference_permission_required", 403)
        path = Path(snapshot.reference_audio_path)
        if path.is_symlink() or not path.is_file():
            raise VoiceProfileError("speech_reference_changed", 409)
        with path.open("rb") as handle:
            data = handle.read(MAX_REFERENCE_BYTES + 1)
        if len(data) > MAX_REFERENCE_BYTES:
            raise VoiceProfileError("cloud_reference_too_large")
        if hashlib.sha256(data).hexdigest() != snapshot.reference_sha256:
            raise VoiceProfileError("speech_reference_changed", 409)
        if not snapshot.prompt_text.strip():
            raise VoiceProfileError("reference_transcript_required")
        media = "flac" if path.suffix.lower() == ".flac" else "wav"
        reference = f"data:audio/{media};base64," + base64.b64encode(data).decode("ascii")
    return OpenRouterSpeechRequest(model=cloud.model, input=text.strip(), voice=cloud.voice,
        reference_audio=reference, reference_transcript=snapshot.prompt_text if cloud.clone_reference else None)


def _input_id(snapshot: SpeechRenderSnapshot, text: str) -> str:
    return hashlib.sha256(f"{snapshot.identity}\n{text.strip()}".encode()).hexdigest()


def snapshots(inputs: list[tuple[str, str, str]], directory: Path) -> list[tuple[str, SpeechRenderSnapshot]]:
    from . import voice_profiles
    captured: dict[tuple[str, str], SpeechRenderSnapshot] = {}
    result: list[tuple[str, SpeechRenderSnapshot]] = []
    for text, profile_id, language in inputs:
        if voice_profiles.get_profile(profile_id).renderer != "openrouter":
            continue
        key = profile_id, language
        if key not in captured:
            captured[key] = capture(profile_id, language, directory, None)
        result.append((text, captured[key]))
    return result


def quote_inputs(inputs: list[tuple[str, str, str]]) -> CloudSpeechQuote:
    return quote_snapshots(snapshots(inputs, _root() / "references"))


def quote_snapshots(captured: list[tuple[str, SpeechRenderSnapshot]]) -> CloudSpeechQuote:
    from .openrouter_catalog import estimate_speech
    from .openrouter_errors import OpenRouterError
    captured = [(text,snapshot) for text,snapshot in captured if snapshot.cloud is not None]
    identifier = uuid.uuid4().hex
    counters: Counter[str] = Counter()
    estimates: dict[str, float] = {}
    models: set[str] = set()
    reference = False
    total = 0.0
    try:
        for text, snapshot in captured:
            quote = estimate_speech(request_body(snapshot, text))
            if snapshot.cloud is None or quote.model_fingerprint != snapshot.cloud.model_fingerprint:
                raise VoiceProfileError("cloud_speech_quote_changed",409)
            key = _input_id(snapshot, text)
            counters[key] += 1
            estimates[key] = quote.estimated_usd
            total += quote.estimated_usd
            models.add(quote.model_id)
            reference |= snapshot.cloud is not None and snapshot.cloud.clone_reference
        public = CloudSpeechQuote(id=identifier, estimated_usd=total, request_count=len(captured), models=sorted(models),
            transfers=["text", "reference_audio", "reference_transcript"] if reference else ["text"] if captured else [], expires_at=time.time() + 300)
        state = _QuoteState(public=public, inputs=dict(counters), estimates=estimates)
        with _LOCK, closing(_connect()) as connection:
            connection.execute("INSERT INTO quotes VALUES(?,?)", (identifier, state.model_dump_json()))
            connection.commit()
        return public
    except OpenRouterError as exc:
        raise VoiceProfileError(exc.code, exc.status) from exc


def _load(connection: sqlite3.Connection, identifier: str) -> _QuoteState:
    row = connection.execute("SELECT payload FROM quotes WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise VoiceProfileError("cloud_speech_quote_expired", 409)
    return _QuoteState.model_validate_json(str(row[0]))


def approve_inputs(inputs: list[tuple[str, str, str]], approval: CloudSpeechApproval | None) -> str | None:
    from . import voice_profiles
    if not any(voice_profiles.get_profile(profile).renderer == "openrouter" for _, profile, _ in inputs):
        return None
    return approve_snapshots(snapshots(inputs, _root() / "references"), approval)


def approve_snapshots(captured: list[tuple[str, SpeechRenderSnapshot]], approval: CloudSpeechApproval | None) -> str | None:
    captured = [(text, snapshot) for text, snapshot in captured if snapshot.cloud is not None]
    if not captured:
        return None
    if approval is None or not approval.transfers_confirmed:
        raise VoiceProfileError("cloud_speech_approval_required", 403)
    counters = dict(Counter(_input_id(snapshot, text) for text, snapshot in captured))
    with _LOCK, closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        state = _load(connection, approval.quote_id)
        if state.activated or state.public.expires_at < time.time() or state.inputs != counters:
            raise VoiceProfileError("cloud_speech_quote_changed", 409)
        state.activated = True
        connection.execute("UPDATE quotes SET payload=? WHERE id=?", (state.model_dump_json(), approval.quote_id))
        connection.commit()
    return approval.quote_id


def require_authorization(snapshot: SpeechRenderSnapshot, text: str, target: Path) -> tuple[str, str]:
    """Persist the receipt before POST. Unknown/received-but-unpublished work blocks reuse."""
    approval_id = snapshot.cloud_authorization_id
    if approval_id is None:
        raise VoiceProfileError("cloud_speech_approval_required", 403)
    from . import openrouter_catalog, openrouter_requests
    from .openrouter_errors import OpenRouterError
    try:
        with _LOCK, closing(_connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = _load(connection, approval_id)
            key = _input_id(snapshot, text)
            if not state.activated or key not in state.inputs:
                raise VoiceProfileError("cloud_speech_quote_changed", 409)
            rows = connection.execute("SELECT receipt_id,normalized,input_id FROM renders WHERE approval_id=?", (approval_id,)).fetchall()
            for row in rows:
                if not row["normalized"]:
                    receipt = openrouter_requests.get(str(row["receipt_id"]))
                    code = "cloud_speech_submission_unknown" if receipt.state in {"submitting", "submission_unknown"} else "cloud_speech_output_missing" if receipt.state in {"submitted", "completed"} else "cloud_speech_retry_requires_quote"
                    raise VoiceProfileError(code, 409)
            if sum(str(row["input_id"]) == key for row in rows) >= state.inputs[key]:
                raise VoiceProfileError("cloud_speech_retry_requires_quote", 409)
            body = request_body(snapshot, text)
            quote = openrouter_catalog.quote_speech(body)
            if snapshot.cloud is None or quote.model_fingerprint != snapshot.cloud.model_fingerprint or quote.estimated_usd > state.estimates[key] + 1e-12:
                raise VoiceProfileError("cloud_speech_quote_changed", 409)
            render_id = uuid.uuid4().hex
            receipt = openrouter_requests.prepare(f"speech:{render_id}", quote)
            connection.execute("INSERT INTO renders(id,approval_id,input_id,receipt_id) VALUES(?,?,?,?)", (render_id, approval_id, key, receipt.id))
            connection.commit()
            return receipt.id, quote.id
    except OpenRouterError as exc:
        raise VoiceProfileError(exc.code, exc.status) from exc


def normalize_audio(data: bytes, target: Path) -> None:
    """Decode a bounded MP3 through the existing durable codec owner; publish real PCM only."""
    from . import audiobook_publish
    if not data or len(data) > MAX_MP3_BYTES:
        raise VoiceProfileError("cloud_speech_audio_invalid")
    token = uuid.uuid4().hex
    source, decoded = _root() / f"{token}.mp3", _root() / f"{token}.wav"
    temporary = target.with_name(f".{target.stem}.{token}.normalized.wav")
    try:
        source.write_bytes(data)
        result = audiobook_publish._run(["-y", "-f", "mp3", "-i", str(source), "-vn", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-fs", str(MAX_PCM_BYTES + 100), str(decoded)])
        if result.returncode or not decoded.is_file() or decoded.stat().st_size >= MAX_PCM_BYTES + 100:
            raise VoiceProfileError("cloud_speech_audio_invalid")
        with wave.open(str(decoded), "rb") as audio:
            expected = audio.getnframes() * 2
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (24000, 1, 2) or expected <= 0 or expected > MAX_PCM_BYTES:
                raise VoiceProfileError("cloud_speech_audio_invalid")
            total = 0
            while frames := audio.readframes(65536):
                total += len(frames)
            if total != expected:
                raise VoiceProfileError("cloud_speech_audio_invalid")
        if target.is_symlink() or target.parent.is_symlink():
            raise VoiceProfileError("cloud_speech_storage_unavailable", 503)
        target.parent.mkdir(parents=True, exist_ok=True)
        with decoded.open("rb") as reader, temporary.open("xb") as writer:
            while chunk := reader.read(65536):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        temporary.replace(target)
    except (OSError, wave.Error, EOFError) as exc:
        raise VoiceProfileError("cloud_speech_audio_invalid") from exc
    finally:
        if audiobook_publish.cleanup_pending():
            audiobook_publish.defer_cleanup([source, decoded])
        else:
            source.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def synthesize(*, profile_id: str, text: str, output_path: Path, snapshot: SpeechRenderSnapshot | None) -> SynthesisOutcome:
    from . import voice_profiles
    from .speech_clone import SynthesisOutcome
    from .openrouter_client import OpenRouterClient
    from .openrouter_errors import OpenRouterError
    # The local mock flag is deliberately ignored: it cannot certify a cloud voice.
    if snapshot is None:
        raise VoiceProfileError("cloud_speech_approval_required", 403)
    cloud = snapshot.cloud
    if cloud is None or snapshot.profile_id != profile_id:
        raise VoiceProfileError("cloud_speech_configuration_required")
    with voice_profiles._LOCK:
        profile = voice_profiles.get_profile(profile_id)
        if not profile.consent_confirmed:
            raise VoiceProfileError("consent_required", 403)
        if cloud.clone_reference and (profile.cloud is None or not profile.cloud.reference_transfer_confirmed):
            raise VoiceProfileError("cloud_reference_permission_required", 403)
        body = request_body(snapshot, text)
    receipt_id, quote_id = require_authorization(snapshot, text, output_path)
    try:
        # Live consent at the last synchronous boundary before paid network I/O.
        if not voice_profiles.get_profile(profile_id).consent_confirmed:
            raise VoiceProfileError("consent_required", 403)
        def live_consent() -> None:
            with voice_profiles._LOCK:
                current = voice_profiles.get_profile(profile_id)
                if not current.consent_confirmed:
                    raise VoiceProfileError("consent_required",403)
                if cloud.clone_reference and (current.cloud is None or not current.cloud.reference_transfer_confirmed):
                    raise VoiceProfileError("cloud_reference_permission_required",403)
        audio = asyncio.run(OpenRouterClient().speech(receipt_id, body, quote_id,before_submit=live_consent))
        normalize_audio(audio.data, output_path)
        with voice_profiles._LOCK:
            profile = voice_profiles.get_profile(profile_id)
            if not profile.consent_confirmed or (cloud.clone_reference and (profile.cloud is None or not profile.cloud.reference_transfer_confirmed)):
                output_path.unlink(missing_ok=True)
                raise VoiceProfileError("consent_required", 403)
            provenance = CloudSpeechProvenance(receipt_id=receipt_id,profile_id=profile_id,model=cloud.model,model_fingerprint=cloud.model_fingerprint,
                voice=cloud.voice,reference_transferred=cloud.clone_reference,generation_id=audio.generation_id,actual_cost_usd=audio.actual_cost_usd)
            from .atomic_files import write_object
            from .contracts import JsonObject
            from pydantic import TypeAdapter
            json_adapter: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
            try:
                write_object(output_path.with_suffix(".cloud.json"),json_adapter.validate_json(provenance.model_dump_json()))
                with _LOCK, closing(_connect()) as connection:
                    connection.execute("UPDATE renders SET normalized=1 WHERE receipt_id=?", (receipt_id,))
                    connection.commit()
            except (OSError,sqlite3.Error) as exc:
                raise VoiceProfileError("cloud_speech_storage_unavailable",503) from exc
        return SynthesisOutcome(status="completed", detail="cloud_speech_completed", output_path=output_path)
    except OpenRouterError as exc:
        _LOG.warning("Cloud speech request failed with %s (receipt %s)", exc.code, receipt_id)
        raise VoiceProfileError("cloud_speech_submission_unknown" if exc.code in {"openrouter_submission_unknown", "submission_unknown"} else exc.code, exc.status) from exc


def read_provenance(audio_path: Path) -> CloudSpeechProvenance | None:
    path = audio_path.with_suffix(".cloud.json")
    if path.is_symlink():
        raise VoiceProfileError("cloud_speech_storage_unavailable",503)
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            data = handle.read(65537)
        if len(data)>65536:
            raise ValueError("provenance_too_large")
        return CloudSpeechProvenance.model_validate_json(data)
    except (OSError,ValidationError,ValueError) as exc:
        raise VoiceProfileError("cloud_speech_storage_unavailable",503) from exc

from contextlib import contextmanager
from collections.abc import Iterator


@contextmanager
def compatible_pcm(paths: list[Path], directory: Path, *, cloud_workflow: bool) -> Iterator[list[Path]]:
    """Convert only incompatible mixed-renderer joins; dry passages/cache stay untouched."""
    from . import audiobook_publish, audiobooks
    from .audiobook_narration import _contained
    parameters: list[tuple[int,int,int]] = []
    for path in paths:
        _contained(path,audiobooks.books_root())
        with wave.open(str(path),"rb") as audio_source:
            parameters.append((audio_source.getframerate(),audio_source.getnchannels(),audio_source.getsampwidth()))
    if len(set(parameters))<=1 or not cloud_workflow:
        yield paths
        return
    _contained(directory,audiobooks.books_root())
    prepared: list[Path] = []
    generated: list[Path] = []
    try:
        for source,parameters_for_source in zip(paths,parameters,strict=True):
            if parameters_for_source==(24000,1,2):
                prepared.append(source)
                continue
            target = directory / f".{uuid.uuid4().hex}.mixed.wav"
            generated.append(target)
            result = audiobook_publish._run(["-y","-f","wav","-i",str(source),"-vn","-ac","1","-ar","24000","-c:a","pcm_s16le","-fs",str(MAX_PCM_BYTES+100),str(target)])
            if result.returncode or not target.is_file() or target.stat().st_size>=MAX_PCM_BYTES+100:
                raise VoiceProfileError("cloud_speech_audio_invalid")
            _validate_normalized(target)
            prepared.append(target)
        yield prepared
    finally:
        if audiobook_publish.cleanup_pending():
            audiobook_publish.defer_cleanup(generated)
        else:
            for path in generated:
                path.unlink(missing_ok=True)


def _validate_normalized(path: Path) -> None:
    with wave.open(str(path),"rb") as audio:
        expected = audio.getnframes()*2
        if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth())!=(24000,1,2) or expected<=0 or expected>MAX_PCM_BYTES:
            raise VoiceProfileError("cloud_speech_audio_invalid")
        total=0
        while frames:=audio.readframes(65536):
            total+=len(frames)
        if total!=expected:
            raise VoiceProfileError("cloud_speech_audio_invalid")


def list_trials(profile_id: str) -> list[CloudSpeechTrial]:
    """Rediscover at most twenty paid previews; invalid sidecars grant no playback."""
    import heapq
    import re
    from datetime import datetime,timezone
    from . import speech_clone,voice_profiles
    with voice_profiles._LOCK:
        profile=voice_profiles.get_profile(profile_id)
        if not profile.consent_confirmed:
            return []
        root=speech_clone.TRIALS_ROOT
        if root.is_symlink():
            raise VoiceProfileError("cloud_speech_storage_unavailable",503)
        if not root.exists():
            return []
        selected: list[tuple[float,str,CloudSpeechTrial]]=[]
        try:
            for metadata in root.glob("*.cloud.json"):
                if re.fullmatch(r"[0-9a-f]{32}\.cloud\.json",metadata.name) is None or metadata.is_symlink() or not metadata.is_file():
                    continue
                identifier=metadata.name[:32]
                audio=root/f"{identifier}.wav"
                if audio.is_symlink() or not audio.is_file() or audio.resolve().parent!=root.resolve():
                    continue
                try:
                    provenance=read_provenance(audio)
                    if provenance is None or provenance.profile_id!=profile_id:
                        continue
                    modified=metadata.stat().st_mtime
                    trial=CloudSpeechTrial(id=identifier,profile_id=profile_id,created_at=datetime.fromtimestamp(modified,timezone.utc).isoformat(),
                        audio_url=f"/api/speech-clone/trials/{identifier}/audio",provenance=provenance)
                    heapq.heappush(selected,(modified,identifier,trial))
                    if len(selected)>20:
                        heapq.heappop(selected)
                except (VoiceProfileError,OSError,ValueError,OverflowError):
                    _LOG.warning("Ignoring invalid cloud trial metadata: %s",metadata.name)
            return [item[2] for item in sorted(selected,key=lambda item:(item[0],item[1]),reverse=True)]
        except OSError as exc:
            raise VoiceProfileError("cloud_speech_storage_unavailable",503) from exc
