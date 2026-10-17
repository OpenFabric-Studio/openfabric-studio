"""Server-created queue proof; neither source path nor proof is a submit field."""
from __future__ import annotations
from .beat_song_mix_contracts import SongSourceSnapshot
from .music_analysis_contracts import MusicAnalysisId
from .music_conditioning_contracts import ConditioningContract


class QueuedMusicSource(ConditioningContract):
    owner_id: MusicAnalysisId
    snapshot: SongSourceSnapshot
