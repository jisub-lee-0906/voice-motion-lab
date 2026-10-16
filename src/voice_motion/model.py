"""Lazy, bounded Basic Pitch ONNX inference; never downloads models."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import threading

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_RATE = 22050
WINDOW_SAMPLES = 43844
FRAME_HOP = 256
OVERLAP_SAMPLES = 30 * FRAME_HOP
MAX_CHUNKS = 12
_lock = threading.Lock()
_session = None
_session_key = None


class ModelUnavailableError(RuntimeError):
    """The explicitly requested local model cannot be used."""


def model_path() -> Path:
    return Path(os.environ.get("VOICE_MOTION_MODEL", str(ROOT / ".runtime/models/basic_pitch.onnx"))).expanduser().resolve()


def model_info() -> dict:
    path = model_path()
    runtime = importlib.util.find_spec("onnxruntime") is not None
    exists = path.is_file()
    info = {
        "name": "Basic Pitch", "format": "ONNX", "path": str(path),
        "installed": exists, "runtime_available": runtime, "available": exists and runtime,
        "provider": "CPUExecutionProvider", "intra_op_threads": 2,
        "sample_rate": MODEL_RATE, "window_samples": WINDOW_SAMPLES,
        "license": "Apache-2.0", "loaded": False,
    }
    if exists:
        info["size_bytes"] = path.stat().st_size
    if _session is not None and _session_key and _session_key[0] == str(path):
        info.update(loaded=True, inputs=[{"name": x.name, "shape": x.shape} for x in _session.get_inputs()],
                    outputs=[{"name": x.name, "shape": x.shape} for x in _session.get_outputs()])
    return info


def _get_session():
    global _session, _session_key
    path = model_path()
    if not path.is_file():
        raise ModelUnavailableError("Basic Pitch 모델 파일이 없습니다. 프로젝트 전용 모델 설치를 확인하세요.")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ModelUnavailableError("프로젝트 가상환경에 ONNX Runtime이 없습니다.") from exc
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _session is None or _session_key != key:
            try:
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.log_severity_level = 3
                candidate = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
                inputs = candidate.get_inputs()
                if len(inputs) != 1 or len(inputs[0].shape) != 3 or inputs[0].shape[1:] != [WINDOW_SAMPLES, 1]:
                    raise ValueError("Unexpected Basic Pitch input shape")
                _session, _session_key = candidate, key
            except Exception as exc:
                raise ModelUnavailableError("Basic Pitch 모델을 CPU로 불러오지 못했습니다.") from exc
        return _session


def _identify_outputs(session, values: list[np.ndarray]) -> dict[str, np.ndarray]:
    """Use actual ONNX names/shapes, including the official TF export names."""
    outputs = session.get_outputs()
    result = {}
    unnamed_88 = []
    # Official Spotify icassp_2022 export: :0 contour, :1 note, :2 onset.
    official_names = {"StatefulPartitionedCall:0": "contour", "StatefulPartitionedCall:1": "note",
                      "StatefulPartitionedCall:2": "onset"}
    for metadata, value in zip(outputs, values):
        value = np.asarray(value)
        if value.ndim != 3 or value.shape[0] != 1 or not np.isfinite(value).all():
            raise ModelUnavailableError("Basic Pitch 출력 형태나 수치가 올바르지 않습니다.")
        name = metadata.name.lower()
        kind = next((x for x in ("contour", "onset", "note") if x in name), None)
        kind = kind or official_names.get(metadata.name)
        if value.shape[-1] == 264:
            kind = "contour"
        if kind in ("note", "onset") and value.shape[-1] != 88:
            raise ModelUnavailableError("Basic Pitch 음표 출력 크기가 다릅니다.")
        if kind:
            result[kind] = np.clip(value[0], 0, 1)
        elif value.shape[-1] == 88:
            unnamed_88.append(metadata.name)
    if "note" not in result or "onset" not in result:
        raise ModelUnavailableError("Basic Pitch note/onset 출력 이름을 확인할 수 없습니다: " + ", ".join(unnamed_88))
    return result


def infer_pitch(samples: np.ndarray, sample_rate: int, times: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    session = _get_session()
    duration = len(samples) / sample_rate
    count = max(1, round(duration * MODEL_RATE))
    resampled = np.interp(np.arange(count) / MODEL_RATE, np.arange(len(samples)) / sample_rate,
                          samples, left=0, right=0).astype(np.float32)
    half_overlap = OVERLAP_SAMPLES // 2
    # Center-pad the first/last window. Keep each prediction's absolute source time.
    padded = np.pad(resampled, (half_overlap, half_overlap))
    hop = WINDOW_SAMPLES - OVERLAP_SAMPLES
    starts = list(range(0, max(1, len(padded) - OVERLAP_SAMPLES), hop))
    if len(starts) > MAX_CHUNKS:
        raise ModelUnavailableError("Basic Pitch 분석 창 수가 안전 한도를 넘었습니다.")
    all_times, all_notes, all_onsets = [], [], []
    input_name = session.get_inputs()[0].name
    for index, start in enumerate(starts):
        chunk = padded[start:start + WINDOW_SAMPLES]
        chunk = np.pad(chunk, (0, WINDOW_SAMPLES - len(chunk)))
        try:
            values = session.run(None, {input_name: chunk.reshape(1, WINDOW_SAMPLES, 1)})
        except Exception as exc:
            raise ModelUnavailableError("Basic Pitch CPU 추론에 실패했습니다.") from exc
        output = _identify_outputs(session, values)
        note = output["note"]
        onset = output["onset"]
        if len(note) != len(onset):
            raise ModelUnavailableError("Basic Pitch 출력 시간축이 서로 다릅니다.")
        frame_times = (start - half_overlap + np.arange(len(note)) * FRAME_HOP) / MODEL_RATE
        keep = (frame_times >= 0) & (frame_times <= duration)
        # Retain the central prediction region, avoiding averaging boundary padding.
        if index > 0:
            keep &= np.arange(len(note)) >= 15
        if index < len(starts) - 1:
            keep &= np.arange(len(note)) < len(note) - 15
        all_times.extend(frame_times[keep].tolist())
        all_notes.extend(note[keep])
        all_onsets.extend(onset[keep])
    if not all_times:
        return np.full_like(times, .5), np.zeros_like(times), np.zeros_like(times)
    order = np.argsort(all_times)
    source_times = np.asarray(all_times)[order]
    notes = np.asarray(all_notes)[order]
    onsets = np.asarray(all_onsets)[order]
    confidence = np.max(notes, axis=1)
    midi = 21 + np.argmax(notes, axis=1)
    frequency = 440.0 * np.power(2.0, (midi - 69) / 12)
    pitch = np.clip(np.log2(frequency / 70) / np.log2(1000 / 70), 0, 1)
    pitch = np.interp(times, source_times, pitch)
    confidence = np.interp(times, source_times, confidence, left=0, right=0)
    onset = np.interp(times, source_times, np.max(onsets, axis=1), left=0, right=0)
    pitch[confidence < .20] = .5
    confidence[confidence < .20] = 0
    return pitch, confidence, onset
