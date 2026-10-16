"""Independently inspect an actual local MV MP4, its source audio and directing JSON.

No inference/server calls or SciPy dependency. Run with this project's .mv-venv.
This checks technical output integrity, not artistic quality or lyric accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import av
import numpy as np

RATE = 16000


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def audio_samples(path):
    chunks = []
    start = None
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError('No audio stream')
        stream = container.streams.audio[0]
        stream.codec_context.thread_count = 2
        codec = stream.codec_context.name
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=RATE)
        for frame in container.decode(stream):
            if start is None:
                start = float(frame.time) if frame.time is not None else 0.0
            chunks.extend(resampler.resample(frame))
        chunks.extend(resampler.resample(None))
    if not chunks:
        raise ValueError('Decoded audio is empty')
    samples = np.concatenate([frame.to_ndarray().ravel() for frame in chunks]).astype(np.float64)
    if not np.isfinite(samples).all():
        raise ValueError('Non-finite decoded audio')
    return samples, start, codec


def compare_audio(source, output, source_start=0.0, output_start=0.0, video_start=0.0, max_lag_seconds=.2):
    """Positive lag means matching source samples occur later in decoded output."""
    source = np.asarray(source, dtype=np.float64)
    output = np.asarray(output, dtype=np.float64)
    if not source.size or not output.size or not np.isfinite(source).all() or not np.isfinite(output).all():
        raise ValueError('Audio comparison requires finite nonempty samples')
    x = source - source.mean()
    y = output - output.mean()
    size = 1 << (len(x) + len(y) - 1).bit_length()
    fft_correlation = np.fft.irfft(np.fft.rfft(y, size) * np.conj(np.fft.rfft(x, size)), size)
    limit = min(round(max_lag_seconds * RATE), len(x) - 1, len(y) - 1)
    lags = np.arange(-limit, limit + 1)
    lag = int(lags[np.argmax(fft_correlation[lags % size])])
    source_index, output_index = max(0, -lag), max(0, lag)
    count = min(len(x) - source_index, len(y) - output_index)
    a, b = x[source_index:source_index+count], y[output_index:output_index+count]
    power = float(np.dot(a, a) * np.dot(b, b))
    similarity = float(np.dot(a, b) / math.sqrt(power)) if power > 1e-20 else 0.0
    gain = float(np.dot(a, b) / max(float(np.dot(a, a)), 1e-20))
    return {'sample_rate': RATE, 'search_window_seconds': max_lag_seconds,
            'best_lag_samples': lag, 'best_lag_seconds': lag / RATE,
            'timestamp_adjusted_offset_seconds': lag / RATE + output_start - source_start - video_start,
            'correlation': similarity, 'gain': gain, 'compared_samples': count,
            'source_duration': len(source)/RATE, 'decoded_output_duration': len(output)/RATE,
            'source_rms': float(np.sqrt(np.mean(source**2))),
            'output_rms': float(np.sqrt(np.mean(output**2)))}


def host_path_locations(value, location='$'):
    """Inspect string values, never emit the potentially private string itself."""
    findings = []
    if isinstance(value, dict):
        for key, child in value.items():
            findings.extend(host_path_locations(child, f'{location}.{key}'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(host_path_locations(child, f'{location}[{index}]'))
    elif isinstance(value, str):
        if (re.search(r'(?i)(?:\b[a-z]:[\\/]|file://|\\\\[^\\\s]+\\)', value)
                or re.search(r'(?:^|[\s"\'])/(?:home|Users|root|tmp|var|mnt|opt|usr)(?:/|\b)', value)):
            findings.append(location)
    return findings


def inspect_project(project, failures):
    check(project.get('format') == 'voice-motion-mv-project', 'Unexpected directing JSON format', failures)
    duration = project.get('duration')
    check(isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0,
          'Invalid project duration', failures)
    shots = project.get('shots')
    if not isinstance(shots, list) or not shots:
        failures.append('No directing shots')
        return {'shots': 0}
    cursor = 0.0
    gaps, overlaps, invalid = [], [], []
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            invalid.append(index)
            continue
        start, end = shot.get('start'), shot.get('end')
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)) or end <= start:
            invalid.append(index)
            continue
        if start > cursor + .002: gaps.append(index)
        if start < cursor - .002: overlaps.append(index)
        cursor = end
    check(not invalid, 'Invalid shot boundaries', failures)
    check(not gaps, 'Shot coverage contains gaps', failures)
    check(not overlaps, 'Shots are out of order or overlap', failures)
    if isinstance(duration, (int, float)):
        check(abs(cursor-duration) <= .01, 'Shot coverage does not reach full duration', failures)
    host_paths = host_path_locations(project)
    check(not host_paths, 'Directing JSON contains absolute host filesystem paths', failures)
    return {'shots': len(shots), 'covered_until': cursor, 'gap_indices': gaps,
            'overlap_indices': overlaps, 'invalid_indices': invalid,
            'absolute_host_path_locations': host_paths,
            'transcription_engine': project.get('transcript', {}).get('engine'),
            'timing_source': project.get('transcript', {}).get('timing_source')}


def inspect(video_path, project_path, source_path):
    failures = []
    project = json.loads(Path(project_path).read_text(encoding='utf-8'))
    directing = inspect_project(project, failures)
    expected = {'width': project.get('width'), 'height': project.get('height'),
                'fps': project.get('fps'), 'duration': project.get('duration')}
    for key, value in expected.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            failures.append(f'Invalid project {key}')
    if failures:
        return {'ok': False, 'failures': failures, 'directing': directing}
    pts, deviations, differences, hashes = [], [], [], set()
    previous = None
    with av.open(str(video_path)) as container:
        check(container.format.name in ('mov,mp4,m4a,3gp,3g2,mj2', 'mp4'), 'Unexpected container format', failures)
        check(len(container.streams.video) == 1, 'Expected exactly one video stream', failures)
        check(len(container.streams.audio) == 1, 'Expected exactly one audio stream', failures)
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 2
        codec = stream.codec_context.name
        fps = float(stream.average_rate) if stream.average_rate else 0.0
        metadata_duration = container.duration / av.time_base if container.duration is not None else None
        for index, frame in enumerate(container.decode(stream)):
            check((frame.width,frame.height) == (expected['width'],expected['height']), 'Decoded dimensions mismatch', failures)
            if frame.time is None:
                failures.append('Video frame lacks timestamp')
                continue
            pts.append(float(frame.time))
            # Every decoded frame is inspected at a fixed low-resolution luma grid.
            grid = frame.to_ndarray(format='gray')[::6,::6]
            deviations.append(float(np.std(grid)))
            if index % max(1, round(fps/2)) == 0:
                hashes.add(hashlib.sha256(grid.tobytes()).hexdigest())
                if previous is not None:
                    differences.append(float(np.mean(np.abs(grid.astype(np.int16)-previous))))
                previous = grid.astype(np.int16)
    frame_count = len(pts)
    check(frame_count > 0, 'No decoded video frames', failures)
    if not frame_count:
        return {'ok':False, 'failures':failures, 'directing':directing}
    decoded_duration = pts[-1] - pts[0] + 1/expected['fps']
    check(codec == 'h264', 'Video codec is not H.264', failures)
    check(abs(fps-expected['fps']) < .001, 'Frame rate mismatch', failures)
    check(frame_count == math.ceil(expected['duration']*expected['fps']), 'Decoded frame count mismatch', failures)
    check(abs(decoded_duration-expected['duration']) <= 1/expected['fps']+.005, 'Decoded duration mismatch', failures)
    check(metadata_duration is not None and abs(metadata_duration-expected['duration']) < .15, 'Missing or incorrect container duration', failures)
    check(all(b>a for a,b in zip(pts,pts[1:])), 'Video timestamps are not strictly increasing', failures)
    check(abs(pts[0]) < .05, 'Video does not begin near zero', failures)
    cadence_error = max(abs((b-a)-1/expected['fps']) for a,b in zip(pts,pts[1:])) if len(pts)>1 else 0
    check(cadence_error < .001, 'Irregular video frame cadence', failures)
    check(min(deviations) >= 4, 'At least one frame is blank or nearly flat', failures)
    motion = float(np.mean(differences)) if differences else 0.0
    check(len(hashes) >= 3 and motion > .5, 'Video lacks measurable scene motion', failures)
    source_audio, source_start, _ = audio_samples(source_path)
    output_audio, output_start, audio_codec = audio_samples(video_path)
    audio = compare_audio(source_audio,output_audio,source_start,output_start,pts[0])
    check(audio_codec == 'aac', 'Audio codec is not AAC', failures)
    check(audio['source_rms'] > .0008 and audio['output_rms'] > .0008, 'Source or result audio is silent', failures)
    check(audio['correlation'] >= .95, 'Output audio does not match source', failures)
    check(abs(audio['timestamp_adjusted_offset_seconds']) <= .2, 'Audio/video source offset exceeds 0.2 seconds', failures)
    check(abs(audio['decoded_output_duration']-expected['duration']) < .15, 'Decoded audio duration mismatch', failures)
    check(.85 <= audio['gain'] <= 1.15, 'Output audio gain differs materially from source', failures)
    return {'ok':not failures, 'failures':list(dict.fromkeys(failures)),
            'scope':'Actual decoded technical output; no artistic-quality or lyric-accuracy claim',
            'video':{'filename':Path(video_path).name,'codec':codec,'width':expected['width'],'height':expected['height'],
                     'fps':fps,'decoded_frames':frame_count,'metadata_duration':metadata_duration,
                     'decoded_duration':decoded_duration,'first_timestamp':pts[0],'last_timestamp':pts[-1],
                     'max_cadence_error':cadence_error,'minimum_luma_std':min(deviations),
                     'mean_luma_std':float(np.mean(deviations)),'half_second_unique_frames':len(hashes),
                     'half_second_mean_absolute_motion':motion},
            'audio':{'codec':audio_codec,**audio},'directing':directing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--report',type=Path)
    args = parser.parse_args()
    try:
        report = inspect(args.video,args.project,args.source)
    except Exception as error:
        # Avoid leaking private absolute paths from third-party decoder exceptions.
        report = {'ok':False,'failures':[f'Inspection raised {type(error).__name__}; inspect input media and JSON.']}
    text = json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(text+'\n',encoding='utf-8')
    print(text)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
