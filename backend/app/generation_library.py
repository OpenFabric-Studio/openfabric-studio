"""SQLite generation snapshots. Retention never removes tracks, files or presets."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from .contracts import JsonObject
from .generation_contracts import (
    AceGenerationSettings, CreateGenerationPresetRequest, GenerationAbcSamplingSettings, GenerationEngine,
    GenerationHistoryEntry, GenerationHistoryResponse, GenerationLibrarySettings,
    GenerationPreset, GenerationPresetImportResponse, GenerationPresetsResponse,
    GenerationSamplingSettings, GenerationSettings, ImportGenerationPresetsRequest,
    UpdateGenerationLibrarySettingsRequest, UpdateGenerationPresetRequest, YueGenerationSettings,
)

LibraryErrorCode = Literal['generation_record_not_found', 'generation_revision_conflict',
                           'generation_preset_name_exists', 'generation_engine_mismatch',
                           'generation_legacy_key_conflict']


class GenerationLibraryError(Exception):
    def __init__(self, code: LibraryErrorCode) -> None:
        self.code = code
        super().__init__(code)


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def migrate(connection: sqlite3.Connection) -> None:
    """Additive, atomic migration and one-time seed from surviving generated tracks."""
    with _transaction(connection):
        connection.execute('''CREATE TABLE IF NOT EXISTS generation_library_schema (
            id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1),
            legacy_seeded INTEGER NOT NULL CHECK(legacy_seeded IN (0,1)))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS generation_library_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), history_limit INTEGER NOT NULL CHECK(history_limit BETWEEN 1 AND 10000),
            revision INTEGER NOT NULL CHECK(revision>=1))''')
        connection.execute('INSERT OR IGNORE INTO generation_library_settings VALUES(1,100,1)')
        connection.execute('''CREATE TABLE IF NOT EXISTS generation_history (
            id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
            engine TEXT NOT NULL CHECK(engine IN ('ace_step','yue2')), created_at TEXT NOT NULL,
            title TEXT NOT NULL, lyrics TEXT NOT NULL, seed INTEGER,
            track_id INTEGER UNIQUE REFERENCES tracks(id) ON DELETE SET NULL,
            settings_json TEXT NOT NULL, search_text TEXT NOT NULL,
            reference_requires_reupload INTEGER NOT NULL CHECK(reference_requires_reupload IN (0,1)))''')
        connection.execute('CREATE INDEX IF NOT EXISTS idx_generation_history_engine ON generation_history(engine)')
        connection.execute('''CREATE TABLE IF NOT EXISTS generation_presets (
            id TEXT PRIMARY KEY CHECK(length(id)=32 AND id NOT GLOB '*[^0-9a-f]*'),
            name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200), name_key TEXT NOT NULL,
            engine TEXT NOT NULL CHECK(engine IN ('ace_step','yue2')), settings_json TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision>=1), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            source_history_id TEXT, UNIQUE(engine,name_key))''')
        connection.execute('''CREATE TABLE IF NOT EXISTS generation_preset_imports (
            legacy_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
            preset_id TEXT REFERENCES generation_presets(id) ON DELETE SET NULL)''')
        connection.execute('INSERT OR IGNORE INTO generation_library_schema VALUES(1,1,0)')
        seeded = connection.execute('SELECT legacy_seeded FROM generation_library_schema WHERE id=1').fetchone()
        if seeded is not None and seeded['legacy_seeded'] == 0:
            retention = get_settings(connection).history_limit
            rows = connection.execute('''SELECT * FROM tracks
                WHERE model IN ('ace_step','yue2') AND NOT EXISTS (
                    SELECT 1 FROM audio_versions av WHERE av.worker_track_id=tracks.id AND av.track_id!=tracks.id)
                ORDER BY id DESC LIMIT ?''', (retention,)).fetchall()
            for row in reversed(rows):
                capture_track(connection, row)
            connection.execute('UPDATE generation_library_schema SET legacy_seeded=1 WHERE id=1')


def get_settings(connection: sqlite3.Connection) -> GenerationLibrarySettings:
    row = connection.execute('SELECT history_limit,revision FROM generation_library_settings WHERE id=1').fetchone()
    if row is None:
        raise ValueError('generation_library_settings_missing')
    return GenerationLibrarySettings.model_validate(dict(row))


def _prune(connection: sqlite3.Connection) -> None:
    connection.execute('''DELETE FROM generation_history WHERE rowid NOT IN (
        SELECT rowid FROM generation_history ORDER BY rowid DESC LIMIT ?)''', (get_settings(connection).history_limit,))


def update_settings(connection: sqlite3.Connection, request: UpdateGenerationLibrarySettingsRequest) -> GenerationLibrarySettings:
    with _transaction(connection):
        cursor = connection.execute('UPDATE generation_library_settings SET history_limit=?,revision=revision+1 WHERE id=1 AND revision=?',
                                    (request.history_limit, request.revision))
        if cursor.rowcount != 1:
            raise GenerationLibraryError('generation_revision_conflict')
        _prune(connection)
        result = get_settings(connection)
    return result


def _validated_fields(model: type[AceGenerationSettings] | type[YueGenerationSettings] | type[GenerationSamplingSettings],
                      values: dict[str, object]) -> dict[str, object]:
    """Salvage known valid legacy fields without trusting opaque vendor parameters."""
    result: dict[str, object] = {}
    for name, value in values.items():
        field = model.model_fields.get(name)
        if field is None:
            continue
        try:
            result[name] = TypeAdapter[object](field.rebuild_annotation()).validate_python(value, strict=True)
        except ValidationError:
            continue
    return result


def _sampling(params: JsonObject, prefix: str, nested: str) -> GenerationSamplingSettings:
    nested_value = params.get(nested)
    values: dict[str, object] = dict(nested_value) if isinstance(nested_value, dict) else {}
    for name in GenerationSamplingSettings.model_fields:
        if f'{prefix}_{name}' in params:
            values[name] = params[f'{prefix}_{name}']
    model = GenerationAbcSamplingSettings if prefix == 'abc' else GenerationSamplingSettings
    valid = _validated_fields(model, values)
    minimum = valid.get('min_tokens')
    maximum = valid.get('max_tokens')
    if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
        valid.pop('min_tokens')
    return model.model_validate(valid)


def snapshot_from_params(engine: GenerationEngine, params: JsonObject, lyrics: str, seed: object) -> GenerationSettings:
    """Prefer validated app snapshots; native fallback copies only inspected public fields."""
    supplied = params.get('_generation_settings')
    if supplied is not None:
        try:
            snapshot = TypeAdapter[GenerationSettings](GenerationSettings).validate_python(supplied)
            if snapshot.engine == engine:
                return snapshot
        except ValidationError:
            pass
    if engine == 'ace_step':
        mapping = {'simpleQuery': 'sample_query', 'customPrompt': 'prompt', 'audioFormat': 'audio_format',
                   'bpm': 'bpm', 'keyScale': 'key_scale', 'timeSignature': 'time_signature',
                   'vocalLanguage': 'vocal_language', 'inferenceSteps': 'inference_steps',
                   'guidanceScale': 'guidance_scale', 'selectedModel': 'model', 'randomSeed': 'use_random_seed',
                   'batchSize': 'batch_size', 'taskType': 'task_type', 'repaintStart': 'repainting_start',
                   'repaintEnd': 'repainting_end', 'trackName': 'track_name', 'trackClasses': 'track_classes',
                   'coverStrength': 'audio_cover_strength', 'useCotCaption': 'use_cot_caption'}
        values: dict[str, object] = {name: params[key] for name, key in mapping.items() if key in params}
        values.update({'engine': engine, 'customLyrics': lyrics[:100_000], 'seed': seed,
                       'mode': 'simple' if params.get('sample_mode') is True or params.get('sample_query') else 'custom',
                       'useRefAudio': bool(params.get('ctx_audio') or params.get('src_audio') or params.get('task_type') in ('cover','repaint','extract','lego','complete')),
                       'styleReferenceRequiresReupload': bool(params.get('reference_audio')),
                       'loraRequiresReselection': bool(params.get('lora_path'))})
        duration = params.get('audio_duration', params.get('duration'))
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            values['durationAuto'] = duration <= 0
            if duration > 0:
                values['duration'] = min(300, max(10, duration))
        return AceGenerationSettings.model_validate(_validated_fields(AceGenerationSettings, values))
    mapping = {'style': 'style', 'cot': 'cot', 'precision': 'precision', 'abc': 'abc',
               'cfgScale': 'cfg_scale', 'numInferenceSteps': 'num_inference_steps', 'batchSize': 'batch_size'}
    values = {name: params[key] for name, key in mapping.items() if key in params}
    values.update({'engine': engine, 'lyrics': lyrics[:100_000], 'seed': seed,
                   'semantic': _sampling(params, 'semantic', 'semantic'),
                   'abcSampling': _sampling(params, 'abc', 'abc_sampling'),
                   'referenceRequiresReupload': bool(params.get('audio'))})
    return YueGenerationSettings.model_validate(_validated_fields(YueGenerationSettings, values))


def capture_track(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Caller owns the track write transaction; a snapshot and its track commit together."""
    engine: object = row['model']
    if engine not in ('ace_step', 'yue2'):
        return
    if not isinstance(engine, str):
        raise ValueError('invalid_generation_engine')
    params_raw: object = row['params_json']
    lyrics_raw: object = row['lyrics']
    title_raw: object = row['title']
    if not isinstance(lyrics_raw, str) or not isinstance(title_raw, str):
        raise ValueError('invalid_generation_track_text')
    try:
        params = TypeAdapter(JsonObject).validate_json(params_raw) if isinstance(params_raw, str) else {}
    except ValidationError:
        params = {}
    typed_engine: GenerationEngine = 'ace_step' if engine == 'ace_step' else 'yue2'
    snapshot = snapshot_from_params(typed_engine, params, lyrics_raw, row['seed'])
    reference = (snapshot.useRefAudio or snapshot.styleReferenceRequiresReupload) if isinstance(snapshot, AceGenerationSettings) else snapshot.referenceRequiresReupload
    seed = row['seed']
    if isinstance(seed, bool) or not isinstance(seed, int) or abs(seed) > 9_007_199_254_740_991:
        seed = None
    search_fields = [title_raw, lyrics_raw]
    if isinstance(snapshot, AceGenerationSettings):
        search_fields.extend([snapshot.simpleQuery, snapshot.customPrompt, snapshot.customLyrics])
    else:
        search_fields.extend([snapshot.style, snapshot.abc, snapshot.lyrics])
    connection.execute('''INSERT OR IGNORE INTO generation_history
        (id,engine,created_at,title,lyrics,seed,track_id,settings_json,search_text,reference_requires_reupload)
        VALUES(?,?,?,?,?,?,?,?,?,?)''', (uuid.uuid4().hex, typed_engine, row['created_at'], title_raw[:500],
            lyrics_raw[:100_000], seed, row['id'], snapshot.model_dump_json(), '\n'.join(search_fields).casefold(), int(reference)))
    _prune(connection)


def _history(row: sqlite3.Row) -> GenerationHistoryEntry:
    snapshot = TypeAdapter[GenerationSettings](GenerationSettings).validate_json(row['settings_json'])
    return GenerationHistoryEntry.model_validate({
        'id': row['id'], 'engine': row['engine'], 'created_at': row['created_at'],
        'title': row['title'], 'lyrics': row['lyrics'], 'seed': row['seed'],
        'track_id': row['track_id'], 'settings': snapshot,
        'reference_requires_reupload': bool(row['reference_requires_reupload']),
    })


def _search_pattern(search: str) -> str:
    return '%' + search.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def list_history(connection: sqlite3.Connection, engine: GenerationEngine | None = None,
                 search: str = '', limit: int = 100, offset: int = 0) -> GenerationHistoryResponse:
    where = "WHERE (? IS NULL OR engine=?) AND search_text LIKE ? ESCAPE '\\'"
    values = (engine, engine, _search_pattern(search))
    count = connection.execute(f'SELECT COUNT(*) FROM generation_history {where}', values).fetchone()[0]
    rows = connection.execute(f'SELECT * FROM generation_history {where} ORDER BY rowid DESC LIMIT ? OFFSET ?', (*values, limit, offset)).fetchall()
    return GenerationHistoryResponse(data=[_history(row) for row in rows], total=count, retention_limit=get_settings(connection).history_limit)


def delete_history(connection: sqlite3.Connection, record_id: str) -> None:
    with _transaction(connection):
        cursor = connection.execute('DELETE FROM generation_history WHERE id=?', (record_id,))
        if cursor.rowcount != 1:
            raise GenerationLibraryError('generation_record_not_found')


def _preset(row: sqlite3.Row) -> GenerationPreset:
    return GenerationPreset.model_validate({
        'id': row['id'], 'name': row['name'], 'engine': row['engine'],
        'settings': TypeAdapter[GenerationSettings](GenerationSettings).validate_json(row['settings_json']),
        'revision': row['revision'], 'created_at': row['created_at'],
        'updated_at': row['updated_at'], 'source_history_id': row['source_history_id'],
    })


def _get_preset(connection: sqlite3.Connection, preset_id: str) -> GenerationPreset:
    row = connection.execute('SELECT * FROM generation_presets WHERE id=?', (preset_id,)).fetchone()
    if row is None:
        raise GenerationLibraryError('generation_record_not_found')
    return _preset(row)


def get_preset(connection: sqlite3.Connection, preset_id: str) -> GenerationPreset:
    return _get_preset(connection, preset_id)


def list_presets(connection: sqlite3.Connection, engine: GenerationEngine | None = None, search: str = '',
                 limit: int = 100, offset: int = 0) -> GenerationPresetsResponse:
    values = (engine, engine, _search_pattern(search))
    where = "WHERE (? IS NULL OR engine=?) AND name_key LIKE ? ESCAPE '\\'"
    count = connection.execute(f'SELECT COUNT(*) FROM generation_presets {where}', values).fetchone()[0]
    rows = connection.execute(f'SELECT * FROM generation_presets {where} ORDER BY name_key,id LIMIT ? OFFSET ?',
                              (*values, limit, offset)).fetchall()
    return GenerationPresetsResponse(data=[_preset(row) for row in rows], total=count)


def _insert_preset(connection: sqlite3.Connection, request: CreateGenerationPresetRequest) -> GenerationPreset:
    if request.source_history_id is not None:
        source = connection.execute('SELECT engine FROM generation_history WHERE id=?', (request.source_history_id,)).fetchone()
        if source is None:
            raise GenerationLibraryError('generation_record_not_found')
        if source['engine'] != request.settings.engine:
            raise GenerationLibraryError('generation_engine_mismatch')
    record_id = uuid.uuid4().hex
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    try:
        connection.execute('''INSERT INTO generation_presets
            (id,name,name_key,engine,settings_json,revision,created_at,updated_at,source_history_id)
            VALUES(?,?,?,?,?,1,?,?,?)''', (record_id, request.name, request.name.casefold(), request.settings.engine,
                                        request.settings.model_dump_json(), now, now, request.source_history_id))
    except sqlite3.IntegrityError as exc:
        if connection.execute('SELECT 1 FROM generation_presets WHERE engine=? AND name_key=?',
                              (request.settings.engine, request.name.casefold())).fetchone() is not None:
            raise GenerationLibraryError('generation_preset_name_exists') from exc
        raise
    return _get_preset(connection, record_id)


def create_preset(connection: sqlite3.Connection, request: CreateGenerationPresetRequest) -> GenerationPreset:
    with _transaction(connection):
        result = _insert_preset(connection, request)
    return result


def update_preset(connection: sqlite3.Connection, preset_id: str, request: UpdateGenerationPresetRequest) -> GenerationPreset:
    with _transaction(connection):
        current = _get_preset(connection, preset_id)
        if current.revision != request.revision:
            raise GenerationLibraryError('generation_revision_conflict')
        if current.engine != request.settings.engine:
            raise GenerationLibraryError('generation_engine_mismatch')
        conflicting = connection.execute('SELECT 1 FROM generation_presets WHERE engine=? AND name_key=? AND id!=?',
                                         (current.engine, request.name.casefold(), preset_id)).fetchone()
        if conflicting is not None:
            raise GenerationLibraryError('generation_preset_name_exists')
        connection.execute('''UPDATE generation_presets SET name=?,name_key=?,settings_json=?,revision=revision+1,updated_at=?
            WHERE id=? AND revision=?''', (request.name, request.name.casefold(), request.settings.model_dump_json(),
                                         time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), preset_id, request.revision))
        result = _get_preset(connection, preset_id)
    return result


def duplicate_preset(connection: sqlite3.Connection, preset_id: str, name: str, revision: int) -> GenerationPreset:
    with _transaction(connection):
        current = _get_preset(connection, preset_id)
        if current.revision != revision:
            raise GenerationLibraryError('generation_revision_conflict')
        # Provenance survives a source history record being pruned.
        request = CreateGenerationPresetRequest(name=name, settings=current.settings)
        result = _insert_preset(connection, request)
        connection.execute('UPDATE generation_presets SET source_history_id=? WHERE id=?', (current.source_history_id, result.id))
        result = _get_preset(connection, result.id)
    return result


def delete_preset(connection: sqlite3.Connection, preset_id: str, revision: int) -> None:
    with _transaction(connection):
        current = _get_preset(connection, preset_id)
        if current.revision != revision:
            raise GenerationLibraryError('generation_revision_conflict')
        connection.execute('DELETE FROM generation_presets WHERE id=? AND revision=?', (preset_id, revision))


def import_presets(connection: sqlite3.Connection, request: ImportGenerationPresetsRequest) -> GenerationPresetImportResponse:
    results: list[GenerationPreset] = []
    imported = 0
    with _transaction(connection):
        for legacy in request.presets:
            fingerprint = hashlib.sha256(json.dumps({'name': legacy.name, 'settings': json.loads(legacy.settings.model_dump_json())},
                                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            previous = connection.execute('SELECT fingerprint,preset_id FROM generation_preset_imports WHERE legacy_key=?', (legacy.legacy_key,)).fetchone()
            if previous is not None:
                if previous['fingerprint'] != fingerprint:
                    raise GenerationLibraryError('generation_legacy_key_conflict')
                if previous['preset_id'] is not None:
                    results.append(_get_preset(connection, previous['preset_id']))
                continue
            name = legacy.name
            suffix = 1
            while connection.execute('SELECT 1 FROM generation_presets WHERE engine=? AND name_key=?', (legacy.settings.engine, name.casefold())).fetchone() is not None:
                label = f' (imported {suffix})'
                name = legacy.name[:200 - len(label)] + label
                suffix += 1
            preset = _insert_preset(connection, CreateGenerationPresetRequest(name=name, settings=legacy.settings))
            connection.execute('INSERT INTO generation_preset_imports VALUES(?,?,?)', (legacy.legacy_key, fingerprint, preset.id))
            results.append(preset)
            imported += 1
    return GenerationPresetImportResponse(data=results, imported=imported)
