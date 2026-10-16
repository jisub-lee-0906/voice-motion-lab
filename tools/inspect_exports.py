"""Inspect browser-created artifacts; run with the isolated Comfy venv (PyAV)."""
import argparse
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    destination = ROOT / "output" / "verified"
    destination.mkdir(parents=True, exist_ok=True)
    paths = {}
    for suffix in ("svg", "json", "webm"):
        source = args.directory / f"voice-motion-bounce.{suffix}"
        assert source.is_file(), source
        target = destination / source.name
        shutil.copy2(source, target)
        paths[suffix] = target
    project = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert project["asset"]["name"] == "orbit-mark.svg"
    assert project["settings"]["preset"] == "bounce"
    assert len(project["audio"]["samples"]) == 110250
    assert len(project["keyframes"]) == 301
    assert project["analysis"]["engine"] == "basic_pitch"
    svg = ET.parse(paths["svg"])
    animations = [el for el in svg.iter() if el.tag.endswith("animateTransform")]
    assert len(animations) >= 3
    assert all(len(el.attrib["values"].split(";")) == 301 for el in animations)
    video = av.open(str(paths["webm"]))
    assert {stream.type for stream in video.streams} == {"video", "audio"}
    frames = list(video.decode(video=0))
    times = [float(frame.pts * frame.time_base) for frame in frames]
    assert len(frames) >= 140 and 4.9 <= times[-1] - times[0] <= 5.3
    assert all((frame.width, frame.height) == (960, 540) for frame in frames)
    centers = []
    for frame in frames:
        image = frame.to_ndarray(format="rgb24")
        green = (image[:, :, 1] > 180) & (image[:, :, 0] > 90) & (image[:, :, 2] < 160)
        if green.any():
            y, x = np.where(green)
            centers.append([float(x.mean()), float(y.mean())])
    assert len(centers) == len(frames), "Source mark must remain visible."
    assert np.ptp(np.array(centers)[:, 0]) > 300, "Video must contain actual movement."
    audio = av.open(str(paths["webm"]))
    decoded_audio = list(audio.decode(audio=0))
    assert decoded_audio
    audio_seconds = sum(f.samples for f in decoded_audio) / decoded_audio[0].sample_rate
    assert 4.9 <= audio_seconds <= 5.3
    energy = np.concatenate([f.to_ndarray().reshape(-1) for f in decoded_audio])
    assert float(np.sqrt(np.mean(energy ** 2))) > .01, "Audio must not be a silent track."
    frames[min(range(len(times)), key=lambda i: abs(times[i] - times[0] - 2.8))].to_image().save(destination / "motion-frame.png")
    report = {"synthetic_fixture": True, "source_asset": project["asset"]["name"],
              "keyframes": len(project["keyframes"]), "svg_animation_tracks": len(animations),
              "video_frames": len(frames), "video_seconds": round(times[-1] - times[0], 3),
              "resolution": [960, 540], "audio_codec": audio.streams.audio[0].codec_context.name,
              "audio_non_silent": True, "audio_seconds": round(audio_seconds, 3),
              "asset_visible_in_all_frames": True,
              "bytes": {suffix: path.stat().st_size for suffix, path in paths.items()}}
    (ROOT / "tests/artifacts/export-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
