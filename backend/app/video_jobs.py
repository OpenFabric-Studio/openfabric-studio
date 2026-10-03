"""Videos for songs already in the library.

The picture comes from LTX-2.3 on Apple Silicon (the MLX port). One shot is a
short clip. A full song is a list of shots the user can edit: each shot is
generated from that part of the song, then the shots are joined and the whole
song is put back on top.
"""

from __future__ import annotations

import asyncio
import json
import math
import logging
import os
import re
import secrets
import shutil
import struct
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .job_lifecycle import (
    cancel_and_wait,
    communicate_process,
    kill_process_tree,
    spawn_process,
)
from .config import DATA_DIR, FFMPEG_BIN_DIR, LOG_DIR, LTX_DIR
from .stems import gpu_lock
from .gpu_lease import gpu_lease
from .video_projects import atomic_text
from .video_process import (
    WorkerIdentity,
    WorkerOutputError,
    read_owned_output,
    spawn_owned,
    terminate_verified,
)
from .video_media import VideoMediaError, validate_media

logger = logging.getLogger(__name__)

CLIP_SECONDS = (2, 4, 6, 8, 10, 12)
FRAME_RATE = 24
WIDTH = 704
HEIGHT = 448
SIZES = ((704, 448), (768, 512), (1280, 704))
MAX_SHOTS = 40
STAGE1_STEPS = 30
STAGE2_STEPS = 3
CFG_SCALE = 3.0
MODEL_ID = "dgrauet/ltx-2.3-mlx-q8"
GEMMA_ID = "mlx-community/gemma-3-12b-it-4bit"
PROMPT_LIMIT = 400
_ACTIVE = {"queued", "running"}
_ID = re.compile(r"^[0-9a-f]{32}$")
_EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_][\w.]*)?(?:Error|Exception|Interrupt)\b")
_BAR = re.compile(r"(\d+)\s*/\s*(\d+)")


class VideoJobError(Exception):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


class VideoCancelled(Exception):
    pass


@dataclass
class ProcSlot:
    proc: asyncio.subprocess.Process | None = None
    owner: VideoJob | None = None


@dataclass
class VideoJob:
    id: str
    track_id: int
    title: str
    prompt: str
    seconds: int
    start_sec: float
    frames: int
    seed: int
    created_at: str
    status: str = "queued"
    error: str = ""
    error_code: str = ""
    cancel: bool = False
    dropped: bool = False
    shots: list[dict] = field(default_factory=list)
    shot_index: int = 0
    shot_count: int = 0
    progress_current: int = 0
    progress_total: int = 0
    phase: str = ""
    stage1_steps: int = STAGE1_STEPS
    stage2_steps: int = STAGE2_STEPS
    cfg_scale: float = CFG_SCALE
    width: int = WIDTH
    height: int = HEIGHT
    slot: ProcSlot = field(default_factory=ProcSlot)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    worker: WorkerIdentity | None = None


_current: VideoJob | None = None
_unverified: set[str] = set()
_gate = asyncio.Lock()


def frames_for_seconds(seconds: int, frame_rate: int = FRAME_RATE) -> int:
    """LTX frame counts are 8k+1. At 24 fps, 4s is 97 frames and 8s is 193."""
    if seconds not in CLIP_SECONDS or frame_rate <= 0:
        raise VideoJobError("bad_length")
    steps = (seconds * frame_rate - 1 + 7) // 8
    return 8 * steps + 1


def cover_seconds(remaining: float) -> int | None:
    """Shot length that keeps walking an 8 second grid until the song runs out."""
    if remaining < 2 - 0.05:
        return None
    if remaining >= 8 + 2 - 0.05:
        return 8
    for length in (12, 10, 8, 6, 4, 2):
        if length <= remaining + 0.25:
            return length
    return None


def shot_ends(duration_sec: float) -> list[float]:
    """Ends of the shots propose_plan would use. Each step is a whole shot."""
    if not math.isfinite(duration_sec) or duration_sec <= 0:
        return []
    ends: list[float] = []
    cursor = 0.0
    while len(ends) < MAX_SHOTS:
        seconds = cover_seconds(duration_sec - cursor)
        if seconds is None:
            break
        cursor += seconds
        ends.append(float(cursor))
    return ends


def timeline_duration(shots: list[dict], song_duration: float) -> float:
    """How long the joined video should be.

    A prefix that stops well before the song ends there, on a shot boundary.
    A plan that already reaches the song keeps a leftover shorter than one shot.
    """
    covered = 0.0
    for shot in shots:
        covered = max(covered, float(shot["start_sec"]) + float(shot["seconds"]))
    if not math.isfinite(song_duration) or song_duration <= 0:
        return covered
    if song_duration - covered < 2:
        return song_duration
    return covered


# Location phrases already include the preposition, so a shot can drop one in
# as a place. Longer names come first: "gypsy jazz" before "jazz".
_PLACES = (
    ("gypsy jazz", "inside a small lamp-lit jazz club, on a wooden stage"),
    ("jazz", "inside a small lamp-lit jazz club"),
    ("blues", "inside a dim bar"),
    ("hip hop", "on a night street beside a small stage"),
    ("hip-hop", "on a night street beside a small stage"),
    ("rap", "on a night street beside a small stage"),
    ("metal", "on a dark stage under hard light"),
    ("rock", "on a dim club stage"),
    ("punk", "inside a small loud club"),
    ("folk", "inside a wooden room"),
    ("country", "on a wooden bar stage"),
    ("classical", "inside a concert hall"),
    ("orchestra", "inside a concert hall"),
    ("electronic", "inside a dark room of colored light"),
    ("synth", "inside a dark room of colored light"),
    ("edm", "inside a dark room of colored light"),
    ("opera", "on a theater stage"),
    ("ballad", "inside a quiet room"),
    ("reggae", "on a warm open-air stage"),
    ("latin", "on a warm club stage"),
    ("bossa", "inside a quiet sunlit room"),
    ("pop", "on a small stage in soft light"),
    ("soul", "inside a small club"),
    ("funk", "on a tight club stage"),
    ("ambient", "inside a quiet dark room"),
    ("lo-fi", "inside a small bedroom"),
    ("lofi", "inside a small bedroom"),
)
_DEFAULT_WHERE = "on a small stage"
# Longer names come first so "acoustic guitar" is kept and bare "guitar" is not
# counted a second time.
_INSTRUMENTS = (
    "acoustic guitar",
    "electric guitar",
    "guitar",
    "violin",
    "cello",
    "piano",
    "double bass",
    "upright bass",
    "bass",
    "drums",
    "saxophone",
    "trumpet",
    "flute",
    "clarinet",
    "accordion",
    "strings",
)
_PLAYING = {
    "acoustic guitar": "Fingers move on the frets.",
    "electric guitar": "A hand moves on the guitar neck.",
    "guitar": "Fingers move on the frets.",
    "violin": "The bow draws across the strings.",
    "cello": "The bow moves across its strings.",
    "piano": "Hands move on the keys.",
    "double bass": "A hand moves on the upright bass.",
    "upright bass": "A hand moves on the upright bass.",
    "bass": "Fingers walk along the bass.",
    "drums": "Sticks come down on the drums.",
    "saxophone": "The saxophone is raised.",
    "trumpet": "The trumpet is raised.",
    "flute": "The flute is raised.",
    "clarinet": "The clarinet is raised.",
    "accordion": "The bellows open and close.",
    "strings": "The bows move together.",
}
_LOOK = {
    "woman": ("A woman in a simple dark dress", "the woman in a simple dark dress"),
    "man": ("A man in a dark shirt", "the man in a dark shirt"),
    "singer": ("A singer in dark clothes", "the singer in dark clothes"),
    "players": ("Musicians in dark clothes", "the musicians in dark clothes"),
}


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _article(name: str) -> str:
    return "an" if name[:1].lower() in "aeiou" else "a"


def _section_kind(section: str) -> str:
    name = section.lower()
    if any(word in name for word in ("intro", "интро")):
        return "intro"
    if any(word in name for word in ("outro", "аутро")):
        return "outro"
    if any(word in name for word in ("pre-chorus", "prechorus", "предприпев")):
        return "prechorus"
    if any(word in name for word in ("chorus", "припев", "hook", "хук")):
        return "chorus"
    if any(word in name for word in ("bridge", "бридж")):
        return "bridge"
    if any(word in name for word in ("verse", "куплет")):
        return "verse"
    return ""


def _caption(title: str, style: str) -> str:
    """The style line, never a file name like gypsy-test-1."""
    text = " ".join((style or "").split())
    if text:
        return text
    name = " ".join((title or "").split())
    lowered = name.lower()
    if (
        "," in name
        or "vocal" in lowered
        or any(key in lowered for key, _place in _PLACES)
    ):
        return name
    return ""


def _found_instruments(text: str) -> list[str]:
    found = [name for name in _INSTRUMENTS if _has_word(text, name)]
    if any(name.endswith(" guitar") for name in found):
        found = [name for name in found if name != "guitar"]
    if any(name.endswith(" bass") and name != "bass" for name in found):
        found = [name for name in found if name != "bass"]
    return found


def _who_kind(text: str) -> str:
    if _has_word(text, "female") or _has_word(text, "woman") or _has_word(text, "girl"):
        return "woman"
    if _has_word(text, "male") or _has_word(text, "man") or _has_word(text, "boy"):
        return "man"
    if _has_word(text, "instrumental") and "vocal" not in text:
        return "players"
    return "singer"


def _where(text: str) -> str:
    for key, phrase in _PLACES:
        if _has_word(text, key):
            return phrase
    return _DEFAULT_WHERE


def _light_phrase(text: str) -> str:
    if re.search(r"\b(dark|night|noir)\b", text):
        return "Low warm light"
    if re.search(r"\b(bright|daylight|sunny)\b", text):
        return "Clear daylight"
    if re.search(r"\bmelanchol", text) or re.search(r"\b(sad|somber)\b", text):
        return "Cool dim light"
    if re.search(r"\b(passionate|warm|romantic)\b", text):
        return "Warm amber light"
    return "Soft warm light"


def _visual_world(caption: str, hint: str = "") -> dict:
    """Place, singer, and instruments. The caption is never copied into a shot."""
    described = " ".join((caption or "").lower().split())
    extra = "" if described else " ".join((hint or "").lower().split())
    text = f"{described} {extra}".strip()
    return {
        "where": _where(text),
        "who_kind": _who_kind(text),
        "light": _light_phrase(text),
        "instruments": _found_instruments(text),
    }


def _who_phrases(kind: str, first: bool) -> tuple[str, str]:
    start, mid = _LOOK[kind]
    if not first:
        start = (
            "The musicians in dark clothes" if kind == "players" else "The" + start[1:]
        )
    return start, mid


def _focus_fill(name: str) -> str:
    if not name:
        return ""
    action = _PLAYING[name]
    if name == "strings":
        return f"The strings fill the foreground. {action}"
    return f"{_article(name).capitalize()} {name} fills the foreground. {action}"


def _instrument_motion(name: str) -> str:
    """Name the instrument in the same sentence as what it is doing."""
    if not name:
        return ""
    action = _PLAYING[name]
    subject = "the strings" if name == "strings" else f"{_article(name)} {name}"
    return f"On {subject}, {action[0].lower()}{action[1:]}"


def _sentences(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def _fit(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= PROMPT_LIMIT:
        return cleaned
    cut = cleaned[:PROMPT_LIMIT].rsplit(".", 1)[0].strip()
    return f"{cut}." if cut else cleaned[:PROMPT_LIMIT].rstrip()


def _with_energy(text: str, energy: str) -> str:
    if energy == "loud":
        text = f"{text} The playing is full."
    elif (
        energy == "quiet" and "still" not in text.lower() and "slow" not in text.lower()
    ):
        text = f"{text} Movement stays small and slow."
    return _fit(text)


def _picture(
    kind: str,
    slot: int,
    where: str,
    light: str,
    start: str,
    mid: str,
    name: str,
    who_kind: str,
) -> str:
    """One filmable shot. The same people and room return in every shot."""
    focus = f"{_article(name)} {name}" if name else ""
    action = _PLAYING.get(name, "")
    fill = _focus_fill(name)
    players = who_kind == "players"

    def cam(move: str) -> str:
        return f"{light}, {move}"

    if kind == "intro" and slot == 0:
        arrival = (
            f"{start} take their places."
            if players
            else f"{start} steps toward a vintage microphone."
        )
        return _sentences(
            f"Wide shot {where}, seen from the back of the room.",
            "The stage is just coming to life.",
            arrival,
            cam("and the camera drifts forward."),
        )
    if kind == "intro":
        begin = (
            f"{start} begin to play."
            if players
            else f"{start} settles at the microphone."
        )
        return _sentences(
            f"Medium shot {where}.", begin, fill, cam("with a slow push-in.")
        )
    if kind == "prechorus" and slot == 0:
        source = focus or "the instruments"
        return _sentences(
            f"The camera rises from {source} up to {mid} {where}.",
            action,
            f"{light}. The playing leans forward.",
        )
    if kind == "prechorus":
        return _sentences(
            f"Tighter shot of {mid} {where}.",
            _instrument_motion(name) or "The playing grows.",
            cam("and the frame closes in."),
        )
    if kind == "chorus" and slot == 0:
        center = (
            "The group fills the stage."
            if players
            else f"{start} is at the center, singing."
        )
        return _sentences(
            f"Wide shot of the whole group playing together {where}.",
            center,
            cam("and the camera slowly opens wider."),
        )
    if kind == "chorus" and slot == 1:
        around = (
            "The players lean into the music."
            if players
            else f"The players lean into the music around {mid}."
        )
        return _sentences(
            f"Medium-wide shot {where}.",
            around,
            _instrument_motion(name),
            cam("and the camera rises slowly."),
        )
    if kind == "chorus":
        if focus:
            move = (
                f"The camera moves across the players {where}, from {focus} to {mid}."
            )
        else:
            move = f"The camera moves across the players {where}, settling on {mid}."
        return _sentences(move, action, f"{light}.")
    if kind == "bridge" and slot == 0:
        how = f"{start} play quietly." if players else f"{start} sings quietly."
        return _sentences(
            f"Side angle {where}, the camera almost still.",
            how,
            _instrument_motion(name),
            f"{light}.",
        )
    if kind == "bridge":
        return _sentences(
            f"Close shot of {mid} {where}, the background soft.",
            _instrument_motion(name),
            f"{light}, and the camera stays locked.",
        )
    if kind == "outro" and slot == 0:
        return _sentences(
            f"The camera eases back to a wide view {where}.",
            "The players lower their hands.",
            f"{light}.",
        )
    if kind == "outro":
        leave = (
            f"{start} step back from the instruments."
            if players
            else f"{start} steps back from the microphone."
        )
        return _sentences(
            f"Wide shot {where} as the light on the stage falls.", leave, f"{light}."
        )
    if slot == 0:
        if who_kind in {"woman", "man"}:
            lead = f"{start} sings into a vintage microphone {where}."
        elif who_kind == "singer":
            lead = f"{start} stands at a vintage microphone {where}."
        else:
            lead = f"{start} play {where}."
        return _sentences(lead, fill, cam("and the camera holds nearly still."))
    if slot == 1:
        look = (
            f"Close shot {where}, looking past {focus} toward {mid}."
            if focus
            else f"Close shot of {mid} {where}."
        )
        return _sentences(look, action, cam("with a slow push-in."))
    if slot == 2:
        return _sentences(
            f"Profile of {mid} {where}.", fill, cam("with a gentle sideways drift.")
        )
    if focus:
        return _sentences(
            f"Detail of {focus} {where}.",
            action,
            cam(f"and the camera tilts up to {mid}."),
        )
    return _sentences(
        f"Medium shot of {mid} {where}.", cam("and the camera tilts up slowly.")
    )


def _lyric_cues(lyrics: str) -> list[tuple[str, str]]:
    section = ""
    cues: list[tuple[str, str]] = []
    for raw in (lyrics or "").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if line.startswith("[") and line.endswith("]") and len(line) <= 40:
            section = line[1:-1].strip().lower()
            continue
        cues.append((section, line))
    return cues


def _energy_at(levels: list[float], start: float, seconds: int) -> str:
    if not levels:
        return "mid"
    window = levels[max(0, int(start)) : max(0, int(start)) + max(1, seconds)]
    if not window:
        window = levels[-1:]
    average = sum(window) / len(window)
    if average < 0.35:
        return "quiet"
    if average > 0.72:
        return "loud"
    return "mid"


def _shot_prompt(world: dict, kind: str, energy: str, shot_index: int) -> str:
    counts = {"intro": 2, "prechorus": 2, "chorus": 3, "bridge": 2, "outro": 2}
    instruments = world["instruments"]
    name = instruments[shot_index % len(instruments)] if instruments else ""
    start, mid = _who_phrases(world["who_kind"], shot_index == 0)
    text = _picture(
        kind,
        shot_index % counts.get(kind, 4),
        world["where"],
        world["light"],
        start,
        mid,
        name,
        world["who_kind"],
    )
    return _with_energy(text, energy)


def propose_plan(
    title: str,
    lyrics: str,
    duration_sec: float,
    levels: list[float] | None = None,
    style: str = "",
) -> dict:
    """Shots that cover the song. Each line is a picture, not the lyric text."""
    if (
        not math.isfinite(duration_sec)
        or duration_sec <= 0
        or duration_sec > 6 * 60 * 60
    ):
        raise VideoJobError("bad_length")
    caption = _caption(title, style)
    world = _visual_world(caption, "" if caption else title)
    cues = _lyric_cues(lyrics)
    shots: list[dict] = []
    cursor = 0.0
    while len(shots) < MAX_SHOTS:
        seconds = cover_seconds(duration_sec - cursor)
        if seconds is None:
            break
        if cues:
            cue_index = min(len(cues) - 1, int(len(cues) * cursor / duration_sec))
            section, _line = cues[cue_index]
        else:
            section = ""
        kind = _section_kind(section)
        shots.append(
            {
                "start_sec": round(cursor, 2),
                "seconds": seconds,
                "prompt": _shot_prompt(
                    world, kind, _energy_at(levels or [], cursor, seconds), len(shots)
                ),
            }
        )
        cursor += seconds
    if not shots:
        raise VideoJobError("bad_length")
    return {"duration_sec": round(duration_sec, 2), "shots": shots}


def validate_shots(shots: list[dict], duration_ms: float | None) -> list[dict]:
    if not isinstance(shots, list) or not shots or len(shots) > MAX_SHOTS:
        raise VideoJobError("bad_plan")
    cleaned: list[dict] = []
    for shot in shots:
        if not isinstance(shot, dict):
            raise VideoJobError("bad_plan")
        try:
            seconds = int(shot.get("seconds"))
        except (TypeError, ValueError):
            raise VideoJobError("bad_length") from None
        start = shot.get("start_sec", 0)
        prompt = normalize_prompt(str(shot.get("prompt") or ""))
        if seconds not in CLIP_SECONDS:
            raise VideoJobError("bad_length")
        check_window(start, seconds, duration_ms)
        cleaned.append(
            {"start_sec": float(start), "seconds": seconds, "prompt": prompt}
        )
    cleaned.sort(key=lambda item: (item["start_sec"], item["seconds"]))
    for prev, nxt in zip(cleaned, cleaned[1:]):
        if nxt["start_sec"] < prev["start_sec"] + prev["seconds"] - 1e-6:
            raise VideoJobError("overlap")
    return cleaned


def shot_timeline(shots: list[dict], duration_sec: float) -> list[tuple[str, float]]:
    """('shot', index) or ('gap', seconds) from the start of the song through duration_sec."""
    events: list[tuple[str, float]] = []
    cursor = 0.0
    for index, shot in enumerate(shots):
        start = float(shot["start_sec"])
        if start > cursor + 0.05:
            events.append(("gap", round(start - cursor, 3)))
        events.append(("shot", float(index)))
        cursor = start + float(shot["seconds"])
    if duration_sec > cursor + 0.05:
        events.append(("gap", round(duration_sec - cursor, 3)))
    return events


def normalize_prompt(text: str) -> str:
    prompt = " ".join((text or "").split())
    if not prompt or len(prompt) > PROMPT_LIMIT:
        raise VideoJobError("bad_prompt")
    return prompt


def check_window(start_sec: float, seconds: int, duration_ms: float | None) -> None:
    if isinstance(start_sec, bool) or not isinstance(start_sec, (int, float)):
        raise VideoJobError("bad_start")
    if not math.isfinite(start_sec) or start_sec < 0 or start_sec > 6 * 60 * 60:
        raise VideoJobError("bad_start")
    if duration_ms is None:
        return
    try:
        duration = float(duration_ms)
    except (TypeError, ValueError):
        return
    if duration <= 0:
        return
    if start_sec * 1000 >= duration or (start_sec + seconds) * 1000 > duration + 250:
        raise VideoJobError("past_end")


def partial_mp4(dest: Path) -> Path:
    """A sibling mp4. A name that does not end in .mp4 hides the format from ffmpeg."""
    return dest.with_name(f"{dest.stem}.partial.mp4")


def format_cfg(value: float) -> str:
    text = f"{float(value):.1f}"
    return text[:-2] if text.endswith(".0") else text


def picture_size(width: int, height: int) -> tuple[int, int]:
    """One of the sizes the two-stage picture can draw without rounding."""
    if isinstance(width, bool) or isinstance(height, bool):
        raise VideoJobError("bad_settings")
    try:
        pair = (int(width), int(height))
    except (TypeError, ValueError):
        raise VideoJobError("bad_settings") from None
    if pair not in SIZES:
        raise VideoJobError("bad_settings")
    return pair


def video_settings(
    stage1_steps: int, stage2_steps: int, cfg_scale: float
) -> tuple[int, int, float]:
    """Denoise steps, refine steps, and guidance. Standard is 30, 3, and 3."""
    if (
        isinstance(stage1_steps, bool)
        or not isinstance(stage1_steps, int)
        or not 10 <= stage1_steps <= 50
    ):
        raise VideoJobError("bad_settings")
    if (
        isinstance(stage2_steps, bool)
        or not isinstance(stage2_steps, int)
        or not 1 <= stage2_steps <= 3
    ):
        raise VideoJobError("bad_settings")
    if isinstance(cfg_scale, bool) or not isinstance(cfg_scale, (int, float)):
        raise VideoJobError("bad_settings")
    cfg = float(cfg_scale)
    if not math.isfinite(cfg) or cfg < 1 or cfg > 8:
        raise VideoJobError("bad_settings")
    return stage1_steps, stage2_steps, cfg


def a2v_argv(
    binary: Path,
    *,
    prompt: str,
    audio: Path,
    frames: int,
    output: Path,
    seed: int,
    stage1_steps: int = STAGE1_STEPS,
    stage2_steps: int = STAGE2_STEPS,
    cfg_scale: float = CFG_SCALE,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> list[str]:
    stage1_steps, stage2_steps, cfg_scale = video_settings(
        stage1_steps, stage2_steps, cfg_scale
    )
    width, height = picture_size(width, height)
    argv = [
        str(binary),
        "a2v",
        "--prompt",
        prompt,
        "--audio",
        str(audio),
        "--audio-start",
        "0",
        "--frame-rate",
        str(FRAME_RATE),
        "--frames",
        str(frames),
        "--height",
        str(height),
        "--width",
        str(width),
        "--output",
        str(output),
        "--model",
        MODEL_ID,
        "--gemma",
        GEMMA_ID,
        "--seed",
        str(seed),
        "--stage1-steps",
        str(stage1_steps),
        "--stage2-steps",
        str(stage2_steps),
        "--cfg-scale",
        format_cfg(cfg_scale),
        "--low-ram",
    ]
    # Longer than 6 seconds, and the 1280 picture, do not fit beside the
    # music model unless the picture is denoised in pieces. Teacache stays off.
    if frames > 145 or (width, height) == (1280, 704):
        argv.extend(["--tile-frames", "2"])
    if (width, height) == (1280, 704):
        argv.extend(["--tile-spatial", "2"])
    return argv


def engine_binary() -> Path:
    return LTX_DIR / ".venv" / "bin" / "ltx-2-mlx"


def videos_root() -> Path:
    root = DATA_DIR / "videos"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _video_dir(video_id: str) -> Path:
    if not _ID.fullmatch(video_id or ""):
        raise VideoJobError("not_found")
    root = videos_root().resolve()
    path = root / video_id
    if not path.resolve().is_relative_to(root):
        raise VideoJobError("not_found")
    return path


def _tool(name: str) -> str:
    candidate = FFMPEG_BIN_DIR / (f"{name}.exe" if sys.platform == "win32" else name)
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        raise VideoJobError("ffmpeg_missing")
    return found


def _session_kwargs() -> dict:
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}


async def _kill_proc(proc: asyncio.subprocess.Process | None) -> None:
    await kill_process_tree(proc)


def _check(job: VideoJob) -> None:
    if job.cancel or job.dropped:
        raise VideoCancelled()


def _payload(job: VideoJob) -> dict:
    return {
        "id": job.id,
        "created_at": job.created_at,
        "track_id": job.track_id,
        "title": job.title,
        "prompt": job.prompt,
        "seconds": job.seconds,
        "start_sec": job.start_sec,
        "frames": job.frames,
        "seed": job.seed,
        "status": job.status,
        "error": job.error,
        "error_code": job.error_code,
        "shots": job.shots,
        "shot_index": job.shot_index,
        "shot_count": job.shot_count,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "phase": job.phase,
        "stage1_steps": job.stage1_steps,
        "stage2_steps": job.stage2_steps,
        "cfg_scale": job.cfg_scale,
        "width": job.width,
        "height": job.height,
        "worker": job.worker.model_dump() if job.worker is not None else None,
    }


def _write_json(path: Path, payload: dict) -> None:
    atomic_text(path, json.dumps(payload, allow_nan=False))


def _write(job: VideoJob) -> None:
    if job.dropped:
        return
    _write_json(_video_dir(job.id) / "video.json", _payload(job))


def public_view(payload: dict) -> dict:
    video_id = str(payload.get("id") or "")
    ready = payload.get("status") == "ready"
    return {
        "id": video_id,
        "created_at": payload.get("created_at") or "",
        "track_id": payload.get("track_id"),
        "title": payload.get("title") or "",
        "prompt": payload.get("prompt") or "",
        "seconds": payload.get("seconds"),
        "start_sec": payload.get("start_sec") or 0,
        "status": payload.get("status") or "failed",
        "error": payload.get("error") or "",
        "error_code": payload.get("error_code") or "",
        "file_url": f"/api/videos/{video_id}/file" if ready and video_id else "",
        "shots": [
            item for item in (payload.get("shots") or []) if isinstance(item, dict)
        ],
        "shot_index": _count(payload.get("shot_index")),
        "shot_count": _count(payload.get("shot_count")),
        "progress_current": _count(payload.get("progress_current")),
        "progress_total": _count(payload.get("progress_total")),
        "phase": payload.get("phase")
        if payload.get("phase") in {"download", "denoise", "mux", "starting"}
        else "",
        "stage1_steps": _count(payload.get("stage1_steps")) or STAGE1_STEPS,
        "stage2_steps": _count(payload.get("stage2_steps")) or STAGE2_STEPS,
        "cfg_scale": _cfg(payload.get("cfg_scale")),
        "width": _size_axis(payload.get("width"), payload.get("height"))[0],
        "height": _size_axis(payload.get("width"), payload.get("height"))[1],
    }


def _count(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _size_axis(width, height) -> tuple[int, int]:
    try:
        pair = (int(width), int(height))
    except (TypeError, ValueError, OverflowError):
        return WIDTH, HEIGHT
    if pair not in SIZES:
        return WIDTH, HEIGHT
    return pair


def _cfg(value) -> float:
    try:
        cfg = float(value)
    except (TypeError, ValueError):
        return CFG_SCALE
    if not math.isfinite(cfg) or cfg < 1 or cfg > 8:
        return CFG_SCALE
    return cfg


def parse_video_phase(text: str) -> tuple[str, int, int] | None:
    """The latest download or denoise bar. Tool bars are not shown to the user."""
    phase = ""
    current = 0
    total = 0
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        match = _BAR.search(line)
        if not match:
            continue
        if "Fetching" in line:
            phase, current, total = "download", int(match.group(1)), int(match.group(2))
        elif "Denoising" in line:
            phase, current, total = "denoise", int(match.group(1)), int(match.group(2))
    if not phase or total <= 0:
        return None
    return phase, min(current, total), total


def _read_meta(path: Path) -> dict | None:
    try:
        data = json.loads((path / "video.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def reconcile_interrupted(root: Path, live_id: str | None) -> None:
    """A restart kills the generator. A queued or running file is no longer running."""
    if not root.is_dir():
        return
    for path in root.iterdir():
        if not path.is_dir() or not _ID.fullmatch(path.name):
            continue
        if live_id and path.name == live_id:
            continue
        meta = _read_meta(path)
        if meta is None or meta.get("status") not in _ACTIVE:
            continue
        meta["status"] = "failed"
        meta["error_code"] = "interrupted"
        meta["error"] = ""
        _write_json(path / "video.json", meta)


def _live_id() -> str | None:
    job = _current
    if job is not None and job.status in _ACTIVE and not job.dropped:
        return job.id
    return None


def list_videos() -> list[dict]:
    root = videos_root()
    found: list[dict] = []
    for path in root.iterdir():
        if not path.is_dir() or not _ID.fullmatch(path.name):
            continue
        meta = _read_meta(path)
        if meta is None:
            continue
        found.append(public_view(meta))
    found.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return found


def get_video(video_id: str) -> dict:
    path = _video_dir(video_id)
    meta = _read_meta(path)
    if meta is None:
        raise VideoJobError("not_found")
    return public_view(meta)


def output_file(video_id: str) -> Path:
    path = _video_dir(video_id)
    meta = _read_meta(path)
    if meta is None or meta.get("status") != "ready":
        raise VideoJobError("not_found")
    dest = (path / "output.mp4").resolve()
    if dest.parent != path.resolve() or not dest.is_file():
        raise VideoJobError("not_found")
    return dest


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    cache = DATA_DIR / "models" / "ltx"
    hub = cache / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    env["HF_HOME"] = str(cache)
    env["HF_HUB_CACHE"] = str(hub)
    env["HUGGINGFACE_HUB_CACHE"] = str(hub)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONUNBUFFERED"] = "1"
    env["PATH"] = f"{FFMPEG_BIN_DIR}{os.pathsep}{env.get('PATH', '')}"
    return env


def _log_path(video_id: str) -> Path:
    return LOG_DIR / f"video_{video_id}.log"


def failure_detail(text: str) -> str:
    lines: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or "warnings.warn" in line or line.startswith("FutureWarning"):
            continue
        lines.append(line)
    for line in reversed(lines):
        head = line.split(":", 1)[0]
        if "Warning" in head:
            continue
        if _EXCEPTION_LINE.match(line):
            return line[:500]
    for line in reversed(lines):
        if line.startswith(("File ", "Traceback", "During handling")):
            continue
        if "Warning" in line:
            continue
        return line[:500]
    return ""


def _owned_identity(slot: ProcSlot, identity: WorkerIdentity) -> None:
    if slot.owner is not None:
        slot.owner.worker = identity
        _write(slot.owner)


async def _spawn(cmd: list[str], *, cwd: Path, log_name: str, slot: ProcSlot) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{log_name}.log"
    with log_path.open("w", encoding="utf-8", errors="replace") as handle:
        proc = await spawn_owned(
            cmd,
            cwd=cwd,
            receipt_path=LOG_DIR / f"{log_name}.worker.json",
            env=_child_env(),
            stdout=handle.fileno(),
            on_identity=lambda identity: _owned_identity(slot, identity),
        )
        slot.proc = proc
        try:
            return await proc.wait()
        finally:
            await _kill_proc(proc)
            if proc.stdin is not None:
                proc.stdin.close()
            slot.proc = None
            if slot.owner is not None:
                slot.owner.worker = None
                _write(slot.owner)


async def _ffmpeg(
    args: list[str], slot: ProcSlot, job: VideoJob, timeout: float
) -> None:
    _check(job)
    slot.owner = job
    proc = await spawn_owned(
        [_tool("ffmpeg"), *args],
        receipt_path=_video_dir(job.id) / "ffmpeg.worker.json",
        stdout=asyncio.subprocess.PIPE,
        on_identity=lambda identity: _owned_identity(slot, identity),
    )
    slot.proc = proc
    try:
        out = await read_owned_output(proc, max_bytes=1024 * 1024, timeout=timeout)
    except WorkerOutputError as exc:
        raise VideoJobError("mux_failed") from exc
    except TimeoutError:
        raise VideoJobError("mux_failed", "ffmpeg timed out") from None
    finally:
        await _kill_proc(proc)
        if proc.stdin is not None:
            proc.stdin.close()
        slot.proc = None
        job.worker = None
        _write(job)
    _check(job)
    if proc.returncode != 0:
        detail = out.decode("utf-8", errors="replace").strip()[-500:]
        raise VideoJobError(
            "mux_failed", detail or f"ffmpeg exited with code {proc.returncode}"
        )


async def _probe_duration(path: Path) -> float:
    proc = await spawn_process(
        _tool("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await communicate_process(proc, 30)
    try:
        return float(out.decode().strip())
    except ValueError:
        return 0.0


def _replace_partial(partial: Path, dest: Path) -> None:
    if not partial.is_file():
        raise VideoJobError("mux_failed", "output file was not written")
    partial.replace(dest)


async def _watch_video(job: VideoJob, stop: asyncio.Event) -> None:
    while not stop.is_set():
        path = _log_path(job.id)
        if path.is_file():
            try:
                text = path.read_bytes()[-8000:].decode("utf-8", errors="replace")
            except OSError:
                text = ""
            parsed = parse_video_phase(text)
            if parsed and parsed != (
                job.phase,
                job.progress_current,
                job.progress_total,
            ):
                job.phase, job.progress_current, job.progress_total = parsed
                _write(job)
        try:
            await asyncio.wait_for(stop.wait(), 2)
        except TimeoutError:
            pass


async def _generate_clip(
    job: VideoJob,
    binary: Path,
    audio: Path,
    prompt: str,
    frames: int,
    dest: Path,
    partials: list[Path],
    seed: int,
) -> None:
    partial = partial_mp4(dest)
    partials.append(partial)
    from .video_engine import (
        RenderSettings,
        render_argv,
        VideoEngineError,
        verified_cached_fingerprint,
    )

    cache = DATA_DIR / "models" / "ltx"
    try:
        if verified_cached_fingerprint(cache) is None:
            code = (
                "from pathlib import Path;from app.video_engine import verify_and_record_artifacts;verify_and_record_artifacts(Path("
                + repr(str(cache))
                + "))"
            )
            verification = await _spawn(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parents[1],
                log_name=f"video_{job.id}_verify",
                slot=job.slot,
            )
            if verification != 0:
                raise VideoJobError("model_integrity_failed")
        argv = render_argv(
            LTX_DIR,
            cache,
            RenderSettings(
                output=partial,
                prompt=prompt,
                source_audio=audio,
                frames=frames,
                seed=seed,
                stage1_steps=job.stage1_steps,
                stage2_steps=job.stage2_steps,
                cfg_scale=job.cfg_scale,
                width=job.width,
                height=job.height,
                temporal_tiles=2 if frames > 145 else 1,
                spatial_tiles=2 if job.width >= 1280 else 1,
            ),
        )
    except VideoEngineError as exc:
        raise VideoJobError(exc.code) from exc
    job.phase = "starting"
    job.progress_current = 0
    job.progress_total = 0
    _write(job)
    stop = asyncio.Event()
    watcher = asyncio.create_task(_watch_video(job, stop))
    try:
        _check(job)
        async with gpu_lease(gpu_lock, 'video_generation', 'Video generation'):
            _check(job)
            code = await _spawn(
                argv, cwd=LTX_DIR, log_name=f"video_{job.id}", slot=job.slot
            )
    finally:
        stop.set()
        await watcher
    _check(job)
    if code != 0 or not partial.is_file():
        detail = ""
        log_path = _log_path(job.id)
        if log_path.is_file():
            detail = failure_detail(
                log_path.read_text(encoding="utf-8", errors="replace")
            )
        raise VideoJobError("generate_failed", detail)
    try:
        await validate_media(
            partial, (frames - 1) / FRAME_RATE, (job.width, job.height)
        )
    except VideoMediaError as exc:
        raise VideoJobError(exc.code) from exc
    partial.replace(dest)


async def _excerpt(
    job: VideoJob,
    source: Path,
    dest: Path,
    start: float,
    seconds: int,
    partials: list[Path],
) -> None:
    partial = dest.with_name(f"{dest.stem}.partial.wav")
    partials.append(partial)
    await _ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-ss",
            str(start),
            "-t",
            str(seconds),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-f",
            "wav",
            str(partial),
        ],
        job.slot,
        job,
        180,
    )
    _replace_partial(partial, dest)
    got = await _probe_duration(dest)
    if got + 0.35 < seconds:
        raise VideoJobError("past_end")


async def _video_size(path: Path) -> tuple[int, int]:
    proc = await spawn_process(
        _tool("ffprobe"),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _err = await communicate_process(proc)
    text = out.decode().strip()
    if "x" not in text:
        return WIDTH, HEIGHT
    width, height = text.split("x", 1)
    try:
        return max(2, int(width)), max(2, int(height))
    except ValueError:
        return WIDTH, HEIGHT


def _concat_list(paths: list[Path]) -> str:
    lines = []
    for path in paths:
        name = path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{name}'")
    return "\n".join(lines) + "\n"


async def _energy_levels(path: Path) -> list[float]:
    proc = await spawn_process(
        _tool("ffmpeg"),
        "-v",
        "error",
        "-i",
        str(path),
        "-ac",
        "1",
        "-ar",
        "8000",
        "-f",
        "s16le",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    raw, _err = await communicate_process(proc)
    if not raw:
        return []
    hop = 8000
    levels: list[float] = []
    for offset in range(0, len(raw) - 1, hop * 2):
        chunk = raw[offset : offset + hop * 2]
        count = len(chunk) // 2
        if count < 100:
            break
        samples = struct.unpack_from(f"<{count}h", chunk)
        total = 0
        taken = 0
        for sample in samples[::16]:
            total += sample * sample
            taken += 1
        levels.append((total / max(1, taken)) ** 0.5)
    peak = max(levels) if levels else 0.0
    if peak <= 0:
        return []
    return [value / peak for value in levels]


async def analyze_song(track_id: int) -> dict:
    row = db.get_track(int(track_id))
    if row is None:
        raise VideoJobError("no_track")
    audio = Path(row["audio_path"])
    if not audio.is_file():
        raise VideoJobError("track_missing")
    duration = float(row["duration_ms"] or 0) / 1000
    if duration <= 0:
        duration = await _probe_duration(audio)
    levels = await _energy_levels(audio)
    plan = propose_plan(
        _track_title(row),
        str(row["lyrics"] or ""),
        duration,
        levels,
        _style_caption(row),
    )
    plan["track_id"] = int(row["id"])
    plan["title"] = _track_title(row)
    return plan


async def _run(job: VideoJob) -> None:
    directory = _video_dir(job.id)
    partials: list[Path] = []
    try:
        job.slot.owner = job
        job.status = "running"
        job.phase = "starting"
        _write(job)
        track = db.get_track(job.track_id)
        if track is None:
            raise VideoJobError("no_track")
        source_audio = Path(track["audio_path"])
        if not source_audio.is_file():
            raise VideoJobError("track_missing")
        import platform

        if sys.platform != "darwin" or platform.machine().lower() not in {
            "arm64",
            "aarch64",
        }:
            raise VideoJobError("unsupported_platform")
        binary = engine_binary()
        if not binary.is_file():
            raise VideoJobError("engine_missing")
        shots = job.shots or [
            {
                "start_sec": job.start_sec,
                "seconds": job.seconds,
                "prompt": job.prompt,
            }
        ]
        job.shot_count = len(shots)
        duration = float(track["duration_ms"] or 0) / 1000
        if duration <= 0:
            duration = await _probe_duration(source_audio)
        duration = timeline_duration(shots, duration)

        clips: list[Path] = []
        for index, shot in enumerate(shots):
            _check(job)
            job.shot_index = index + 1
            _write(job)
            excerpt = directory / f"audio_{index:02d}.wav"
            await _excerpt(
                job,
                source_audio,
                excerpt,
                float(shot["start_sec"]),
                int(shot["seconds"]),
                partials,
            )
            clip = directory / f"shot_{index:02d}.mp4"
            await _generate_clip(
                job,
                binary,
                excerpt,
                str(shot["prompt"]),
                frames_for_seconds(int(shot["seconds"])),
                clip,
                partials,
                (job.seed + index) % (2**31),
            )
            clips.append(clip)

        output = directory / "output.mp4"
        output_partial = partial_mp4(output)
        partials.append(output_partial)
        job.phase = "mux"
        job.progress_current = 0
        job.progress_total = 0
        _write(job)
        if len(clips) == 1 and not job.shots:
            await _ffmpeg(
                [
                    "-y",
                    "-i",
                    str(clips[0]),
                    "-i",
                    str(directory / "audio_00.wav"),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-f",
                    "mp4",
                    str(output_partial),
                ],
                job.slot,
                job,
                180,
            )
            _replace_partial(output_partial, output)
        else:
            width, height = await _video_size(clips[0])
            pieces: list[Path] = []
            for kind, value in shot_timeline(shots, duration):
                _check(job)
                if kind == "shot":
                    index = int(value)
                    norm = directory / f"norm_{index:02d}.mp4"
                    norm_partial = partial_mp4(norm)
                    partials.append(norm_partial)
                    await _ffmpeg(
                        [
                            "-y",
                            "-i",
                            str(clips[index]),
                            "-t",
                            str(shots[index]["seconds"]),
                            "-an",
                            "-vf",
                            f"scale={width}:{height},fps={FRAME_RATE}",
                            "-c:v",
                            "libx264",
                            "-pix_fmt",
                            "yuv420p",
                            "-f",
                            "mp4",
                            str(norm_partial),
                        ],
                        job.slot,
                        job,
                        300,
                    )
                    _replace_partial(norm_partial, norm)
                    pieces.append(norm)
                else:
                    gap = directory / f"gap_{len(pieces):02d}.mp4"
                    gap_partial = partial_mp4(gap)
                    partials.append(gap_partial)
                    await _ffmpeg(
                        [
                            "-y",
                            "-f",
                            "lavfi",
                            "-i",
                            f"color=c=black:s={width}x{height}:r={FRAME_RATE}:d={value}",
                            "-t",
                            str(value),
                            "-an",
                            "-c:v",
                            "libx264",
                            "-pix_fmt",
                            "yuv420p",
                            "-f",
                            "mp4",
                            str(gap_partial),
                        ],
                        job.slot,
                        job,
                        180,
                    )
                    _replace_partial(gap_partial, gap)
                    pieces.append(gap)
            listing = directory / "concat.txt"
            listing.write_text(_concat_list(pieces), encoding="utf-8")
            joined = directory / "joined.mp4"
            joined_partial = partial_mp4(joined)
            partials.append(joined_partial)
            await _ffmpeg(
                [
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-c",
                    "copy",
                    "-f",
                    "mp4",
                    str(joined_partial),
                ],
                job.slot,
                job,
                300,
            )
            _replace_partial(joined_partial, joined)
            await _ffmpeg(
                [
                    "-y",
                    "-i",
                    str(joined),
                    "-i",
                    str(source_audio),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-f",
                    "mp4",
                    str(output_partial),
                ],
                job.slot,
                job,
                300,
            )
            _replace_partial(output_partial, output)
        try:
            await validate_media(
                output,
                duration if job.shots else job.seconds,
                (job.width, job.height),
                require_audio=True,
            )
        except VideoMediaError as exc:
            raise VideoJobError(exc.code) from exc
        job.status = "ready"
        job.phase = ""
        job.error = ""
        job.error_code = ""
        _write(job)
    except asyncio.CancelledError:
        await _kill_proc(job.slot.proc)
        job.status = "cancelled"
        job.error_code = "cancelled"
        job.error = ""
        _write(job)
        raise
    except VideoCancelled:
        job.status = "cancelled"
        job.error_code = "cancelled"
        job.error = ""
        _write(job)
    except VideoJobError as exc:
        logger.warning(
            "Video job %s failed (%s): %s", job.id, exc.code, exc.detail, exc_info=True
        )
        job.status = "failed"
        job.error_code = exc.code
        job.error = ""
        _write(job)
    except Exception:
        logger.exception("Video job %s failed", job.id)
        job.status = "failed"
        job.error_code = "unknown"
        job.error = "Video processing failed. Check the backend log."
        _write(job)
    finally:
        for partial in partials:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
        if job.status in _ACTIVE:
            job.status = "failed"
            job.error_code = job.error_code or "interrupted"
            _write(job)
        global _current
        if _current is job:
            _current = None


def _track_title(row) -> str:
    title = str(row["title"] or "").strip()
    return title or f"Track {row['id']}"


def _style_caption(row) -> str:
    """The generation style, such as gypsy jazz and violin. Not the track title."""
    try:
        params = json.loads(row["params_json"] or "{}")
    except (TypeError, json.JSONDecodeError, KeyError):
        return ""
    if not isinstance(params, dict):
        return ""
    for key in ("prompt", "tags", "description", "caption"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


async def start_video(
    track_id: int,
    prompt: str,
    seconds: int,
    start_sec: float,
    shots: list[dict] | None = None,
    stage1_steps: int = STAGE1_STEPS,
    stage2_steps: int = STAGE2_STEPS,
    cfg_scale: float = CFG_SCALE,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> dict:
    global _current
    row = db.get_track(int(track_id))
    if row is None:
        raise VideoJobError("no_track")
    audio = Path(row["audio_path"])
    if not audio.is_file():
        raise VideoJobError("track_missing")
    if shots:
        cleaned_shots = validate_shots(shots, row["duration_ms"])
        cleaned = cleaned_shots[0]["prompt"]
        seconds = int(cleaned_shots[0]["seconds"])
        start_sec = float(cleaned_shots[0]["start_sec"])
    else:
        cleaned = normalize_prompt(prompt)
        if seconds not in CLIP_SECONDS:
            raise VideoJobError("bad_length")
        check_window(start_sec, seconds, None)
        check_window(float(start_sec), seconds, row["duration_ms"])
        cleaned_shots = []
    frames = frames_for_seconds(seconds)
    stage1_steps, stage2_steps, cfg_scale = video_settings(
        stage1_steps, stage2_steps, cfg_scale
    )
    width, height = picture_size(width, height)
    if not engine_binary().is_file():
        raise VideoJobError("engine_missing")
    _tool("ffmpeg")

    from .resource_admission import admission_lock, native_work_inflight
    from .ace_jobs import work_busy as ace_busy
    from .work_busy import other_work_busy

    async with admission_lock, _gate:
        from .module_jobs import work_busy as setup_busy
        if (
            setup_busy()
            or
            native_work_inflight()
            or ace_busy()
            or await other_work_busy()
            or work_busy()
        ):
            raise VideoJobError("busy")
        if _current is not None and _current.status in _ACTIVE and not _current.dropped:
            raise VideoJobError("busy")
        job = VideoJob(
            id=uuid.uuid4().hex,
            track_id=int(row["id"]),
            title=_track_title(row),
            prompt=cleaned,
            seconds=sum(int(shot["seconds"]) for shot in cleaned_shots)
            if cleaned_shots
            else seconds,
            start_sec=float(start_sec),
            frames=frames,
            seed=secrets.randbelow(2**31),
            created_at=datetime.now(timezone.utc).isoformat(),
            shots=cleaned_shots,
            shot_count=len(cleaned_shots),
            stage1_steps=stage1_steps,
            stage2_steps=stage2_steps,
            cfg_scale=cfg_scale,
            width=width,
            height=height,
        )
        _current = job
        try:
            _write(job)
        except OSError as exc:
            logger.exception("Could not persist a new video job")
            _current = None
            raise VideoJobError("unknown") from exc
        job.task = asyncio.create_task(_run(job))
    return public_view(_payload(job))


async def _cancel_job(job: VideoJob) -> None:
    global _current
    job.cancel = True
    try:
        await cancel_and_wait(job.task)
    finally:
        await _kill_proc(job.slot.proc)
        job.slot.proc = None
        if job.status in _ACTIVE:
            job.status = "cancelled"
            job.error_code = "cancelled"
            job.error = ""
            _write(job)
        if _current is job:
            _current = None


async def cancel_video(video_id: str) -> dict:
    path = _video_dir(video_id)
    job = _current
    if job is not None and job.id == video_id and job.status in _ACTIVE:
        await _cancel_job(job)
    meta = _read_meta(path)
    if meta is None:
        raise VideoJobError("not_found")
    return public_view(meta)


async def delete_video(video_id: str) -> None:
    path = _video_dir(video_id)
    if not path.is_dir():
        raise VideoJobError("not_found")
    if video_id in _unverified:
        raise VideoJobError("worker_identity_unverified")
    job = _current
    if job is not None and job.id == video_id:
        job.dropped = True
        await _cancel_job(job)
    shutil.rmtree(path, ignore_errors=True)


async def shutdown() -> None:
    """Drain the current video task and all subprocess cleanup."""
    job = _current
    from .video_render import shutdown as shutdown_projects, request_shutdown

    request_shutdown()
    if job is not None:
        await _cancel_job(job)
    await shutdown_projects()


def workers_unverified() -> bool:
    """Unknown worker ownership remains a gate even for CPU-only exports."""
    return bool(_unverified)


def work_busy() -> bool:
    from .video_render import work_busy as project_work_busy

    return (
        bool(_unverified)
        or (_current is not None and _current.status in _ACTIVE)
        or project_work_busy()
    )


async def recover() -> None:
    """Startup recovery owns process reconciliation; GET requests never write."""
    from .video_render import recover as recover_projects

    root = videos_root()
    for path in root.iterdir():
        if not _ID.fullmatch(path.name) or not path.is_dir():
            continue
        try:
            directory = _video_dir(path.name)
        except VideoJobError:
            continue
        meta = _read_meta(directory)
        if (
            meta is None
            or meta.get("status") not in _ACTIVE
            and meta.get("worker") is None
        ):
            continue
        worker = meta.get("worker")
        if worker is not None:
            from pydantic import ValidationError

            try:
                identity = WorkerIdentity.model_validate(worker)
            except ValidationError:
                _unverified.add(path.name)
                meta["status"] = "failed"
                meta["error_code"] = "worker_identity_unverified"
                _write_json(directory / "video.json", meta)
                continue
            if not await terminate_verified(identity):
                _unverified.add(path.name)
                meta["status"] = "failed"
                meta["error_code"] = "worker_identity_unverified"
                _write_json(directory / "video.json", meta)
                continue
            _unverified.discard(path.name)
        output = directory / "output.mp4"
        ready = False
        try:
            expected = float(meta.get("seconds") or 0)
            shots = meta.get("shots")
            if isinstance(shots, list) and shots:
                row = db.get_track(int(meta["track_id"]))
                duration = float(row["duration_ms"] or 0) / 1000 if row else expected
                expected = timeline_duration(shots, duration)
            await validate_media(
                output,
                expected,
                picture_size(meta.get("width", WIDTH), meta.get("height", HEIGHT)),
                require_audio=True,
            )
            ready = True
        except (VideoMediaError, OSError, ValueError, TypeError, VideoJobError):
            pass
        meta.update(
            status="ready" if ready else "failed",
            error_code="" if ready else "interrupted",
            error="",
            worker=None,
            phase="",
        )
        _write_json(directory / "video.json", meta)
    await recover_projects()
