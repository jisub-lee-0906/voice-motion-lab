"""Create original, explicitly synthetic BOP! motion performances.

No samples, voices, external assets, GPU, or network are used. Run using this
project's .venv. ``--analyze`` additionally evaluates the local CPU model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RATE = 22050
DESTINATION = ROOT / "input" / "production" / "audio"


def envelope(length: int, attack: float = .018, release: float = .08) -> np.ndarray:
    """Cosine edges avoid clicks while retaining an intentional rhythmic attack."""
    values = np.ones(length)
    rise = min(length // 2, round(attack * RATE))
    fall = min(length // 2, round(release * RATE))
    if rise:
        values[:rise] = np.sin(np.linspace(0, np.pi / 2, rise)) ** 2
    if fall:
        values[-fall:] = np.cos(np.linspace(0, np.pi / 2, fall)) ** 2
    return values


def voice(duration: float, start_hz: float, end_hz: float, *, amplitude=.40,
          attack=.018, release=.09, vowel="oo", bend=0.0) -> np.ndarray:
    """A simple harmonic humming instrument, never a recording of a person."""
    time = np.arange(round(duration * RATE)) / RATE
    fraction = time / max(duration, 1 / RATE)
    curve = fraction + bend * np.sin(np.pi * fraction)
    frequency = start_hz * np.power(end_hz / start_hz, curve)
    # Gentle vibrato fades in so it does not blur the attack.
    frequency *= 1 + .0035 * np.sin(2 * np.pi * 5.25 * time) * np.minimum(1, time / .18)
    phase = np.cumsum(frequency) * (2 * np.pi / RATE)
    harmonics = (1.0, .33, .10, .035, .015) if vowel == "oo" else (1.0, .42, .18, .08, .025)
    result = sum(level * np.sin((index + 1) * phase + .035 * index)
                 for index, level in enumerate(harmonics))
    return result * amplitude * envelope(len(time), attack, release) / sum(harmonics)


def plop(duration=.11, *, high=380, low=130, amplitude=.36, seed=1) -> np.ndarray:
    """Soft cartoon mouth-pop shape, synthesized from sine and filtered noise."""
    time = np.arange(round(duration * RATE)) / RATE
    frequency = low + (high - low) * np.exp(-time * 40)
    phase = np.cumsum(frequency) * (2 * np.pi / RATE)
    noise = np.random.default_rng(seed).normal(size=len(time))
    noise = np.convolve(noise, np.ones(5) / 5, mode="same")
    body = .9 * np.sin(phase) * np.exp(-time * 28)
    click = .10 * noise * np.exp(-time * 100)
    return (body + click) * amplitude * envelope(len(time), .003, .025)


def add(audio: np.ndarray, start: float, value: np.ndarray) -> None:
    offset = round(start * RATE)
    stop = min(len(audio), offset + len(value))
    audio[offset:stop] += value[:stop - offset]


def write_wav(name: str, audio: np.ndarray) -> dict:
    # One normalization pass, no limiter distortion. Peak is -1.5 dBFS.
    # Do not apply a global DC offset: intentional quiet gaps stay exact zero.
    audio *= 10 ** (-1.5 / 20) / max(float(np.max(np.abs(audio))), 1e-9)
    pcm = np.round(audio * 32767).astype("<i2")
    path = DESTINATION / f"{name}.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())
    return {"file": str(path.relative_to(ROOT)).replace("\\", "/"),
            "duration": len(audio) / RATE, "sample_rate": RATE,
            "peak_dbfs": round(20 * np.log10(np.max(np.abs(audio))), 2),
            "rms_dbfs": round(20 * np.log10(np.sqrt(np.mean(audio ** 2))), 2),
            "clipped_samples": int(np.sum(np.abs(audio) >= 1)),
            "samples": pcm.astype(float) / 32768}


def bounce() -> np.ndarray:
    audio = np.zeros(round(7.2 * RATE))
    # Three playful phrases: bop-bop, a longer boing, then a syncopated payoff.
    for index, (start, duration, high, low, amplitude) in enumerate([
        (.35, .34, 330, 247, .43), (1.00, .38, 392, 294, .43),
        (1.72, .78, 262, 440, .38), (3.10, .42, 392, 262, .43),
        (3.83, .40, 349, 247, .40), (4.62, 1.02, 220, 392, .38),
        (6.12, .45, 330, 220, .45),
    ]):
        add(audio, start, voice(duration, high, low, amplitude=amplitude,
                               vowel="oh" if index % 3 == 2 else "oo", bend=.15))
        add(audio, start, plop(amplitude=.22, seed=5090 + index))
    return audio


def fly() -> np.ndarray:
    audio = np.zeros(round(7.2 * RATE))
    add(audio, .35, plop(high=310, low=150, amplitude=.22, seed=72))
    add(audio, .35, voice(1.80, 196, 440, amplitude=.42, attack=.04, release=.14, bend=.15))
    # A whole second of digital silence is an intentional mid-flight freeze.
    add(audio, 3.30, voice(2.05, 440, 220, amplitude=.38, attack=.045,
                           release=.18, vowel="oh", bend=-.12))
    add(audio, 5.93, plop(high=440, low=196, amplitude=.28, seed=73))
    add(audio, 5.93, voice(.58, 262, 392, amplitude=.41, release=.12))
    return audio


def orbit() -> np.ndarray:
    audio = np.zeros(round(7.6 * RATE))
    for index, (start, duration, high, low) in enumerate([
        (.35, 1.18, 220, 330), (1.87, 1.12, 330, 220),
        (3.32, 1.43, 262, 440), (5.08, 1.47, 440, 247),
    ]):
        add(audio, start, voice(duration, high, low, amplitude=.41,
                               attack=.025, release=.13, bend=.12 * (-1) ** index))
        add(audio, start, plop(high=350, low=155, amplitude=.15, seed=90 + index))
    add(audio, 6.93, voice(.27, 330, 294, amplitude=.38, release=.08))
    add(audio, 6.93, plop(high=420, low=170, amplitude=.20, seed=95))
    return audio


CUE_SHEET = """# BOP! production test audio

All three files are **original synthetic sound performances, not human voice
recordings**. They use a monophonic harmonic humming instrument, gentle vibrato,
and synthesized sine/noise mouth-pop sounds. No speech, cloned voices, external
samples, music downloads, API services, or GPU processing are involved.

Generate: `.venv/Scripts/python.exe tools/make_production_audio.py --analyze`.
Format: mono PCM16 WAV, 22,050 Hz, normalized to -1.5 dBFS peak. The local Basic
Pitch CPU check measures model behavior; it does not turn these into real voices.

| File | Duration | Intended preset / direction | Cues (seconds) |
| --- | --- | --- | --- |
| `audio/bop-bounce.wav` | 7.2 s | Bounce; best first demonstration. Short bops punctuate long elastic boings. | 0.35 bop, 1.00 bop, 1.72–2.50 rising boing, 3.10 bop, 3.83 bop, 4.62–5.64 rising boing, 6.12 final low bop; then rest. |
| `audio/bop-freeze-flight.wav` | 7.2 s | Fly; visibly pause progress in silence, then resume in a new direction. | 0.35–2.15 rise, 2.30–3.15 intentional silent hold, 3.30–5.35 descending sweep, 5.93–6.51 upward final stamp; then rest. |
| `audio/bop-orbit.wav` | 7.6 s | Orbit; four singing arcs travel around the stage, with a small ending accent. | 0.35–1.53 rise, 1.87–2.99 fall, 3.32–4.75 rise, 5.08–6.55 fall, 6.93–7.20 short payoff. |

Silence follows every phrase so pauses are intentional. Strong attacks create
impacts; continuous pitch sweeps should not create extra note-by-note impacts.
Preset paths remain the app's fixed editable paths: the sounds control progress,
pitch expression, and impact rather than describing arbitrary semantic motion.

The BOP! identity and audio were created specifically for this local test. The
project MIT license applies to this synthesis script and its generated audio.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze", action="store_true", help="Analyze with local CPU Basic Pitch")
    args = parser.parse_args()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    records = []
    if args.analyze:
        sys.path.insert(0, str(ROOT / "src"))
        from voice_motion.analysis import analyze
    for name, generator in (("bop-bounce", bounce), ("bop-freeze-flight", fly), ("bop-orbit", orbit)):
        record = write_wav(name, generator())
        samples = record.pop("samples")
        if args.analyze:
            result = analyze(samples, RATE, engine="basic_pitch")
            analysis_dir = DESTINATION.parent / "analysis"
            analysis_dir.mkdir(exist_ok=True)
            (analysis_dir / f"{name}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            record["engine"] = result["engine"]
            record["impact_times"] = [event["time"] for event in result["events"]]
            active = [frame for frame in result["frames"] if frame["energy"] > 0]
            record["voiced_fraction"] = round(sum(frame["pitch_confidence"] > 0 for frame in active) / max(1, len(active)), 3)
            record["warnings"] = result["warnings"]
            if name == "bop-freeze-flight":
                hold = [frame["progress"] for frame in result["frames"] if 2.30 <= frame["time"] <= 3.15]
                record["silent_hold_progress_delta"] = round(max(hold) - min(hold), 8)
        records.append(record)
    (DESTINATION.parent / "AUDIO.md").write_text(CUE_SHEET, encoding="utf-8")
    (DESTINATION.parent / "audio-manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
