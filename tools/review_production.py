"""Measure native BOP! browser exports, including audio alignment and visible bounds."""
import json
from pathlib import Path

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "output/production"


def review(name, impacts):
    project = json.loads((DEST / f"{name}.json").read_text(encoding="utf-8"))
    assert project["analysis"]["engine"] == "basic_pitch"
    assert len(project["analysis"]["events"]) == impacts
    assert project["settings"]["preset"] == name
    duration = project["analysis"]["duration"]
    video = av.open(str(DEST / f"{name}.webm"))
    video.streams.video[0].codec_context.thread_count = 2
    bg = np.array([int(project["settings"]["background"][i:i+2], 16) for i in (1, 3, 5)])
    times, centers, margins = [], [], []
    poster_saved = False
    for frame in video.decode(video=0):
        assert (frame.width, frame.height) == (960, 540)
        times.append(float(frame.time))
        image = frame.to_ndarray(format="rgb24")
        mask = np.max(np.abs(image.astype(np.int16) - bg), axis=2) > 40
        yy, xx = np.where(mask)
        assert len(xx) > 500, "Source art vanished"
        margins.append(int(min(xx.min(), yy.min(), 959 - xx.max(), 539 - yy.max())))
        centers.append((float(xx.mean()), float(yy.mean())))
        if not poster_saved and times[-1] - times[0] >= duration * .5:
            frame.to_image().save(DEST / f"{name}-poster.png")
            poster_saved = True
    assert abs(times[-1] - times[0] - duration) < .15
    assert min(margins) >= 5, "Visible art touched the edge"
    assert len(times) >= duration * 27, "Unexpected frame loss"
    stream = av.open(str(DEST / f"{name}.webm"))
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=22050)
    audio_frames = []
    audio_start = None
    for frame in stream.decode(audio=0):
        if audio_start is None:
            audio_start = float(frame.time)
        audio_frames.extend(resampler.resample(frame))
    audio_frames.extend(resampler.resample(None))
    sound = np.concatenate([f.to_ndarray().ravel() for f in audio_frames]).astype(np.float64)
    source = np.array(project["audio"]["samples"], dtype=np.float64)
    assert project["audio"]["sample_rate"] == 22050
    size = 1 << (len(source) + len(sound) - 1).bit_length()
    correlation = np.fft.irfft(np.fft.rfft(sound, size) * np.conj(np.fft.rfft(source, size)), size)
    lags = np.arange(-5512, 5513)
    lag = int(lags[np.argmax(correlation[lags % size])])
    offset = lag / 22050 + audio_start - times[0]
    src_begin, out_begin = max(0, -lag), max(0, lag)
    count = min(len(source) - src_begin, len(sound) - out_begin)
    x, y = source[src_begin:src_begin+count], sound[out_begin:out_begin+count]
    similarity = float(np.dot(x, y) / np.sqrt(np.dot(x, x) * np.dot(y, y)))
    assert abs(offset) < .10, f"Audio offset {offset:.3f}s"
    assert similarity > .90, f"Audio mismatch {similarity:.3f}"
    report = {"preset": name, "engine": "basic_pitch", "synthetic_audio": True,
        "duration": duration, "video_frames": len(times), "min_visible_margin_px": min(margins),
        "audio_offset_ms": round(offset * 1000, 1), "audio_similarity": round(similarity, 4),
        "intensity": project["settings"]["intensity"], "impacts": impacts}
    if name == "fly":
        held = [f for f in project["analysis"]["frames"] if 2.30 <= f["time"] <= 3.15]
        delta = max(f["progress"] for f in held) - min(f["progress"] for f in held)
        assert delta == 0
        at = np.array(centers)[np.array([(2.4 <= t-times[0] <= 3.05) for t in times])]
        drift = float(np.max(np.ptp(at, axis=0)))
        assert drift < 1, f"Silence moved the exported picture by {drift:.2f}px"
        report.update(silent_progress_delta=delta, silent_picture_drift_px=round(drift, 3))
    return report


if __name__ == "__main__":
    result = [review(name, impacts) for name, impacts in (("bounce", 7), ("fly", 3), ("orbit", 5))]
    (DEST / "review.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
