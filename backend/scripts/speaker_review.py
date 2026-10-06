"""Offline CPU ECAPA adapter; no remote YAML, fetching, GPU selection or unsafe load.

Architecture: speechbrain/spkrec-ecapa-voxceleb hyperparams.yaml (Apache-2.0).
Inference: SpeechBrain v1.0.3 EncoderClassifier.encode_batch(normalize=False).
Only its embedding_model.ckpt is needed; fixed preprocessing is implemented here.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import speaker_review_worker as protocol,speaker_review_environment as environment


def _checkpoint(path:Path)->None:
    # Always hash actual bytes here. Backend status may cache a stat-bound result.
    if path.is_symlink() or not path.is_file() or path.stat().st_size>100*1024*1024:raise ValueError("speaker_weights_unverified")
    digest=hashlib.sha256()
    with path.open("rb") as source:
        while block:=source.read(65536):digest.update(block)
    if digest.hexdigest()!=protocol.WEIGHTS_SHA256:raise ValueError("speaker_weights_unverified")


def _encode(reference:Path,passage:Path,weights:Path)->tuple[protocol.Embedding,protocol.Embedding]:
    _checkpoint(weights)
    identity=environment.verified_identity(Path(sys.executable),weights)
    reference_samples,reference_ms,reference_active=protocol.read_pcm(reference)
    passage_samples,passage_ms,passage_active=protocol.read_pcm(passage)
    # All imports are confined to this paid-free explicitly configured process.
    import torch
    from speechbrain.lobes.features import Fbank
    from speechbrain.processing.features import InputNormalization
    from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN
    torch.set_num_threads(2)
    model=ECAPA_TDNN(input_size=80,channels=[1024,1024,1024,1024,3072],kernel_sizes=[5,3,3,3,1],
        dilations=[1,2,3,4,1],attention_channels=128,lin_neurons=192)
    with weights.open("rb") as source:
        loaded:object=torch.load(source,map_location="cpu",weights_only=True)
    if not isinstance(loaded,dict):raise ValueError("speaker_weights_unverified")
    state:dict[str,torch.Tensor]={}
    for key,value in loaded.items():
        if not isinstance(key,str) or not isinstance(value,torch.Tensor):raise ValueError("speaker_weights_unverified")
        state[key]=value
    model.load_state_dict(state,strict=True);model.eval();model.cpu()
    # Recheck replacement/mutation between fingerprint and checkpoint loading.
    _checkpoint(weights)
    features=Fbank(n_mels=80)
    normalize=InputNormalization(norm_type="sentence",std_norm=False)
    results:list[protocol.Embedding]=[]
    with torch.inference_mode():
        for samples,duration,active in ((reference_samples,reference_ms,reference_active),(passage_samples,passage_ms,passage_active)):
            signal=torch.tensor(samples,dtype=torch.float32).unsqueeze(0)
            lengths=torch.ones(1)
            embedding=model(normalize(features(signal),lengths),lengths)
            values:object=embedding.detach().cpu().reshape(-1).tolist()
            results.append(protocol.Embedding(identity,protocol.vector(values),duration,active))
    return results[0],results[1]


def _write(path:Path,value:object)->None:
    if path.is_symlink():raise ValueError("speaker_output_invalid")
    temporary:Path|None=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",dir=path.parent,delete=False,encoding="utf-8") as output:
            json.dump(value,output,allow_nan=False);output.flush();os.fsync(output.fileno());temporary=Path(output.name)
        temporary.replace(path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def main(arguments:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capability",action="store_true");parser.add_argument("--weights",type=Path)
    parser.add_argument("--request",type=Path);parser.add_argument("--output",type=Path)
    options=parser.parse_args(arguments)
    # Also deny implicit HF network access if optional libraries change internally.
    os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1";os.environ["CUDA_VISIBLE_DEVICES"]=""
    try:
        if options.capability:
            packages=environment.versions(Path(sys.executable));deps=packages is not None and environment.compatible(packages)
            weights=options.weights;encoder:object=None
            reason="speaker_dependencies_missing" if packages is None else "speaker_dependencies_incompatible" if not deps else "speaker_weights_missing"
            if deps and weights is not None:
                encoder=environment.verified_identity(Path(sys.executable),weights).to_json();reason=""
            print(json.dumps({"available":encoder is not None,"configured":weights is not None,"deps_available":deps,
                "weights_available":weights is not None and weights.is_file() and not weights.is_symlink(),"encoder":encoder,"reason":reason,"setup_hint":"No weights are downloaded. Configure the reviewed local checkpoint explicitly."}))
            return 0
        request:Path|None=options.request;output:Path|None=options.output
        if request is None or output is None or request.is_symlink() or request.stat().st_size>65536:raise ValueError("speaker_input_invalid")
        raw:object=json.loads(request.read_bytes())
        if not isinstance(raw,dict) or set(raw)!={"reference","passage","weights"}:raise ValueError("speaker_input_invalid")
        values:list[Path]=[]
        for key in ("reference","passage","weights"):
            value=raw[key]
            if not isinstance(value,str) or not 1<=len(value)<=4096:raise ValueError("speaker_input_invalid")
            values.append(Path(value))
        reference,passage=_encode(*values)
        _write(output,{"reference":reference.to_json(),"passage":passage.to_json()})
        return 0
    except (OSError,ValueError,RuntimeError,ImportError) as error:
        code=str(error) if isinstance(error,ValueError) and str(error).startswith("speaker_") else "speaker_runner_failed"
        print(json.dumps({"error":code}));return 2


if __name__=="__main__":raise SystemExit(main())
