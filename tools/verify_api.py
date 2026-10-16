"""Exercise only this project's two loopback services with synthetic audio."""
import json
import math
from pathlib import Path
import struct
import time
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]
APP = "http://127.0.0.1:8793"
COMFY = "http://127.0.0.1:8794"


def request(url, body=None):
    data = None if body is None else json.dumps(body, allow_nan=False).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=45) as response:
        return json.load(response)


def main():
    health = request(APP + "/api/health")
    assert health["engines"]["basic_pitch"] and health["comfy"]["url"] == COMFY
    schema = request(COMFY + "/object_info/VoiceMotionAnalyze")["VoiceMotionAnalyze"]
    assert schema["output"][0] == "STRING" and schema["output_node"]
    with wave.open(str(ROOT / "tests/fixtures/synthetic-performance.wav")) as audio:
        rate = audio.getframerate()
        raw = audio.readframes(audio.getnframes())
    samples = [round(value[0] / 32768, 6) for value in struct.iter_unpack("<h", raw)]
    payload = {"samples": samples, "sample_rate": rate, "engine": "basic_pitch", "sensitivity": 1, "smoothing": .35}
    results = {}
    for label, route in (("direct", "/api/analyze"), ("comfy", "/api/analyze-comfy")):
        started = time.perf_counter()
        result = request(APP + route, payload)
        assert result["engine"] == "basic_pitch" and result["duration"] == 5.0
        assert len(result["frames"]) >= 250 and result["frames"][-1]["progress"] == 1
        assert all(math.isfinite(float(value)) for frame in result["frames"] for value in frame.values())
        pause = [frame["progress"] for frame in result["frames"] if 2.4 <= frame["time"] <= 2.6]
        assert pause and max(pause) - min(pause) < 1e-5, "Silence should hold position"
        results[label] = {"seconds": round(time.perf_counter() - started, 3), "engine": result["engine"],
                          "frames": len(result["frames"]), "events": result["events"], "warnings": result["warnings"]}
    # Exercise both analysis and project packaging on the independent instance.
    graph = json.loads((ROOT / "workflows/voice_motion_project.api.json").read_text(encoding="utf-8"))
    assert all(node["class_type"].startswith("VoiceMotion") for node in graph.values())
    analysis_target = "voice_motion_analysis"
    target = "voice_motion_project"
    graph[analysis_target]["inputs"].update(samples_json=json.dumps(samples), sample_rate=rate, engine="basic_pitch")
    graph[target]["inputs"].update(samples_json=json.dumps(samples), sample_rate=rate,
        svg=(ROOT / "tests/fixtures/orbit-mark.svg").read_text(encoding="utf-8"), asset_name="orbit-mark.svg")
    queued = request(COMFY + "/prompt", {"prompt": graph, "client_id": "voice-motion-verification"})
    prompt_id = queued["prompt_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        history = request(COMFY + "/history/" + prompt_id)
        if prompt_id in history:
            item = history[prompt_id]
            assert item["status"]["status_str"] == "success", item["status"]
            output = json.loads(item["outputs"][target]["text"][0])
            assert output["analysis"]["engine"] == "basic_pitch"
            assert output["asset"]["name"] == "orbit-mark.svg"
            assert output["audio"]["samples"] == samples
            artifact = ROOT / "tests/artifacts/comfy-project.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(json.dumps(output), encoding="utf-8")
            results["workflow"] = {"prompt_id": prompt_id, "status": "success", "engine": output["analysis"]["engine"], "project": str(artifact.relative_to(ROOT))}
            break
        time.sleep(.1)
    else:
        raise AssertionError("Project workflow did not finish")
    results["fixture"] = "synthetic harmonic/tap audio; not a human recording"
    destination = ROOT / "tests/artifacts/api-check.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
