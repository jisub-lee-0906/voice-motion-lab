"""Bounded, offline upload decoding to a stereo/rate-preserving float WAV.

Only project-owned callers choose the target; no client path or URL is opened.
The source bytes are opened through custom memory IO, with network/file protocols
and playlist demuxers excluded. No downmix or sample-rate conversion is performed.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path

import av
import numpy as np

MAX_BYTES = 24 * 1024 * 1024
MAX_SECONDS = 60
ALLOWED_FORMATS = frozenset({'wav', 'mp3', 'flac', 'ogg', 'mov', 'aac', 'aiff'})
OPEN_OPTIONS = {'protocol_whitelist': 'pipe', 'format_whitelist': ','.join(sorted(ALLOWED_FORMATS)),
                'enable_drefs': '0', 'use_absolute_path': '0'}


def normalize_uploaded_audio(raw: bytes, target: Path) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('원본 음악은 비어 있지 않은 24MiB 이하 파일이어야 합니다.')
    if raw.lstrip().startswith((b'#EXTM3U', b'[playlist]', b'<?xml', b'<MPD')):
        raise ValueError('외부 주소나 재생 목록 대신 실제 음악 파일을 선택해주세요.')
    target = Path(target)
    if target.name != 'source.wav' or not target.parent.is_dir() or target.parent.is_symlink() or target.exists() or target.is_symlink():
        raise ValueError('정규화 출력은 새 프로젝트 작업의 source.wav여야 합니다.')
    temporary = target.with_name('source.wav.normalizing')
    if temporary.exists() or temporary.is_symlink():
        raise ValueError('이 작업의 오디오 임시 파일이 이미 존재합니다.')
    created_temporary = False
    try:
        with av.open(io.BytesIO(raw), mode='r', options=dict(OPEN_OPTIONS)) as source:
            formats = set(source.format.name.split(','))
            if not formats.intersection(ALLOWED_FORMATS):
                raise ValueError('WAV·MP3·M4A·FLAC·OGG·AAC·AIFF 음악 파일만 지원합니다.')
            covers = list(source.streams.video)
            if len(source.streams.audio) != 1 or any(not (stream.disposition & av.stream.Disposition.attached_pic) for stream in covers):
                raise ValueError('음악 트랙 하나만 있는 오디오 파일을 선택해주세요. 영상 파일은 지원하지 않습니다.')
            stream = source.streams.audio[0]
            stream.codec_context.thread_count = 2
            rate = stream.codec_context.sample_rate
            channels = stream.codec_context.channels
            if not isinstance(rate, int) or not 8000 <= rate <= 96000 or channels not in (1, 2):
                raise ValueError('음악은 8–96kHz 모노 또는 스테레오여야 합니다.')
            if stream.duration is not None and stream.time_base is not None:
                advertised = float(stream.duration * stream.time_base)
                if not math.isfinite(advertised) or advertised > MAX_SECONDS + .15:
                    raise ValueError('노래는 최대 60초 구간을 선택해주세요.')
            codec = stream.codec_context.name
            layout = 'mono' if channels == 1 else 'stereo'
            converter = av.AudioResampler(format='fltp', layout=layout, rate=rate)
            count = 0
            square_sum = np.zeros(channels, dtype=np.float64)
            peaks = np.zeros(channels, dtype=np.float64)
            # Exclusively create the one temporary file owned by this invocation.
            with temporary.open('xb') as output_file:
                created_temporary = True
                with av.open(output_file, mode='w', format='wav') as output:
                    audio = output.add_stream('pcm_f32le', rate=rate)
                    audio.layout = layout
                    audio.codec_context.thread_count = 1

                    def write_frame(frame):
                        nonlocal count
                        if frame.sample_rate != rate or len(frame.layout.channels) != channels:
                            raise ValueError('음악 도중 샘플레이트나 채널 형식이 바뀌는 파일은 지원하지 않습니다.')
                        data = frame.to_ndarray()
                        if data.shape != (channels, frame.samples) or not np.isfinite(data).all() or np.max(np.abs(data), initial=0) > 16:
                            raise ValueError('음악 파일에 올바르지 않은 오디오 값이 있습니다.')
                        if count + frame.samples > MAX_SECONDS * rate:
                            raise ValueError('노래는 최대 60초 구간을 선택해주세요.')
                        square_sum[:] += np.square(data, dtype=np.float64).sum(axis=1)
                        peaks[:] = np.maximum(peaks, np.max(np.abs(data), axis=1, initial=0))
                        # Pack samples without changing values, rate or channel order.
                        packed = np.ascontiguousarray(data.T.reshape(1, -1))
                        encoded_frame = av.AudioFrame.from_ndarray(packed, format='flt', layout=layout)
                        encoded_frame.sample_rate = rate
                        encoded_frame.pts = count
                        for packet in audio.encode(encoded_frame):
                            output.mux(packet)
                        count += frame.samples

                    for index, packet in enumerate(source.demux(stream)):
                        if index >= 30000:
                            raise ValueError('음악 파일의 패킷 수가 안전한 처리 범위를 넘었습니다.')
                        for frame in packet.decode():
                            if frame.sample_rate != rate or len(frame.layout.channels) != channels:
                                raise ValueError('음악의 샘플레이트나 채널 정보를 확인할 수 없습니다.')
                            for converted in converter.resample(frame):
                                write_frame(converted)
                    for converted in converter.resample(None):
                        write_frame(converted)
                    if count < rate:
                        raise ValueError('노래는 1초 이상 60초 이하 구간을 선택해주세요.')
                    rms = np.sqrt(square_sum / count)
                    if float(np.sqrt(np.mean(square_sum / count))) < .0008:
                        raise ValueError('소리가 거의 없는 파일입니다. 보컬이나 음악이 들리는 구간을 선택해주세요.')
                    for packet in audio.encode(None):
                        output.mux(packet)
            # Atomic publish only after complete decode, validation and WAV flush.
            temporary.replace(target)
            created_temporary = False
            return {'duration': count / rate, 'sample_rate': rate, 'channels': channels,
                    'codec': codec, 'source_format': source.format.name,
                    'ignored_attached_pictures': len(covers),
                    'source_sha256': hashlib.sha256(raw).hexdigest(), 'source_bytes': len(raw),
                    'normalized_format': 'wav', 'normalized_codec': 'pcm_f32le',
                    'normalized_sample_rate': rate, 'normalized_channels': channels,
                    'normalized_samples_per_channel': count,
                    'normalized_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'channel_rms': rms.tolist(), 'channel_peak': peaks.tolist(),
                    'resampled': False, 'downmixed': False}
    except ValueError:
        raise
    except (av.FFmpegError, OSError, OverflowError) as error:
        raise ValueError('음악 파일을 안전하게 읽을 수 없습니다. WAV·MP3·M4A 등 실제 오디오 파일을 선택해주세요.') from error
    finally:
        if created_temporary:
            # Exact path exclusively created above; never clean any other file.
            temporary.unlink(missing_ok=True)


def main():
    """Single-purpose child worker; native decoder output is discarded by parent."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    source, target, result = args.source.resolve(), args.target.resolve(), args.result.resolve()
    if source.name != 'upload.audio' or target.name != 'source.wav' or result.name != 'normalization-result.json' or not source.parent == target.parent == result.parent:
        return 2
    # Filesystem locations are constructed by the trusted manager, never HTTP data.
    av.logging.set_level(av.logging.PANIC)
    try:
        with source.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        metadata = normalize_uploaded_audio(raw, target)
        record = {'ok': True, 'audio': metadata}
        code = 0
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '음악 파일을 안전하게 읽을 수 없습니다.'
        # Domain errors contain product text only; never return native host paths.
        if len(message) > 300 or any(part in message for part in ('Traceback', ':\\', ':/', 'http://', 'https://')):
            message = '음악 파일을 안전하게 읽을 수 없습니다.'
        record = {'ok': False, 'error': message[:300]}
        code = 2
    result.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
