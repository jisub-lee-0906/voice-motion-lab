"""Download pinned MIT muxer bundles into this project's static web directory.

Uses only the Python standard library. npm is not installed or executed. Both
packages are maintenance-frozen upstream; only their small container muxers are
used. Downloaded source and licenses are preserved without modification.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "web" / "play" / "vendor"
CACHE = ROOT / ".cache" / "export-runtime"
PACKAGES = (
    ("mp4-muxer", "5.2.2", "dhozjTywI0h2qFzeShagt8YYw811fh1XlwiDCE2f6Aeqf6xG2CyuShoSa5E0AZDO8pPF0JOZ3wOmWBNWIGdSpQ=="),
    ("webm-muxer", "5.1.4", "ditzgFVFbfqPaugkIr4mGhAdob5K9HY6Rzlh7TRsA368yA1sp/m5O7nQCcMLdgFDeNGtFPg8B+MeXLtpzKWX6Q=="),
)


def setup() -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    VENDOR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, version, expected_hash in PACKAGES:
        url = f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz"
        archive_path = CACHE / f"{name}-{version}.tgz"
        payload = archive_path.read_bytes() if archive_path.is_file() else None
        if payload is None or base64.b64encode(hashlib.sha512(payload).digest()).decode() != expected_hash:
            request = urllib.request.Request(url, headers={"User-Agent": "VoiceMotionLab-local-setup/0.2"})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read(2_000_000)
            if base64.b64encode(hashlib.sha512(payload).digest()).decode() != expected_hash:
                raise ValueError(f"Integrity check failed for {name}@{version}; no bundle was extracted.")
            archive_path.write_bytes(payload)
        files = {}
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            package_json = json.loads(archive.extractfile("package/package.json").read())
            if package_json["name"] != name or package_json["version"] != version or package_json["license"] != "MIT":
                raise ValueError(f"Unexpected package identity/license for {name}.")
            for member, destination_name in ((f"build/{name}.mjs", f"{name}.mjs"),
                                             ("LICENSE", f"{name}.LICENSE"),
                                             ("README.md", f"{name}.README.md")):
                data = archive.extractfile(f"package/{member}").read()
                destination = VENDOR / destination_name
                if not destination.is_file() or destination.read_bytes() != data:
                    destination.write_bytes(data)
                files[destination_name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        manifest.append({"name": name, "version": version, "license": "MIT", "url": url,
                         "integrity": f"sha512-{expected_hash}", "files": files,
                         "upstream_status": "Deprecated in favor of Mediabunny; frozen container-only dependency."})
    (VENDOR / "export-runtime.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(setup(), indent=2))
