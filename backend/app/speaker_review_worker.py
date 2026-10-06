"""Small stdlib protocol shared with the optional offline CPU encoder process."""
from __future__ import annotations
import array
from dataclasses import dataclass
import math
from pathlib import Path
import re
import sys
import wave

FAMILY="speechbrain-ecapa-voxceleb"
PIPELINE="ecapa-voxceleb-16k-fbank80-sentence-mean-l2-v1"
WEIGHTS_SHA256="0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2"
MAX_ANALYSIS_MS=30000


def _text(value:object,maximum:int=100)->str:
    if not isinstance(value,str) or not 1<=len(value)<=maximum:
        raise ValueError("speaker_output_invalid")
    return value


@dataclass(frozen=True)
class EncoderIdentity:
    weights_sha256:str
    speechbrain_version:str
    torch_version:str
    torchaudio_version:str
    family:str=FAMILY
    pipeline:str=PIPELINE

    def __post_init__(self)->None:
        if re.fullmatch(r"[0-9a-f]{64}",self.weights_sha256) is None or self.family!=FAMILY or self.pipeline!=PIPELINE:
            raise ValueError("speaker_output_invalid")
        for value in (self.speechbrain_version,self.torch_version,self.torchaudio_version):_text(value)

    def to_json(self)->dict[str,object]:
        return {"family":self.family,"pipeline":self.pipeline,"weights_sha256":self.weights_sha256,
                "speechbrain_version":self.speechbrain_version,"torch_version":self.torch_version,"torchaudio_version":self.torchaudio_version}


def parse_identity(value:object)->EncoderIdentity:
    if not isinstance(value,dict) or set(value)!={"family","pipeline","weights_sha256","speechbrain_version","torch_version","torchaudio_version"}:
        raise ValueError("speaker_output_invalid")
    return EncoderIdentity(weights_sha256=_text(value["weights_sha256"]),speechbrain_version=_text(value["speechbrain_version"]),
        torch_version=_text(value["torch_version"]),torchaudio_version=_text(value["torchaudio_version"]),
        family=_text(value["family"]),pipeline=_text(value["pipeline"]))


def vector(value:object)->list[float]:
    if not isinstance(value,(list,tuple)) or len(value)!=192:
        raise ValueError("speaker_output_invalid")
    result:list[float]=[]
    for item in value:
        if isinstance(item,bool) or not isinstance(item,(int,float)) or not math.isfinite(item) or abs(item)>1e12:
            raise ValueError("speaker_output_invalid")
        result.append(float(item))
    norm=math.hypot(*result)
    if norm<1e-12:raise ValueError("speaker_output_invalid")
    return [item/norm for item in result]


@dataclass(frozen=True)
class Embedding:
    encoder:EncoderIdentity
    values:list[float]
    duration_ms:int
    active_ms:int

    def __post_init__(self)->None:
        vector(self.values)
        if not 3000<=self.duration_ms<=MAX_ANALYSIS_MS or not 2000<=self.active_ms<=self.duration_ms:
            raise ValueError("speaker_audio_insufficient")

    def to_json(self)->dict[str,object]:
        return {"encoder":self.encoder.to_json(),"values":self.values,"duration_ms":self.duration_ms,"active_ms":self.active_ms}


def parse_embedding(value:object)->Embedding:
    if not isinstance(value,dict) or set(value)!={"encoder","values","duration_ms","active_ms"}:
        raise ValueError("speaker_output_invalid")
    duration,active=value["duration_ms"],value["active_ms"]
    if type(duration) is not int or type(active) is not int:
        raise ValueError("speaker_output_invalid")
    return Embedding(parse_identity(value["encoder"]),vector(value["values"]),duration,active)


def compare(reference:Embedding,passage:Embedding)->float:
    if reference.encoder!=passage.encoder:
        raise ValueError("speaker_encoder_changed")
    return max(-1.0,min(1.0,sum(a*b for a,b in zip(vector(reference.values),vector(passage.values),strict=True))))


def read_pcm(path:Path)->tuple[list[float],int,int]:
    """Activity is an amplitude screen, not a speech detector or quality verdict."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>16000*2*30+4096:
            raise ValueError("speaker_audio_invalid")
        with wave.open(str(path),"rb") as audio:
            if (audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getcomptype())!=(16000,1,2,"NONE"):
                raise ValueError("speaker_audio_invalid")
            frames=audio.getnframes()
            raw=audio.readframes(frames)
            if len(raw)!=frames*2:raise ValueError("speaker_audio_invalid")
        duration=frames*1000//16000
        samples=array.array("h",raw)
        if sys.byteorder!="little":samples.byteswap()
        values=[item/32768.0 for item in samples]
        active=sum(abs(item)>=0.005 for item in values)*1000//16000
        if not 3000<=duration<=MAX_ANALYSIS_MS or active<2000:
            raise ValueError("speaker_audio_insufficient")
        return values,duration,active
    except (OSError,EOFError,wave.Error) as error:
        raise ValueError("speaker_audio_invalid") from error
