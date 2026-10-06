"""Install optional CPU dependencies into a separate explicitly selected venv.

This does not fetch encoder checkpoints or activate review in the main backend.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

_MARKER={"schema_version":1,"dependency_setup":"speechbrain-1.0.3-torch-2.6.0-cpu-v1"}


def _owned(marker:Path)->bool:
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size>1024:return False
    try:
        value:object=json.loads(marker.read_bytes())
        return isinstance(value,dict) and set(value)==set(_MARKER) and type(value.get("schema_version")) is int and value==_MARKER
    except (OSError,ValueError):return False


def _claim(marker:Path)->None:
    temporary:Path|None=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",dir=marker.parent,encoding="utf-8",delete=False) as output:
            json.dump(_MARKER,output);output.flush();os.fsync(output.fileno());temporary=Path(output.name)
        temporary.replace(marker)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def install(runtime_root:Path,*,python_path:Path|None=None)->Path:
    if runtime_root.is_symlink():raise ValueError("speaker_setup_path_invalid")
    root=runtime_root.absolute()
    backend=Path(__file__).resolve().parents[1]
    if root.resolve() in {backend,backend.parent} or (root/".git").exists() or (root/"app"/"config.py").is_file():raise ValueError("speaker_setup_path_invalid")
    target=root/".venv"
    if target.is_symlink() or target.resolve()==Path(sys.prefix).resolve():raise ValueError("speaker_setup_path_invalid")
    marker=root/".openfabric-speaker-runtime.json"
    if (target.exists() or marker.exists() or marker.is_symlink()) and not _owned(marker):raise ValueError("speaker_setup_unowned")
    selected=python_path or Path(sys.executable)
    version=subprocess.run([str(selected),"--version"],capture_output=True,text=True,timeout=5,check=True)
    if not (version.stdout.strip() or version.stderr.strip()).startswith("Python 3.12."):raise ValueError("speaker_python_unsupported")
    root.mkdir(parents=True,exist_ok=True)
    if not _owned(marker):_claim(marker)
    subprocess.run([str(selected),"-m","venv",str(target)],check=True)
    python=target/("Scripts/python.exe" if os.name=="nt" else "bin/python")
    requirements=Path(__file__).resolve().parents[1]/"requirements-speaker-review.txt"
    # Linux/Windows PyPI wheels may bundle CUDA. Use the official CPU wheel index.
    command=[str(python),"-m","pip","install","torch==2.6.0","torchaudio==2.6.0"]
    if sys.platform!="darwin":command.extend(["--index-url","https://download.pytorch.org/whl/cpu"])
    subprocess.run(command,check=True)
    subprocess.run([str(python),"-m","pip","install","-r",str(requirements)],check=True)
    return python


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--runtime-root",required=True,type=Path);parser.add_argument("--python",type=Path)
    options=parser.parse_args()
    try:
        python=install(options.runtime_root,python_path=options.python)
        print(f"CPU dependencies installed: {python}")
        print("Encoder weights were not downloaded. Configure OPENFABRIC_SPEAKER_REVIEW_PYTHON and OPENFABRIC_SPEAKER_REVIEW_WEIGHTS after reviewing docs/speaker-review.md.")
        return 0
    except (OSError,ValueError,subprocess.CalledProcessError):
        print("speaker_dependency_setup_failed",file=sys.stderr);return 1


if __name__=="__main__":raise SystemExit(main())
