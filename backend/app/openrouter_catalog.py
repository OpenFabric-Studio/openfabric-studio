"""A small curated selection intersected with bounded live capabilities."""
from __future__ import annotations
import base64
import binascii
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from .atomic_files import document_lock, write_object
from .config import DATA_DIR
from .contracts import JsonObject
from .openrouter_contracts import (MediaKind, OpenRouterCatalog, OpenRouterModel, OpenRouterPrice, OpenRouterQuote, OpenRouterQuoteRequest, OpenRouterSpeechRequest, OpenRouterVideoRequest, OpenRouterMusicRequest)
from .openrouter_errors import OpenRouterError
from . import openrouter_settings as settings

CATALOG_PATH = DATA_DIR/'providers'/'openrouter-catalog.json'
_VIDEO = {'google/veo-3.1','google/veo-3.1-fast'}
_SPEECH = {'hexgrad/kokoro-82m','mistralai/voxtral-mini-tts-2603','fish-audio/s2.1-pro'}
# Published model pages explicitly price per song, while token fields are zero.
_MUSIC = {'google/lyria-3-clip-preview':0.04,'google/lyria-3-pro-preview':0.08}
_json: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_LOCK = threading.RLock()
_QUOTES: dict[str, tuple[OpenRouterQuote, OpenRouterQuoteRequest]] = {}

class _Wire(BaseModel):
    model_config = ConfigDict(extra='ignore',allow_inf_nan=False,strict=True)
class _Video(_Wire):
    id: str = Field(max_length=200)
    name: str = Field(max_length=160)
    supported_durations: list[int] = Field(max_length=60)
    supported_resolutions: list[str] = Field(max_length=20)
    supported_aspect_ratios: list[str] = Field(max_length=20)
    supported_sizes: list[str] = Field(max_length=40)
    supported_frame_images: list[Literal['first_frame','last_frame']] = Field(default_factory=list,max_length=2)
    generate_audio: bool | None = None
    seed: bool | None = None
    pricing_skus: dict[str,str] = Field(max_length=32)
class _Architecture(_Wire):
    output_modalities: list[str] = Field(max_length=10)
class _Audio(_Wire):
    id: str = Field(max_length=200)
    name: str = Field(max_length=160)
    architecture: _Architecture
    supported_voices: list[str] | None = Field(default=None,max_length=256)
    supported_parameters: list[str] = Field(default_factory=list,max_length=80)
    pricing: dict[str,str] = Field(max_length=16)
class _Endpoint(_Wire):
    model_id: str = Field(max_length=200)
    supports_voice_cloning: bool = False
    pricing: dict[str,str | float] = Field(default_factory=dict,max_length=16)
class _EndpointData(_Wire):
    endpoints: list[_Endpoint] = Field(max_length=64)
class _Endpoints(_Wire):
    data: _EndpointData


def now() -> str:
    return datetime.now(timezone.utc).isoformat()

def digest(value: BaseModel | list[OpenRouterModel]) -> str:
    text = value.model_dump_json() if isinstance(value,BaseModel) else json.dumps([row.model_dump() for row in value],sort_keys=True,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(text.encode()).hexdigest()

def _rows(value: object) -> list[JsonObject]:
    parsed = _json.validate_python(value)
    rows = parsed.get('data')
    if not isinstance(rows,list) or len(rows)>1000:
        raise ValueError('catalog_invalid')
    return [row for row in rows if isinstance(row,dict)]

def _fingerprint(model: OpenRouterModel) -> OpenRouterModel:
    return model.model_copy(update={'fingerprint':digest(model)})

def _rate(value: str | float) -> float:
    rate = float(value)
    # Validate through the actual public finite bounded price contract.
    return OpenRouterPrice(unit='character',rate_usd=rate,source='live_catalog').rate_usd

def normalize_video(value: object) -> list[OpenRouterModel]:
    result: list[OpenRouterModel] = []
    for row in _rows(value):
        if row.get('id') not in _VIDEO:
            continue
        wire = _Video.model_validate(row)
        if any(type(duration) is not int or not 1<=duration<=60 for duration in wire.supported_durations) or not wire.supported_durations or not wire.supported_sizes:
            raise ValueError('catalog_invalid')
        if any(re.fullmatch(r'[1-9][0-9]{1,4}x[1-9][0-9]{1,4}',size) is None for size in wire.supported_sizes):
            raise ValueError('catalog_invalid')
        if any(re.fullmatch(r'[1-9][0-9]{0,3}:[1-9][0-9]{0,3}',ratio) is None for ratio in wire.supported_aspect_ratios):
            raise ValueError('catalog_invalid')
        prices: list[OpenRouterPrice] = []
        for sku, raw in wire.pricing_skus.items():
            matched = re.fullmatch(r'duration_seconds_(with|without)_audio(?:_(720p|1080p|4k))?',sku)
            if matched is not None:
                resolution=matched[2]
                prices.append(OpenRouterPrice(unit='second',rate_usd=_rate(raw),source='live_catalog',generate_audio=matched[1]=='with',resolution='4K' if resolution=='4k' else resolution))
        if not prices:
            raise ValueError('catalog_price_unknown')
        result.append(_fingerprint(OpenRouterModel(id=wire.id,name=wire.name,kind='video',fingerprint='0'*64,prices=prices,
            supported_durations=wire.supported_durations,supported_resolutions=wire.supported_resolutions,supported_aspect_ratios=wire.supported_aspect_ratios,
            supported_sizes=wire.supported_sizes,supported_frame_images=wire.supported_frame_images,supports_generate_audio=wire.generate_audio is True,supports_seed=wire.seed is True)))
    return result

def normalize_audio(value: object, kind: Literal['speech','music']) -> list[OpenRouterModel]:
    result: list[OpenRouterModel] = []
    for row in _rows(value):
        if row.get('id') not in (_SPEECH if kind=='speech' else _MUSIC):
            continue
        wire=_Audio.model_validate(row)
        if ('speech' if kind=='speech' else 'audio') not in wire.architecture.output_modalities:
            raise ValueError('catalog_invalid')
        # Even ignored token pricing is validated: NaN is never a harmless price.
        for raw in wire.pricing.values(): _rate(raw)
        rate=_rate(wire.pricing['prompt']) if kind=='speech' else _MUSIC[wire.id]
        voices=wire.supported_voices or []
        if any(not voice or len(voice)>160 or any(ord(char)<32 for char in voice) for voice in voices):
            raise ValueError('catalog_invalid')
        result.append(_fingerprint(OpenRouterModel(id=wire.id,name=wire.name,kind=kind,fingerprint='0'*64,
            prices=[OpenRouterPrice(unit=('utf8_byte' if wire.id=='fish-audio/s2.1-pro' else 'character') if kind=='speech' else 'request',rate_usd=rate,source='live_catalog' if kind=='speech' else 'published_model_page')],
            supported_voices=voices,supports_seed='seed' in wire.supported_parameters,warnings=[] if kind=='speech' else ['experimental_music','fixed_song_estimate_from_published_page'])))
    return result

def apply_endpoints(model: OpenRouterModel, value: object) -> OpenRouterModel:
    data=_Endpoints.model_validate(value)
    if any(row.model_id!=model.id for row in data.data.endpoints):
        raise ValueError('catalog_invalid')
    rates=[_rate(row.pricing['prompt']) for row in data.data.endpoints if 'prompt' in row.pricing]
    prices=[price.model_copy(update={'rate_usd':max(rates)}) for price in model.prices] if rates else model.prices
    return _fingerprint(model.model_copy(update={'fingerprint':'0'*64,'prices':prices,'supports_voice_cloning':any(row.supports_voice_cloning for row in data.data.endpoints)}))

def publish(models: list[OpenRouterModel]) -> OpenRouterCatalog:
    if len({model.id for model in models})!=len(models): raise ValueError('catalog_invalid')
    result=OpenRouterCatalog(models=models,fingerprint=digest(models),fetched_at=now(),expires_at=time.time()+900)
    with document_lock(CATALOG_PATH):
        if CATALOG_PATH.is_symlink() or CATALOG_PATH.parent.is_symlink(): raise OpenRouterError('provider_storage_unavailable')
        CATALOG_PATH.parent.mkdir(parents=True,exist_ok=True)
        write_object(CATALOG_PATH,_json.validate_json(result.model_dump_json()))
    return result

def load() -> OpenRouterCatalog:
    with document_lock(CATALOG_PATH):
        if CATALOG_PATH.is_symlink() or CATALOG_PATH.parent.is_symlink(): raise OpenRouterError('provider_storage_unavailable')
        if not CATALOG_PATH.exists():
            return OpenRouterCatalog(models=[],fingerprint=digest([]),fetched_at='',expires_at=0)
        try:
            if CATALOG_PATH.stat().st_size>1024*1024: raise ValueError()
            return OpenRouterCatalog.model_validate_json(CATALOG_PATH.read_bytes())
        except (OSError,ValidationError,ValueError) as error: raise OpenRouterError('catalog_invalid') from error

def require_model(model_id: str, kind: MediaKind) -> OpenRouterModel:
    catalog=load()
    if catalog.expires_at<time.time(): raise OpenRouterError('catalog_stale',409)
    model=next((row for row in catalog.models if row.id==model_id and row.kind==kind),None)
    if model is None: raise OpenRouterError('model_unavailable',409)
    return model

def reference(value: str | None, kind: Literal['image','audio']) -> bytes:
    if value is None: return b''
    matched=re.fullmatch(r'data:(image/(?:png|jpeg|webp)|audio/(?:wav|mpeg|mp3|flac));base64,([A-Za-z0-9+/=]+)',value)
    if matched is None or not matched[1].startswith(kind+'/'): raise OpenRouterError('reference_invalid',422)
    try: data=base64.b64decode(matched[2],validate=True)
    except binascii.Error as error: raise OpenRouterError('reference_invalid',422) from error
    if not 1<=len(data)<=15*1024*1024: raise OpenRouterError('reference_too_large',422)
    return data

def video_parameters(body: OpenRouterVideoRequest) -> OpenRouterQuoteRequest:
    data=reference(body.reference_image,'image')
    return OpenRouterQuoteRequest(kind='video',model_id=body.model,text=body.prompt,duration_seconds=body.duration,size=body.size,resolution=body.resolution,aspect_ratio=body.aspect_ratio,generate_audio=body.generate_audio,seed=body.seed,reference_bytes=len(data),reference_sha256=hashlib.sha256(data).hexdigest() if data else None)

def speech_parameters(body: OpenRouterSpeechRequest) -> OpenRouterQuoteRequest:
    data=reference(body.reference_audio,'audio')
    return OpenRouterQuoteRequest(kind='speech',model_id=body.model,text=body.input,voice=body.voice,reference_bytes=len(data),reference_sha256=hashlib.sha256(data).hexdigest() if data else None,reference_transcript=body.reference_transcript)

def music_parameters(body: OpenRouterMusicRequest) -> OpenRouterQuoteRequest:
    return OpenRouterQuoteRequest(kind='music',model_id=body.model,text=body.prompt,seed=body.seed)

def _estimate(body: OpenRouterQuoteRequest) -> OpenRouterQuote:
    model=require_model(body.model_id,body.kind)
    if not body.text.strip() or len(body.text)>model.input_character_limit: raise OpenRouterError('input_too_large',422)
    transfers: list[Literal['prompt','text','reference_image','reference_audio']]=['text' if body.kind=='speech' else 'prompt']
    if body.kind=='video':
        if body.duration_seconds not in model.supported_durations or body.size not in model.supported_sizes or body.resolution is not None and body.resolution not in model.supported_resolutions or body.aspect_ratio is not None and body.aspect_ratio not in model.supported_aspect_ratios:
            raise OpenRouterError('video_parameters_unsupported',422)
        if body.generate_audio and not model.supports_generate_audio or body.seed is not None and not model.supports_seed: raise OpenRouterError('video_parameters_unsupported',422)
        if body.reference_bytes and 'first_frame' not in model.supported_frame_images: raise OpenRouterError('reference_unsupported',422)
        if body.size is not None and body.aspect_ratio is not None:
            width,height=(int(item) for item in body.size.split('x'))
            aspect_width,aspect_height=(int(item) for item in body.aspect_ratio.split(':'))
            if abs(width/height-aspect_width/aspect_height)>0.002: raise OpenRouterError('video_parameters_unsupported',422)
        resolution=body.resolution
        if resolution is None and body.size is not None:
            minimum=min(int(item) for item in body.size.split('x'))
            resolution={720:'720p',1080:'1080p',2160:'4K'}.get(minimum)
        specific=[price for price in model.prices if price.generate_audio==body.generate_audio and price.resolution==resolution]
        prices=specific or [price for price in model.prices if price.generate_audio==body.generate_audio and price.resolution is None]
        if not prices or body.duration_seconds is None: raise OpenRouterError('catalog_price_unknown',409)
        estimate=max(price.rate_usd for price in prices)*body.duration_seconds
        if body.reference_bytes: transfers.append('reference_image')
    elif body.kind=='speech':
        if body.reference_bytes:
            if not model.supports_voice_cloning: raise OpenRouterError('reference_unsupported',422)
            if body.voice is not None: raise OpenRouterError('speech_parameters_unsupported',422)
            transfers.append('reference_audio')
        elif body.voice not in model.supported_voices: raise OpenRouterError('voice_unsupported',422)
        estimate=max(price.rate_usd*(len(body.text.encode('utf-8')) if price.unit=='utf8_byte' else len(body.text)) for price in model.prices)
    else:
        if body.reference_bytes or body.voice or body.generate_audio or body.duration_seconds is not None: raise OpenRouterError('music_parameters_unsupported',422)
        if body.seed is not None and not model.supports_seed: raise OpenRouterError('music_parameters_unsupported',422)
        estimate=max(price.rate_usd for price in model.prices)
    limit=settings.load().estimate_limit_usd
    if limit is not None and estimate>limit: raise OpenRouterError('estimate_limit_exceeded',409)
    result=OpenRouterQuote(id=uuid.uuid4().hex,kind=body.kind,model_id=model.id,model_fingerprint=model.fingerprint,request_fingerprint=digest(body),estimated_usd=estimate,expires_at=time.time()+300,transfers=transfers,warnings=model.warnings)
    return result

def quote(body: OpenRouterQuoteRequest) -> OpenRouterQuote:
    result=_estimate(body)
    with _LOCK:
        for key, (old,_body) in list(_QUOTES.items()):
            if old.expires_at<time.time(): del _QUOTES[key]
        if len(_QUOTES)>=1000: raise OpenRouterError('quote_limit',429)
        _QUOTES[result.id]=(result,body)
    return result

def consume_quote(identifier: str) -> None:
    # The durable receipt owns the fingerprint after begin_submit succeeds.
    # Removing admission also makes every paid replay fail closed.
    with _LOCK: _QUOTES.pop(identifier,None)

def validate_quote(identifier: str, body: OpenRouterQuoteRequest) -> OpenRouterQuote:
    with _LOCK: saved=_QUOTES.get(identifier)
    if saved is None or saved[0].expires_at<time.time(): raise OpenRouterError('quote_stale',409)
    result=saved[0]
    model=require_model(body.model_id,body.kind)
    if result.model_fingerprint!=model.fingerprint or result.request_fingerprint!=digest(body): raise OpenRouterError('quote_mismatch',409)
    limit=settings.load().estimate_limit_usd
    if limit is not None and result.estimated_usd>limit: raise OpenRouterError('estimate_limit_exceeded',409)
    return result

def quote_video(body: OpenRouterVideoRequest) -> OpenRouterQuote: return quote(video_parameters(body))
def quote_speech(body: OpenRouterSpeechRequest) -> OpenRouterQuote: return quote(speech_parameters(body))
def estimate_speech(body: OpenRouterSpeechRequest) -> OpenRouterQuote:
    """Capability-checked cost preview; it grants no paid submit admission."""
    return _estimate(speech_parameters(body))
def quote_music(body: OpenRouterMusicRequest) -> OpenRouterQuote: return quote(music_parameters(body))

def saved_quote(identifier: str) -> OpenRouterQuote:
    with _LOCK: saved=_QUOTES.get(identifier)
    if saved is None or saved[0].expires_at<time.time(): raise OpenRouterError('quote_stale',409)
    return saved[0]
