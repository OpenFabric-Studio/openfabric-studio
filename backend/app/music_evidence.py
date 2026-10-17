"""Capture publisher declarations once; exports use recorded, bounded lineage."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, TypeAdapter, ValidationError

from . import db
from .contracts import JsonObject
from .export_provenance_contracts import MusicAdapterIdentity, MusicComponentEvidence, MusicEvidence
from .module_licenses import declarations
from .music_lora_contracts import MusicAdapter

_json = TypeAdapter(JsonObject)
# shortcut: inspect at most 100 tracks and mark overflow unknown; expand when larger real mixes require it.
_MAX_TRACKS = 100


class _RecordedAdapter(MusicAdapter):
    strength: float = Field(ge=0, le=2)


def capture_music_evidence(engine: Literal['ace_step', 'yue2', 'stable_audio3']) -> MusicEvidence:
    """Backend-owned submission snapshot, never an installed-file audit."""
    components = [MusicComponentEvidence(component_id=row.component_id, scope=row.scope,
        restriction=row.restriction, redistribution=row.redistribution, copyleft=row.copyleft,
        declared_license=row.declared_license, source_url=row.source_url,
        evidence=row.evidence, identities=row.identities) for row in declarations(engine)
        if row.component_id not in {'sheetsage2_orig', 'muscriptor_small_f32'}]
    return MusicEvidence(components=components, lineage_complete=True)


def serialize_music_evidence(value: MusicEvidence) -> JsonObject:
    """Return a detached JSON snapshot for backend-owned job/track records."""
    return _json.validate_json(value.model_dump_json())


def merge_music_evidence(values: Sequence[MusicEvidence | None]) -> MusicEvidence:
    """Conditioning and sample restrictions count just as audio restrictions do."""
    components: dict[str, MusicComponentEvidence] = {}
    adapters: dict[str, MusicAdapterIdentity] = {}
    attributions: dict[str, None] = {}
    restrictions: set[str] = set()
    for value in values:
        if value is None:
            continue
        restrictions.add(value.restriction)
        for item in value.components:
            components.setdefault(item.model_dump_json(), item.model_copy(deep=True))
        for adapter in value.adapters:
            adapters.setdefault(adapter.model_dump_json(), adapter.model_copy(deep=True))
        attributions.update(dict.fromkeys(value.public_attributions))
    return MusicEvidence(components=list(components.values()), adapters=list(adapters.values()),
        restriction='noncommercial' if 'noncommercial' in restrictions else 'conditional' if 'conditional' in restrictions else 'unknown',
        lineage_complete=bool(values) and all(value is not None and value.lineage_complete for value in values),
        public_attributions=list(attributions))


def track_music_evidence(track_id: int) -> MusicEvidence:
    """Follow only recorded track links; absent or malformed history stays unknown."""
    evidence: list[MusicEvidence | None] = []
    pending: list[tuple[int, frozenset[int]]] = [(track_id, frozenset())]
    visited: set[int] = set()
    while pending and len(visited) < _MAX_TRACKS:
        identifier, ancestors = pending.pop()
        if identifier in ancestors:
            evidence.append(None)
            continue
        if identifier in visited:
            continue
        visited.add(identifier)
        row = db.get_track(identifier)
        model: object = row['model'] if row is not None and 'model' in row.keys() else None
        raw: object = row['params_json'] if row is not None and 'params_json' in row.keys() else None
        if not isinstance(raw, str) or len(raw) > 8 * 1024 * 1024:
            evidence.append(None)
            continue
        try:
            params = _json.validate_json(raw)
        except ValidationError:
            evidence.append(None)
            continue
        captured = params.get('_music_evidence')
        if captured is not None:
            try:
                evidence.append(MusicEvidence.model_validate(captured))
            except ValidationError:
                evidence.append(None)
        elif model != 'editor':
            evidence.append(None)
        adapters = params.get('_music_adapters', [])
        if isinstance(adapters, list) and len(adapters) <= 1000:
            for raw_adapter in adapters:
                try:
                    adapter = _RecordedAdapter.model_validate(raw_adapter)
                except ValidationError:
                    evidence.append(None)
                    continue
                evidence.append(MusicEvidence(adapters=[MusicAdapterIdentity(id=adapter.id, engine=adapter.engine,
                    target=adapter.target, sha256=adapter.sha256, strength=adapter.strength,
                    source_job_id=adapter.source_job_id)], lineage_complete=True))
        else:
            evidence.append(None)
        parents = params.get('source_track_ids', []) if model == 'editor' else []
        if isinstance(parents, list) and len(parents) <= _MAX_TRACKS:
            if parents and params.get('source_ancestry_complete') is not True:
                evidence.append(None)
            linked = list(parents)
        else:
            evidence.append(None)
            linked = []
        parent = params.get('source_track_id') if model == 'upload' and params.get('source') == 'voice_replacement' else None
        if parent is not None:
            linked.append(parent)
        for parent in linked:
            if not isinstance(parent, int) or isinstance(parent, bool) or parent <= 0:
                evidence.append(None)
            else:
                pending.append((parent, ancestors | {identifier}))
    if pending:
        evidence.append(None)
    return merge_music_evidence(evidence)


def _public_url(value: str | None) -> str | None:
    if value is None:
        return None
    url = urlsplit(value)
    return urlunsplit((url.scheme, url.netloc, url.path, '', ''))


def shareable_music_evidence(value: MusicEvidence) -> MusicEvidence:
    """Share public attribution and hashes, never operator labels or local filenames."""
    shared = value.model_copy(deep=True)
    for component in shared.components:
        component.source_url = _public_url(component.source_url)
        for observation in component.evidence:
            observation.reviewer = 'public source observation'
            observation.source_url = _public_url(observation.source_url)
        identities = []
        for identity in component.identities:
            identity.repository_url = _public_url(identity.repository_url)
            if identity.repository_url is None:
                if identity.sha256 is None:
                    continue
                identity.filename = None
                identity.revision = None
            identities.append(identity)
        component.identities = identities
    return shared
