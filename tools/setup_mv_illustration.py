"""Explicit project-local setup of the pinned optional CPU illustration model.

No environment installs. No model code is imported/executed. Only allowlisted
files are downloaded, with pinned Git/LFS integrity verification and provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/".runtime"/"mv-models"/"illustration"
MODEL="OpenVINO/LCM_Dreamshaper_v7-int8-ov"
REV="340a03e5488f36f7f222e6efa29819850ad7e4be"
CLIP="openai/clip-vit-large-patch14"
CLIP_REV="32bd64288804d66eefd0ccbe215aa642df71cc41"
SELECTED=("model_index.json","README.md","text_encoder/config.json","text_encoder/openvino_model.bin","text_encoder/openvino_model.xml",
          "unet/config.json","unet/openvino_model.bin","unet/openvino_model.xml",
          "vae_decoder/config.json","vae_decoder/openvino_model.bin","vae_decoder/openvino_model.xml","scheduler/scheduler_config.json")
EXPECTED_WEIGHTS={
    "text_encoder/openvino_model.bin":"d956758485dfbf416f58369beb4ee4524339fd299abd827993f77858d153d577",
    "unet/openvino_model.bin":"5502e0cd53ac86125bbce5fd950592342f9e12ad02c429c137bd2c211dc231cf",
    "vae_decoder/openvino_model.bin":"fd96c8fece425f22239b8c328ed3e3d580b506352d6f8b3192a17b9a85f03f7b",
}
LICENSES=(
    ("LCM-MIT.LICENSE","https://raw.githubusercontent.com/luosiallen/latent-consistency-model/main/LICENSE","1d7049141c08eaf0250c729f641977052d3a67d7"),
    ("BASE-OPENRAIL-M.LICENSE","https://raw.githubusercontent.com/CompVis/stable-diffusion/main/LICENSE","0e609df0d8cd3b5d11a1ea962a56b604b70846a5"),
    ("CLIP-MIT.LICENSE","https://raw.githubusercontent.com/openai/CLIP/main/LICENSE","4e97f0b45803b7c04ae89548934af4f257a97501"),
    ("SCHEDULER-APACHE2.LICENSE","https://raw.githubusercontent.com/huggingface/diffusers/v0.32.1/LICENSE","261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64"),
)


def read_json(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"VoiceMotionLab-project-setup"}),timeout=60) as response:
        return json.load(response)


def _hashes(path):
    size=path.stat().st_size; sha256=hashlib.sha256(); git=hashlib.sha1(f"blob {size}\0".encode())
    with path.open("rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""):
            sha256.update(chunk); git.update(chunk)
    return {"sha256":sha256.hexdigest(),"git_blob":git.hexdigest(),"bytes":size}


def verify(path,record,expected_sha=None):
    hashes=_hashes(path)
    if hashes["bytes"]!=record["size"]: raise ValueError(f"Size mismatch: {path.name}")
    lfs=record.get("lfs",{})
    expected=expected_sha or lfs.get("sha256")
    if expected and hashes["sha256"]!=expected: raise ValueError(f"SHA256 mismatch: {path.name}")
    if not expected and hashes["git_blob"]!=record["blobId"]: raise ValueError(f"Pinned Git blob mismatch: {path.name}")
    return hashes


def download(url,destination,record,expected_sha=None):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        try: return verify(destination,record,expected_sha)
        except (ValueError,OSError):
            raise ValueError(f"Existing project model file failed integrity; inspect it explicitly before replacement: {destination.name}")
    partial=destination.with_name(destination.name+".part")
    if partial.exists():
        raise ValueError(f"Interrupted download exists; inspect/remove this project-only .part file before retry: {partial.name}")
    request=urllib.request.Request(url,headers={"User-Agent":"VoiceMotionLab-project-setup"})
    with urllib.request.urlopen(request,timeout=90) as response, partial.open("xb") as target:
        while True:
            chunk=response.read(1024*1024)
            if not chunk: break
            target.write(chunk)
    hashes=verify(partial,record,expected_sha)
    os.replace(partial,destination)
    return hashes


def setup():
    if not DEST.resolve().is_relative_to(ROOT.resolve()) or DEST.is_symlink():
        raise ValueError("Model destination must stay inside this project")
    meta=read_json(f"https://huggingface.co/api/models/{MODEL}/revision/{REV}?blobs=true")
    clip=read_json(f"https://huggingface.co/api/models/{CLIP}/revision/{CLIP_REV}?blobs=true")
    if meta.get("sha")!=REV or clip.get("sha")!=CLIP_REV: raise ValueError("Pinned revision mismatch")
    records={f["rfilename"]:f for f in meta["siblings"]}
    clip_record=next(f for f in clip["siblings"] if f["rfilename"]=="tokenizer.json")
    total=sum(records[name]["size"] for name in SELECTED)+clip_record["size"]
    print(f"Pinned CPU illustration files: {total:,} bytes; no GPU, safety-checker or encoder downloads.",flush=True)
    files={}
    for relative in SELECTED:
        record=records[relative]; url=f"https://huggingface.co/{MODEL}/resolve/{REV}/{relative}"
        print(f"Downloading/verifying {relative}: {record['size']:,} bytes",flush=True)
        files[relative]={**download(url,DEST/relative,record,EXPECTED_WEIGHTS.get(relative)),"source":url}
    url=f"https://huggingface.co/{CLIP}/resolve/{CLIP_REV}/tokenizer.json"
    files["tokenizer/tokenizer.json"]={**download(url,DEST/"tokenizer/tokenizer.json",clip_record),"source":url}
    for name,url,blob in LICENSES:
        path=DEST/"licenses"/name
        if path.exists():
            if _hashes(path)["git_blob"]!=blob: raise ValueError(f"License integrity mismatch: {name}")
        else:
            with urllib.request.urlopen(url,timeout=60) as response: data=response.read(128*1024)
            git=hashlib.sha1(f"blob {len(data)}\0".encode()+data).hexdigest()
            if git!=blob: raise ValueError(f"Pinned license content changed: {name}")
            path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        files[f"licenses/{name}"]={**_hashes(path),"source":url}
    notice=("Optional illustration model provenance\n\n"
            f"Converted model: {MODEL}@{REV}. OpenVINO/LCM model cards label MIT.\n"
            "Original LCM: SimianLuo/LCM_Dreamshaper_v7 (MIT metadata).\n"
            "Base model: Lykon/dreamshaper-7, derived from Stable Diffusion 1.5, CreativeML OpenRAIL-M.\n"
            "Retain inherited base-model use restrictions and license; do not present this as an MIT-only model.\n"
            f"CLIP tokenizer: {CLIP}@{CLIP_REV}, MIT.\n"
            "NumPy scheduler follows Diffusers v0.32.1 LCMScheduler, Apache-2.0.\n"
            "Safety checker not installed/executed. Fixed fully clothed adult illustration prompts only; no automatic safety rating.\n"
            "This optional path generates one unreviewed illustration, not a rigged or layered animation pack.\n")
    (DEST/"NOTICE.txt").write_text(notice,encoding="utf-8")
    manifest={"model_id":MODEL,"revision":REV,"tokenizer_id":CLIP,"tokenizer_revision":CLIP_REV,
              "created_utc":datetime.now(timezone.utc).isoformat(),"files":files,"selected_total_bytes":total,
              "license_notice":notice,"safety_checker_installed":False,"purpose":"optional original 512px CPU illustration experiment"}
    (DEST/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"ready":True,"model_id":MODEL,"revision":REV,"bytes":total}),flush=True)
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status",action="store_true",help="read local manifest only; no network")
    args=parser.parse_args(argv)
    if args.status:
        path=DEST/"manifest.json"
        print(json.dumps({"manifest_present":path.is_file(),"model_id":MODEL,"revision":REV})); return 0
    setup(); return 0


if __name__=="__main__": raise SystemExit(main())
