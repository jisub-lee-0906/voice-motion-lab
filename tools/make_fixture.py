"""Generate a clearly synthetic performance for repeatable integration tests."""
import math
from pathlib import Path
import struct
import wave
import random

ROOT = Path(__file__).resolve().parents[1]


def main():
    rate = 22050
    duration = 5.0
    rng = random.Random(5090)
    samples = []
    phase = 0.0
    for index in range(int(rate * duration)):
        time = index / rate
        value = 0.0
        if 0.45 <= time < 2.2:
            local = time - 0.45
            envelope = min(1.0, local / .08) * min(1.0, (2.2 - time) / .14)
            frequency = 210 + 180 * local / 1.75
            phase += 2 * math.pi * frequency / rate
            value = envelope * .28 * (math.sin(phase) + .25 * math.sin(2 * phase))
        elif 3.25 <= time < 4.55:
            local = time - 3.25
            envelope = min(1.0, local / .12) * min(1.0, (4.55 - time) / .18)
            frequency = 380 - 170 * local / 1.3
            phase += 2 * math.pi * frequency / rate
            value = envelope * .25 * (math.sin(phase) + .2 * math.sin(2 * phase))
        for onset in (2.75, 4.7):
            elapsed = time - onset
            if 0 <= elapsed < .10:
                value += .55 * math.exp(-elapsed * 45) * (rng.random() * 2 - 1)
        samples.append(max(-1, min(1, value)))
    destination = ROOT / "tests" / "fixtures" / "synthetic-performance.wav"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destination), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(struct.pack("<h", round(value * 32767)) for value in samples))
    print(f"Synthetic fixture (not a real voice): {destination}")


if __name__ == "__main__":
    main()
