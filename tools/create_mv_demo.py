"""Original procedural music + synthetic Windows TTS fixture, never human microphone audio."""
import json
from pathlib import Path
import wave
import av
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RATE, DURATION = 16000, 32
LINES = [
    'In the quiet city, I carry a little light.',
    'Through the broken windows, flowers learn to fly.',
    'I keep the pieces of a dream inside my hands.',
    'Let the night fall. We will bloom again.',
    'Round and round, the stars remember every name.',
    'From the cracks we rise, and find our way back home.',
]


def decode(path):
    frames = []
    with av.open(str(path)) as source:
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=RATE)
        for frame in source.decode(audio=0):
            frames.extend(resampler.resample(frame))
        frames.extend(resampler.resample(None))
    return np.concatenate([f.to_ndarray().ravel() for f in frames])


def make():
    rng = np.random.default_rng(27)
    audio = np.zeros(RATE * DURATION, dtype=np.float64)
    def add(at, values):
        start = round(at * RATE)
        count = min(len(values), len(audio) - start)
        if count > 0:
            audio[start:start+count] += values[:count]
    beat = .5
    for index in range(round(DURATION / beat)):
        at = index * beat
        strength = .45 if at < 8 else .70 if at < 16 else 1
        t = np.arange(round(.24 * RATE)) / RATE
        kick = np.sin(2*np.pi*(45*t + 85*.035*(1-np.exp(-t/.035)))) * np.exp(-t*22)
        add(at, kick * .16 * strength)
        if index % 2:
            add(at, rng.normal(0, .028*strength, len(t)) * np.exp(-t*38))
        if at >= 8:
            hat_t = np.arange(round(.065 * RATE))/RATE
            add(at + .25, rng.normal(0, .018*strength, len(hat_t))*np.exp(-hat_t*65))
        notes = [220, 261.6256, 329.6276, 293.6648]
        frequency = notes[(index // 8) % len(notes)] * (2 if index % 4 >= 2 else 1)
        t = np.arange(round(.44*RATE))/RATE
        envelope = np.minimum(1, t/.015) * np.exp(-t*7)
        add(at, (.025*np.sin(2*np.pi*frequency*t)+.008*np.sin(2*np.pi*frequency*2*t))*envelope)
    starts = [1.1, 6.2, 11.0, 16.0, 21.1, 26.0]
    for index, at in enumerate(starts):
        voice = decode(ROOT / f'temp/mv-demo-voice/line-{index}.wav')
        peak = np.max(np.abs(voice))
        if peak > 0:
            voice = voice / peak * .62
        add(at, voice)
    audio *= np.minimum(1, np.arange(len(audio))/RATE/.15)
    audio *= np.minimum(1, (len(audio)-np.arange(len(audio)))/RATE/.6)
    audio = np.clip(audio, -1, 1)
    target = ROOT / 'tests/fixtures/mv-original-demo.wav'
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(RATE)
        out.writeframes((audio*32767).astype('<i2').tobytes())
    expected = {'synthetic': True, 'human_recording': False, 'title': 'From the Cracks',
                'music': 'Original procedural composition by this project',
                'vocal': 'Synthetic Microsoft Zira TTS, spoken phrases, not natural singing',
                'duration': DURATION, 'sample_rate': RATE,
                'lyrics': [{'start': at, 'text': text} for at, text in zip(starts, LINES)]}
    (ROOT / 'tests/fixtures/mv-demo-provenance.json').write_text(json.dumps(expected, indent=2), encoding='utf-8')
    print(json.dumps({'file': str(target), 'duration': DURATION, 'synthetic': True}))


if __name__ == '__main__':
    make()
