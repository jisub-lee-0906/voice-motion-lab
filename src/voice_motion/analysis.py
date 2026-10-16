"""Deterministic audio timing analysis. Input audio never leaves memory."""
from __future__ import annotations

import math
import numbers

import numpy as np

from .model import ModelUnavailableError, infer_pitch

MAX_DURATION = 15
MIN_RATE, MAX_RATE = 8000, 96000
FRAME_RATE = 60
MIN_EVENT_SPACING = .14


class AnalysisError(ValueError):
    pass


def _number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
        raise AnalysisError(f"{name} 값은 유한한 숫자여야 합니다.")
    if not low <= value <= high:
        raise AnalysisError(f"{name} 허용 범위는 {low}–{high}입니다.")
    return float(value)


def validate_input(samples, sample_rate, engine="auto", sensitivity=1.0, smoothing=.35):
    if isinstance(sample_rate, (bool, np.bool_)) or not isinstance(sample_rate, numbers.Integral) or not MIN_RATE <= sample_rate <= MAX_RATE:
        raise AnalysisError("sample_rate는 8000–96000 범위의 정수여야 합니다.")
    if not isinstance(engine, str) or engine not in ("auto", "signal", "basic_pitch"):
        raise AnalysisError("engine은 auto, signal, basic_pitch 중 하나여야 합니다.")
    sensitivity = _number(sensitivity, "sensitivity", .1, 3.0)
    smoothing = _number(smoothing, "smoothing", 0.0, 1.0)
    if not isinstance(samples, (list, tuple, np.ndarray)):
        raise AnalysisError("samples는 숫자 배열이어야 합니다.")
    if len(samples) > sample_rate * MAX_DURATION:
        raise AnalysisError("오디오는 최대 15초까지 분석할 수 있습니다.")
    if any(isinstance(x, (bool, np.bool_)) or not isinstance(x, numbers.Real) for x in samples):
        raise AnalysisError("samples에는 유한한 숫자만 사용할 수 있습니다.")
    try:
        array = np.asarray(samples, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AnalysisError("samples 배열을 읽을 수 없습니다.") from exc
    if array.ndim != 1 or not np.isfinite(array).all():
        raise AnalysisError("samples는 NaN/Infinity가 없는 1차원 숫자 배열이어야 합니다.")
    warnings = []
    if np.any(np.abs(array) > 1):
        warnings.append("-1~1을 넘는 입력 샘플을 클리핑했습니다.")
    return np.clip(array, -1, 1), int(sample_rate), engine, sensitivity, smoothing, warnings


def _autocorrelation_pitch(samples, rate, times, rms, sensitivity):
    pitch_rate = 8000
    reduced = np.interp(np.arange(max(1, math.ceil(len(samples) * pitch_rate / rate))) / pitch_rate,
                        np.arange(len(samples)) / rate, samples)
    pitch = np.full(len(times), .5)
    confidence = np.zeros(len(times))
    half_window = 320
    hann = np.hanning(half_window * 2)
    padded = np.pad(reduced, (half_window, half_window))
    lag_low, lag_high = 8, 123
    for index, time in enumerate(times):
        if rms[index] < .004 / sensitivity:
            continue
        center = min(round(time * pitch_rate), len(reduced) - 1)
        window = padded[center:center + 2 * half_window].copy()
        window -= np.mean(window)
        window *= hann
        spectrum = np.fft.rfft(window, n=2048)
        correlation = np.fft.irfft(spectrum * spectrum.conjugate(), n=2048).real
        if correlation[0] < 1e-10:
            continue
        lag = lag_low + int(np.argmax(correlation[lag_low:lag_high + 1]))
        strength = float(correlation[lag] / correlation[0])
        if strength < .55:
            continue
        offset = 0.0
        if lag_low < lag < lag_high:
            a, b, c = correlation[lag - 1:lag + 2]
            denominator = a - 2 * b + c
            if abs(denominator) > 1e-12:
                offset = float(np.clip(.5 * (a - c) / denominator, -.5, .5))
        frequency = pitch_rate / (lag + offset)
        pitch[index] = np.clip(np.log2(frequency / 70) / np.log2(1000 / 70), 0, 1)
        confidence[index] = np.clip(strength, 0, 1)
    return pitch, confidence


def _smooth(values, active, smoothing, default=0.0):
    out = np.full_like(values, default)
    alpha = 1 - .88 * smoothing
    previous = None
    for index, value in enumerate(values):
        if not active[index]:
            previous = None
            continue
        previous = value if previous is None else alpha * value + (1 - alpha) * previous
        out[index] = previous
    return out


def analyze(samples, sample_rate, engine="auto", sensitivity=1.0, smoothing=.35) -> dict:
    audio, rate, requested, sensitivity, smoothing, warnings = validate_input(samples, sample_rate, engine, sensitivity, smoothing)
    duration = len(audio) / rate
    if duration:
        times = np.minimum(np.arange(math.ceil(duration * FRAME_RATE) + 1) / FRAME_RATE, duration)
    else:
        times = np.array([0.0])
    energy = np.zeros(len(times))
    pitch = np.full(len(times), .5)
    confidence = np.zeros(len(times))
    onset = np.zeros(len(times))
    progress = np.zeros(len(times))
    events = []
    actual = "signal"
    if len(audio):
        # Prefix sums make the 40 ms RMS envelope independent of input sample rate.
        squared_sum = np.concatenate(([0.0], np.cumsum(audio * audio)))
        radius = max(1, round(.020 * rate))
        centers = np.rint(times * rate).astype(int)
        starts = np.maximum(0, centers - radius)
        ends = np.minimum(len(audio), centers + radius)
        rms = np.sqrt(np.maximum(0, (squared_sum[ends] - squared_sum[starts]) / np.maximum(1, ends - starts)))
        pitch, confidence = _autocorrelation_pitch(audio, rate, times, rms, sensitivity)
        nonvoiced = rms[confidence == 0]
        if len(nonvoiced) >= max(5, len(times) * .1):
            noise = float(np.quantile(nonvoiced, .2))
        else:
            noise = float(np.quantile(rms, .2)) * .05
        threshold = max(.006 / sensitivity, noise * 2.4 / math.sqrt(sensitivity))
        active = rms > threshold
        if np.any(active):
            reference = max(float(np.quantile(rms[active], .95)), threshold + 1e-8)
            raw_energy = np.where(active, np.sqrt(np.clip((rms - threshold) / (reference - threshold), 0, 1)), 0)
            energy = _smooth(raw_energy, active, smoothing)
            positive_attack = np.maximum(0, rms - np.concatenate(([0.0], rms[:-1])))
            onset = np.where(active, np.clip(positive_attack / max(reference * .30, 1e-8), 0, 1), 0)
        confidence[~active] = 0
        pitch[confidence == 0] = .5
    else:
        active = np.zeros(len(times), dtype=bool)

    if requested != "signal":
        try:
            if len(audio):
                model_pitch, model_confidence, model_onset = infer_pitch(audio, rate, times)
                pitch = np.where(active & (model_confidence > 0), model_pitch, .5)
                confidence = np.where(active, model_confidence, 0)
                # Basic Pitch's onsets are *note* starts: a smooth glissando can
                # cross many notes without any physical impact. Require a real
                # rise in the recorded RMS envelope before the model can affect
                # impact strength. Modest confidence weighting preserves taps
                # (which this pitched-instrument model may not recognize) while
                # never promoting a high note-onset into an energy-free event.
                onset = np.where(active, np.clip(onset * (.9 + .2 * model_onset), 0, 1), 0)
            else:
                # Explicit model selection validates installation even for empty audio.
                from .model import _get_session
                _get_session()
            actual = "basic_pitch"
        except ModelUnavailableError as exc:
            if requested == "basic_pitch":
                raise
            warnings.append(str(exc) + " 신호 분석으로 전환했습니다. AI 모델 분석 결과가 아닙니다.")

    pitch = _smooth(pitch, active & (confidence > 0), smoothing, default=.5)
    candidates = [i for i, strength in enumerate(onset) if strength >= .35 and
                  (i == 0 or strength > onset[i - 1]) and
                  (i == len(onset) - 1 or strength >= onset[i + 1])]
    selected = []
    for index in sorted(candidates, key=lambda i: float(onset[i]), reverse=True):
        if all(abs(times[index] - times[other]) >= MIN_EVENT_SPACING for other in selected):
            selected.append(index)
    for index in sorted(selected):
        event_time = float(times[index])
        # Refine transient positions against original samples (not the 60 Hz grid).
        start = max(0, round((event_time - .025) * rate))
        stop = min(len(audio), round((event_time + .025) * rate))
        if stop > start:
            event_time = (start + int(np.argmax(np.abs(audio[start:stop])))) / rate
        if not events or event_time - events[-1]["time"] >= MIN_EVENT_SPACING - 1e-6:
            events.append({"time": round(event_time, 6), "kind": "impact", "strength": round(float(onset[index]), 6)})
    if len(times) > 1:
        increments = np.concatenate(([0.0], np.diff(times) * energy[1:]))
        progress = np.cumsum(increments)
        if progress[-1] > 0:
            progress /= progress[-1]
        elif np.any(energy):
            progress[np.flatnonzero(energy)[0]:] = 1
    frames = [{"time": round(float(t), 6), "energy": round(float(e), 6),
               "pitch": round(float(p), 6), "pitch_confidence": round(float(c), 6),
               "onset": round(float(o), 6), "progress": round(float(g), 6)}
              for t, e, p, c, o, g in zip(times, energy, pitch, confidence, onset, progress)]
    return {"version": 1, "duration": duration, "sample_rate": rate, "engine": actual,
            "frames": frames, "events": events, "warnings": warnings}
