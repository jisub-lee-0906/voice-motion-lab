"""Generate full-output goldens from private, deterministic synthetic sources only."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import sys
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voice_motion.analysis import analyze


def uniform(seed):
    while True:
        seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
        yield seed / 4294967296 * 2 - 1


def synthetic(source, rate):
    time = np.arange(round(source["duration"] * rate)) / rate
    audio = np.zeros(len(time))
    kind = source["kind"]
    if kind == "silence":
        pass
    elif kind == "low_noise":
        noise = uniform(8)
        audio[:] = [next(noise) * .002 for _ in time]
    elif kind == "tone_holds":
        for start, stop, frequency in ((.25, 1.1, 220), (3., 4.2, 330)):
            active = (time >= start) & (time < stop)
            audio[active] = .3 * np.sin(2 * np.pi * frequency * time[active])
    elif kind == "glide_hits":
        phase = 2 * np.pi * (170 * time + 35 * time ** 2)
        envelope = np.clip((time - .2) / .06, 0, 1) * np.clip((3.3 - time) / .1, 0, 1)
        audio = .13 * envelope * np.sin(phase)
        noise = uniform(191)
        for hit in (1.3, 4.1):
            age = time - hit
            for index in np.flatnonzero((age >= 0) & (age < .08)):
                audio[index] += .65 * np.exp(-age[index] * 55) * next(noise)
    elif kind == "impulses":
        for hit in (.4, .43, 2.1, 4.6):
            audio[round(hit * rate)] = 1.
    elif kind == "published_bop":
        with wave.open(str(ROOT / "web" / "production" / "audio" / "bop-bounce.wav"), "rb") as stream:
            assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, rate)
            audio = np.frombuffer(stream.readframes(len(time)), dtype="<i2").astype(np.float64) / 32768.
    else:
        raise ValueError(kind)
    return audio.astype(np.float32)


def main():
    cases = [("silence", 16000), ("low_noise", 16000), ("tone_holds", 22050),
             ("glide_hits", 22050), ("impulses", 8000), ("published_bop", 22050)]
    fixtures = []
    for kind, rate in cases:
        source = {"kind": kind, "duration": 5}
        audio = synthetic(source, rate)
        options = {"sensitivity": 1., "smoothing": .35}
        references = {engine: analyze(audio, rate, engine=engine, **options) for engine in ("signal", "basic_pitch")}
        count = max(1, round(len(audio) / rate * 22050))
        resampled = np.interp(np.arange(count) / 22050, np.arange(len(audio)) / rate, audio,
                              left=0, right=0).astype(np.float32)
        padded = np.pad(resampled, (3840, 3840))
        hashes = []
        for start in range(0, max(1, count), 36164):
            chunk = padded[start:start + 43844]
            chunk = np.pad(chunk, (0, 43844 - len(chunk)))
            hashes.append(hashlib.sha256(chunk.astype("<f4").tobytes()).hexdigest())
        fixtures.append({"name": kind, "sampleRate": rate, "source": source, "options": options,
                         "model_input_sha256": hashes, "reference": references})
    destination = ROOT / "tests" / "web" / "fixtures" / "browser-analysis-golden.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format": "voice-motion-analysis-goldens", "version": 1,
               "privacy": "Only deterministic synthetic-source descriptions and complete analysis frames; no recorded audio.",
               "model_sha256": "2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec",
               "fixtures": fixtures}
    destination.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(destination), "bytes": destination.stat().st_size,
                      "fixtures": len(fixtures), "complete_analyses": len(fixtures) * 2}))


if __name__ == "__main__":
    main()
