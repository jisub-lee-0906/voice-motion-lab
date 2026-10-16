"""Decode an actual downloaded BOP! video; optional original WAV comparison."""
import argparse
import json
from pathlib import Path

import av
import numpy as np


def sound(path):
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=22050)
        frames = []
        for frame in container.decode(audio=0):
            frames.extend(resampler.resample(frame))
        frames.extend(resampler.resample(None))
        return np.concatenate([frame.to_ndarray().ravel() for frame in frames]).astype(np.float64)


def inspect(path, reference=None):
    with av.open(str(path)) as container:
        assert container.duration is not None, "Container duration is missing"
        duration = container.duration / av.time_base
        assert 4.8 < duration < 5.2, duration
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 2
        codec = stream.codec_context.name
        times, diffs, previous = [], [], None
        for frame in container.decode(video=0):
            assert (frame.width, frame.height) == (720, 960)
            times.append(float(frame.time))
            image = frame.to_ndarray(format="rgb24")[::8, ::8].astype(np.int16)
            if previous is not None:
                diffs.append(float(np.mean(np.abs(image - previous))))
            previous = image
        assert len(times) >= 110, "Video frames missing"
        assert max(diffs) > .5, "No visible animation"
        container.seek(3_000_000)
        assert next(container.decode(video=0)).width == 720, "Seek failed"
    audio = sound(path)
    rms = float(np.sqrt(np.mean(audio ** 2)))
    assert rms > .001, "Original audio is silent"
    report = {"file": path.name, "bytes": path.stat().st_size, "duration": duration,
              "video_codec": codec, "dimensions": [720, 960], "frames": len(times),
              "audio_seconds": len(audio) / 22050, "audio_rms": round(rms, 5),
              "max_frame_change": round(max(diffs), 3), "seek_3s": True}
    if reference:
        source = sound(reference)[:110250]
        size = 1 << (len(source) + len(audio) - 1).bit_length()
        correlation = np.fft.irfft(np.fft.rfft(audio, size) * np.conj(np.fft.rfft(source, size)), size)
        lags = np.arange(-2205, 2206)
        lag = int(lags[np.argmax(correlation[lags % size])])
        start_source, start_audio = max(0, -lag), max(0, lag)
        count = min(len(source)-start_source, len(audio)-start_audio)
        x, y = source[start_source:start_source+count], audio[start_audio:start_audio+count]
        similarity = float(np.dot(x, y) / np.sqrt(np.dot(x, x) * np.dot(y, y)))
        assert similarity > .95, f"Audio does not match original: {similarity}"
        report.update(audio_similarity=round(similarity, 5), audio_lag_ms=round(lag/22.05, 2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = inspect(args.video, args.reference)
    if args.report:
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
