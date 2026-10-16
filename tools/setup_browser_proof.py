"""Prepare a project-local Basic Pitch WASM proof; never install a package.

The pinned official npm tarball and its official tagged MIT license are fetched.
The complete tarball SHA-512 is verified before selecting CPU-WASM assets.
The existing project model and synthetic PCM fixture are reused unchanged.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import time
import urllib.request
import wave


ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / "web" / "browser-proof"
VENDOR = PROOF / "vendor"
VERSION = "1.30.0"
TARBALL = f"https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-{VERSION}.tgz"
INTEGRITY = "sha512-q0y+JrrtukXSzsBWEMccVfqX25LRmosXHF+CaRJmg8pZClzcV7svNc4rKY3jL02Vb7QmRMDs1SigqR4CXAfKYQ=="
MAX_DOWNLOAD = 50 * 1024 * 1024
MODEL_SHA256 = "2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec"
ASSETS = {
    "package/dist/ort.wasm.min.mjs": "ort.wasm.min.mjs",
    "package/dist/ort-wasm-simd-threaded.mjs": "ort-wasm-simd-threaded.mjs",
    "package/dist/ort-wasm-simd-threaded.wasm": "ort-wasm-simd-threaded.wasm",
}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def prepare_vendor() -> dict:
    cache = ROOT / ".runtime" / "temp" / f"browser-proof-onnxruntime-web-{VERSION}.tgz"
    downloaded_bytes = 0
    if cache.exists():
        data = cache.read_bytes()
    else:
        request = urllib.request.Request(TARBALL, headers={"User-Agent": "VoiceMotionLabBrowserProof/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_DOWNLOAD:
                raise RuntimeError("Official npm tarball exceeds the 50 MiB download limit.")
            data = response.read(MAX_DOWNLOAD + 1)
        downloaded_bytes = len(data)
    if not data or len(data) > MAX_DOWNLOAD:
        raise RuntimeError("Empty or oversized official npm tarball.")
    actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")
    if actual != INTEGRITY:
        raise RuntimeError("Pinned official npm dist.integrity does not match the downloaded tarball.")
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        cache.write_bytes(data)

    selected: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        package = json.load(archive.extractfile("package/package.json"))
        if package.get("name") != "onnxruntime-web" or package.get("version") != VERSION:
            raise RuntimeError("Unexpected npm package identity/version.")
        for member_name, local_name in ASSETS.items():
            member = archive.getmember(member_name)
            if not member.isfile() or member.size > MAX_DOWNLOAD:
                raise RuntimeError("Unsafe or oversized runtime member.")
            selected[local_name] = archive.extractfile(member).read()
        # npm's publish list omits the repository root license in this release.
        # Preserve any shipped notices and fetch the MIT license from its exact tag.
        for member in archive.getmembers():
            if member.isfile() and Path(member.name).name.lower() in {"license", "license.txt", "thirdpartynotices.txt"}:
                selected["ONNXRUNTIME_" + Path(member.name).name] = archive.extractfile(member).read()
    license_url = f"https://raw.githubusercontent.com/microsoft/onnxruntime/v{VERSION}/LICENSE"
    with urllib.request.urlopen(license_url, timeout=30) as response:
        runtime_license = response.read(100_000)
    if b"MIT License" not in runtime_license or b"Microsoft" not in runtime_license:
        raise RuntimeError("Unexpected ONNX Runtime license text.")
    selected["ONNXRUNTIME_LICENSE"] = runtime_license

    model = (ROOT / ".runtime" / "models" / "basic_pitch.onnx").read_bytes()
    if hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Existing project model does not match the approved Basic Pitch file.")
    selected["basic_pitch.onnx"] = model
    selected["BASIC_PITCH_LICENSE"] = (ROOT / ".runtime" / "models" / "BASIC_PITCH_LICENSE").read_bytes()
    VENDOR.mkdir(parents=True, exist_ok=True)
    files = []
    for name, content in selected.items():
        destination = VENDOR / name
        destination.write_bytes(content)
        files.append({"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    metadata = {
        "package": "onnxruntime-web", "version": VERSION, "license": "MIT",
        "registry_metadata": f"https://registry.npmjs.org/onnxruntime-web/{VERSION}",
        "tarball": TARBALL, "integrity": INTEGRITY,
        "download_bytes": downloaded_bytes + len(runtime_license), "tarball_bytes": len(data),
        "runtime_license_source": license_url,
        "extracted_bytes": sum(item["bytes"] for item in files),
        "model": {"name": "Spotify Basic Pitch ICASSP 2022", "license": "Apache-2.0",
                  "commit": "fa5997af0a8210982619003269994a1be25eddf3", "sha256": MODEL_SHA256},
        "files": files,
    }
    write_json(PROOF / "manifest.json", metadata)
    return metadata


def summary(array) -> dict:
    import numpy as np
    flat = np.asarray(array, dtype=np.float32).reshape(-1)
    values = flat.astype(np.float64)
    weights = 1.0 + np.remainder(np.arange(flat.size), 251) / 251.0
    return {
        "shape": list(array.shape), "dtype": "float32", "count": int(flat.size),
        "finite": bool(np.isfinite(flat).all()),
        "min": float(values.min()), "max": float(values.max()),
        "sum": float(values.sum()), "mean": float(values.mean()),
        "rms": float(np.sqrt(np.mean(values * values))),
        "weighted_checksum": float(np.sum(values * weights)),
        "sha256_informational": hashlib.sha256(flat.astype("<f4").tobytes()).hexdigest(),
    }


def prepare_reference() -> dict:
    import numpy as np
    import onnxruntime as ort
    audio_path = ROOT / "web" / "production" / "audio" / "bop-bounce.wav"
    audio_data = audio_path.read_bytes()
    with wave.open(io.BytesIO(audio_data), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 22050):
            raise RuntimeError("Proof fixture must remain mono PCM16 at 22050 Hz.")
        pcm = audio.readframes(43844)
    if len(pcm) != 43844 * 2:
        raise RuntimeError("Proof fixture is shorter than one model chunk.")
    samples = (np.frombuffer(pcm, dtype="<i2").astype(np.float32) / np.float32(32768.0)).reshape(1, 43844, 1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    started = time.perf_counter()
    session = ort.InferenceSession(str(ROOT / ".runtime" / "models" / "basic_pitch.onnx"),
                                   sess_options=options, providers=["CPUExecutionProvider"])
    load_ms = (time.perf_counter() - started) * 1000
    input_name = session.get_inputs()[0].name
    started = time.perf_counter()
    outputs = session.run(None, {input_name: samples})
    inference_ms = (time.perf_counter() - started) * 1000
    result = {
        "format": "voice-motion-browser-inference-proof", "version": 1,
        "scope": "Technical test: first raw 43844 PCM samples only; not a complete audio analysis port.",
        "fixture": {"path": "../production/audio/bop-bounce.wav", "synthetic": True,
                    "file_sha256": hashlib.sha256(audio_data).hexdigest(), "sample_rate": 22050,
                    "sample_count": 43844, "normalization": "signed PCM16 / 32768",
                    "input_float32_sha256": hashlib.sha256(samples.astype("<f4").tobytes()).hexdigest()},
        "runtime": {"name": "onnxruntime", "version": ort.__version__,
                    "provider": "CPUExecutionProvider", "threads": 1,
                    "load_ms": load_ms, "inference_ms": inference_ms},
        "input": {"name": input_name, "shape": [1, 43844, 1], "dtype": "float32"},
        "model_sha256": MODEL_SHA256,
        "comparison_tolerances": {"absolute": 0.0001, "relative": 0.001},
        "outputs": {meta.name: summary(array) for meta, array in zip(session.get_outputs(), outputs)},
    }
    write_json(PROOF / "reference.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-only", action="store_true", help="Reuse existing vendor files; no network.")
    args = parser.parse_args()
    PROOF.mkdir(parents=True, exist_ok=True)
    metadata = None if args.reference_only else prepare_vendor()
    reference = prepare_reference()
    print(json.dumps({"status": "READY", "runtime_version": VERSION,
                      "download_bytes": metadata["download_bytes"] if metadata else 0,
                      "vendor_files": metadata["files"] if metadata else "unchanged",
                      "reference": str(PROOF / "reference.json"),
                      "reference_inference_ms": reference["runtime"]["inference_ms"]}, indent=2))


if __name__ == "__main__":
    main()
