"""Read optional CPU environment metadata without importing its model packages."""
from __future__ import annotations
import hashlib
from pathlib import Path
import threading
from .speaker_review_worker import EncoderIdentity,WEIGHTS_SHA256


def versions(python:Path)->tuple[str,str,str]|None:
    root=python.parent.parent
    if not python.is_file() or not (root/"pyvenv.cfg").is_file():return None
    sites=[root/"Lib"/"site-packages",*root.glob("lib/python*/site-packages")]
    result:dict[str,str]={}
    for package in ("speechbrain","torch","torchaudio"):
        for site in sites:
            for metadata in list(site.glob(f"{package}-*.dist-info/METADATA"))[:4]:
                if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size>262144:continue
                for line in metadata.read_text(encoding="utf-8").splitlines():
                    if line.startswith("Version: "):
                        result[package]=line[9:].strip();break
    if len(result)!=3:return None
    return result["speechbrain"],result["torch"],result["torchaudio"]


def compatible(versions_found:tuple[str,str,str])->bool:
    return versions_found[0]=="1.0.3" and versions_found[1].split("+",1)[0]=="2.6.0" and versions_found[2].split("+",1)[0]=="2.6.0"


_VERIFIED:dict[tuple[str,int,int,int,int,int],str]={}
_LOCK=threading.RLock()


def _hash(path:Path)->str:
    fingerprint=hashlib.sha256()
    with path.open("rb") as source:
        while block:=source.read(65536):fingerprint.update(block)
    return fingerprint.hexdigest()


def digest(path:Path)->str:
    if path.is_symlink() or not path.is_file():raise ValueError("speaker_weights_unverified")
    stat=path.stat()
    if stat.st_size>100*1024*1024:raise ValueError("speaker_weights_unverified")
    key=(str(path.absolute()),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    with _LOCK:
        cached=_VERIFIED.get(key)
        if cached is not None:return cached
        result=_hash(path)
        after=path.stat()
        if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=key[1:]:raise ValueError("speaker_weights_unverified")
        if result==WEIGHTS_SHA256:
            if len(_VERIFIED)>=4:_VERIFIED.pop(next(iter(_VERIFIED)))
            _VERIFIED[key]=result
        return result


def verified_identity(python:Path,weights:Path)->EncoderIdentity:
    packages=versions(python)
    if packages is None:raise ValueError("speaker_dependencies_missing")
    if not compatible(packages):raise ValueError("speaker_dependencies_incompatible")
    fingerprint=digest(weights)
    if fingerprint!=WEIGHTS_SHA256:raise ValueError("speaker_weights_unverified")
    return EncoderIdentity(weights_sha256=fingerprint,speechbrain_version=packages[0],torch_version=packages[1],torchaudio_version=packages[2])
