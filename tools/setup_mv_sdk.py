"""Install the two pinned official SDK/proxy wheels in .mv-venv only.

Run with .mv-venv/Scripts/python.exe tools/setup_mv_sdk.py --install.
Without --install this is read-only metadata inspection. No server is started.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    {"name": "comfy-sdk", "version": "0.5.0",
     "filename": "comfy_sdk-0.5.0-py3-none-any.whl",
     "url": "https://files.pythonhosted.org/packages/f8/a7/4ae8c85f4dfcf83e184a4302dd2344b61c651790d022c36953eda24c3e16/comfy_sdk-0.5.0-py3-none-any.whl",
     "sha256": "dfcd3ab026cfc42e83681148766fe8b3d619fdc476ec995e793c1efcab2fd1c5",
     "license": "MIT", "source": "https://github.com/Comfy-Org/comfy-python-sdk"},
    {"name": "comfy-api-proxy", "version": "0.1.6",
     "filename": "comfy_api_proxy-0.1.6-py3-none-any.whl",
     "url": "https://files.pythonhosted.org/packages/98/5a/d1c0dd440ffe8277ffabd6900efe01e61716f500223e9c34f45cdc82ac98/comfy_api_proxy-0.1.6-py3-none-any.whl",
     "sha256": "1ea405f1a4dc5886d06d630bc1de57fd8754062776ace797e66969636ca941c6",
     "license": "MIT", "source": "https://github.com/Comfy-Org/comfy-api-proxy"},
)


def status():
    records = []
    for package in PACKAGES:
        try:
            installed = importlib.metadata.version(package["name"])
        except importlib.metadata.PackageNotFoundError:
            installed = None
        records.append({"package": package["name"], "required": package["version"],
                        "installed": installed, "matches": installed == package["version"],
                        "license": package["license"], "source": package["source"]})
    return {"packages": records, "ready": all(item["matches"] for item in records),
            "provider": "official-comfy-sdk-local-api-v2", "proxy": "http://127.0.0.1:8795",
            "comfy": "http://127.0.0.1:8794"}


def install():
    prefix = Path(sys.prefix).resolve()
    if prefix != ROOT / ".mv-venv" or sys.prefix == sys.base_prefix:
        raise RuntimeError("Use this project's .mv-venv Python; system or existing Comfy Python is forbidden.")
    cache = ROOT / ".cache" / "pip" / "mv-sdk"
    cache.mkdir(parents=True, exist_ok=True)
    wheels = []
    for package in PACKAGES:
        destination = cache / package["filename"]
        if destination.is_file():
            contents = destination.read_bytes()
        else:
            request = urllib.request.Request(package["url"], headers={"User-Agent": "VoiceMotionLab/0.3-setup"})
            with urllib.request.urlopen(request, timeout=60) as response:
                contents = response.read(2 * 1024 * 1024 + 1)
        if len(contents) > 2 * 1024 * 1024 or hashlib.sha256(contents).hexdigest() != package["sha256"]:
            raise RuntimeError(f"Pinned official wheel integrity failed for {package['name']}.")
        if not destination.exists():
            destination.write_bytes(contents)
        wheels.append(str(destination))
    env = dict(os.environ, PYTHONNOUSERSITE="1", PIP_CACHE_DIR=str(ROOT / ".cache" / "pip"),
               TMP=str(ROOT / "temp"), TEMP=str(ROOT / "temp"))
    (ROOT / "temp").mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", *wheels],
                   cwd=ROOT, env=env, check=True)
    provenance = ROOT / ".runtime" / "mv-sdk.json"
    provenance.parent.mkdir(exist_ok=True)
    provenance.write_text(json.dumps({"packages": list(PACKAGES), "status": status()}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install only in this checkout's .mv-venv")
    args = parser.parse_args()
    if args.install:
        install()
    print(json.dumps(status(), indent=2))
