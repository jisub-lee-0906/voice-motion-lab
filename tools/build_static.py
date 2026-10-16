"""Build a standalone public BOP! site; never copies recordings, APIs or Python."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def collect_files():
    selected = [WEB / "index.html", WEB / "core.mjs", WEB / "sw.js",
                WEB / "browser-proof/manifest.json"]
    for path in (WEB / "play").rglob("*"):
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(WEB).parts):
            if path.suffix in {".mjs", ".js", ".css", ".wasm", ".json", ".svg", ".md"} or path.name.endswith(".LICENSE"):
                selected.append(path)
    for name in ("bop-bounce.svg", "bop-fly.svg", "bop-orbit.svg"):
        selected.append(WEB / "production/assets" / name)
    selected.append(WEB / "production/audio/bop-bounce.wav")
    model_meta = json.loads((WEB / "browser-proof/manifest.json").read_text(encoding="utf-8-sig"))
    for item in model_meta["files"]:
        path = WEB / "browser-proof/vendor" / item["name"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Prepare or repair the pinned browser runtime: {path.name}")
        selected.append(path)
    required = ["play/app.mjs", "play/style.css", "play/scene.mjs", "play/mic.mjs",
                "play/analysis-client.mjs", "play/analysis.worker.mjs", "play/analysis.mjs",
                "play/export-video.mjs", "play/media-utils.mjs", "play/webm-finalize.mjs",
                "play/offline.mjs", "play/vendor/export-runtime.json"]
    missing = [name for name in required if not (WEB / name).is_file()]
    if missing:
        raise ValueError("Browser implementation/runtime is incomplete: " + ", ".join(missing))
    export_meta = json.loads((WEB / "play/vendor/export-runtime.json").read_text(encoding="utf-8-sig"))
    for package in export_meta:
        for name, item in package["files"].items():
            path = WEB / "play/vendor" / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError(f"Prepare or repair the pinned export runtime: {name}")
    # Reject paths escaping this project's public web directory, including symlinks.
    for path in selected:
        if not path.is_file() or not path.resolve().is_relative_to(WEB.resolve()):
            raise ValueError(f"Missing or unsafe public asset: {path.name}")
    return sorted(set(selected))


def build(destination):
    destination = destination.resolve()
    if not destination.is_relative_to(ROOT) or destination in {ROOT, WEB}:
        raise ValueError("Build output must be a new directory inside this project.")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Choose an empty output directory; this tool never deletes existing files.")
    files = collect_files()
    digest = hashlib.sha256()
    for path in files:
        if path.name != "sw.js":
            digest.update(str(path.relative_to(WEB)).replace("\\", "/").encode())
            digest.update(path.read_bytes())
    version = digest.hexdigest()[:16]
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"format": "bop-static-build", "version": "0.2.0", "cache_version": version,
                "inference": "browser-only", "audio_uploads": False, "files": {}}
    for source in files:
        relative = source.relative_to(WEB)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.name == "sw.js":
            target.write_text(source.read_text(encoding="utf-8").replace("__BOP_CACHE_VERSION__", version), encoding="utf-8", newline="\n")
        elif relative.as_posix() == "index.html":
            # The advanced local Studio needs its optional Python service. Public play
            # links to its source/instructions instead of publishing a broken editor.
            target.write_text(source.read_text(encoding="utf-8").replace('href="./studio/index.html"',
                'href="https://github.com/jisub-lee-0906/voice-motion-lab#advanced-local-studio"'),
                encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(source, target)
        data = target.read_bytes()
        manifest["files"][relative.as_posix()] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, destination / name)
    (destination / ".nojekyll").write_bytes(b"")
    # Preserve the exact packaged bytes (including upstream assets) when committing
    # the output on Windows; build.json verifies bytes served from Git as well.
    (destination / ".gitattributes").write_text("* -text\n", encoding="utf-8", newline="\n")
    manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"].values())
    (destination / "build.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    destination = args.out or ROOT / ".runtime" / ("site-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    result = build(destination)
    print(json.dumps({"directory": str(destination.resolve()), "files": len(result["files"]),
                      "bytes": result["total_bytes"], "cache_version": result["cache_version"]}))
