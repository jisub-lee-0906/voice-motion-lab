"""Fetch the pinned Apache-2.0 Basic Pitch ONNX model into this project only."""
import hashlib
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "fa5997af0a8210982619003269994a1be25eddf3"
BASE = f"https://raw.githubusercontent.com/spotify/basic-pitch/{COMMIT}"
SOURCE = BASE + "/basic_pitch/saved_models/icassp_2022/nmp.onnx"
MAX_MODEL_BYTES = 64 * 1024 * 1024


def main():
    destination = ROOT / ".runtime" / "models" / "basic_pitch.onnx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "VoiceMotionLab/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(MAX_MODEL_BYTES + 1)
        if not data or len(data) > MAX_MODEL_BYTES or data.startswith(b"version https://git-lfs"):
            raise RuntimeError("The official download is empty, oversized, or a Git LFS pointer.")
        staging = destination.with_suffix(".download")
        staging.write_bytes(data)
        staging.replace(destination)
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(destination), sess_options=options, providers=["CPUExecutionProvider"])
    metadata = {
        "name": "Spotify Basic Pitch ICASSP 2022",
        "license": "Apache-2.0",
        "repository": "https://github.com/spotify/basic-pitch",
        "commit": COMMIT,
        "source": SOURCE,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "bytes": destination.stat().st_size,
        "providers": session.get_providers(),
        "inputs": [{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_inputs()],
        "outputs": [{"name": item.name, "shape": item.shape, "type": item.type} for item in session.get_outputs()],
    }
    destination.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    license_path = destination.parent / "BASIC_PITCH_LICENSE"
    if not license_path.exists():
        with urllib.request.urlopen(BASE + "/LICENSE", timeout=30) as response:
            license_path.write_bytes(response.read(100_000))
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
