"""Real, offline CPU transcription and word alignment in a disposable model process."""
from __future__ import annotations
import argparse
import ctypes
import gc
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = ROOT / '.runtime/mv-models'


def model_status():
    return {'available': all((MODEL_ROOT / name / 'manifest.json').is_file() for name in ('asr', 'aligner')),
            'engine': 'qwen3_asr_0.6b_cpu', 'alignment': 'qwen3_forced_aligner_0.6b_cpu',
            'device': 'cpu', 'threads': 2}


def group_words(words, duration, max_chars=46):
    """Use predicted word timestamps; group them without manufacturing lyric text."""
    segments, pending = [], []
    previous_end = 0.0
    for value in words:
        text = str(value.get('text', '')).strip()
        if not text:
            continue
        start = max(previous_end, min(duration, float(value.get('start_time', value.get('start', 0)))))
        end = max(start, min(duration, float(value.get('end_time', value.get('end', start)))))
        # Keep recognized short words even when the aligner predicts a zero span.
        # The source text is more important than silently deleting a lyric word.
        if end - start < .02:
            end = min(duration, start + .02)
        word = {'start': round(start, 3), 'end': round(end, 3), 'text': text}
        if pending and (start - pending[-1]['end'] > .55 or end - pending[0]['start'] > 4.0 or
                        sum(len(x['text']) + 1 for x in pending) + len(text) > max_chars):
            segments.append({'start': pending[0]['start'], 'end': pending[-1]['end'],
                             'text': ' '.join(x['text'] for x in pending), 'words': pending})
            pending = []
        pending.append(word)
        previous_end = end
        if re.search(r'[.!?。！？]$', text):
            segments.append({'start': pending[0]['start'], 'end': end,
                             'text': ' '.join(x['text'] for x in pending), 'words': pending})
            pending = []
    if pending:
        segments.append({'start': pending[0]['start'], 'end': pending[-1]['end'],
                         'text': ' '.join(x['text'] for x in pending), 'words': pending})
    return segments


def transcribe(audio_path, supplied_text=None, progress=None):
    # This function runs in the project's CPU model subprocess, never the web thread.
    os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HOME'] = str(ROOT / '.cache/huggingface')
    os.environ['TORCH_HOME'] = str(ROOT / '.cache/torch')
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    os.environ['OMP_NUM_THREADS'] = '2'
    os.environ['MKL_NUM_THREADS'] = '2'
    import av
    import numpy as np
    import torch
    from transformers import AutoProcessor, AutoModelForMultimodalLM, AutoModelForTokenClassification
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    def report(value, message):
        if progress:
            progress(value, message)
    report(.01, 'CPU에서 노래를 읽는 중')
    blocks = []
    with av.open(str(audio_path)) as source:
        converter = av.AudioResampler(format='fltp', layout='mono', rate=16000)
        for frame in source.decode(audio=0):
            blocks.extend(converter.resample(frame))
        blocks.extend(converter.resample(None))
    samples = np.concatenate([block.to_ndarray().ravel() for block in blocks]).astype(np.float32)
    duration = len(samples) / 16000
    if not 1 <= duration <= 60.05 or not np.isfinite(samples).all():
        raise ValueError('노래는 유한한 1~60초 오디오여야 합니다.')
    if float(np.sqrt(np.mean(samples**2))) < .0008:
        raise ValueError('소리가 거의 없는 파일입니다. 노래가 들리는 구간을 선택해주세요.')
    if not model_status()['available']:
        raise RuntimeError('공개 전사 모델이 준비되지 않았습니다. tools/setup_mv_models.py를 실행하세요.')
    report(.05, '공개 가사 전사 모델을 CPU에 준비하는 중')
    asr_dir = MODEL_ROOT / 'asr'
    align_dir = MODEL_ROOT / 'aligner'
    processor = AutoProcessor.from_pretrained(asr_dir, local_files_only=True, trust_remote_code=False)
    warnings = ['자동 전사와 음절 시점은 노래·반주에 따라 틀릴 수 있습니다. 결과에서 확인해주세요.']
    language, parts = None, []
    begun = time.monotonic()
    if supplied_text:
        # Detect language only for tokenizer selection; text provenance stays explicit.
        text = supplied_text.strip()
        language = 'Korean' if re.search(r'[가-힣]', text) else 'English'
        engine = 'provided_lyrics'
    else:
        model = AutoModelForMultimodalLM.from_pretrained(asr_dir, dtype=torch.float32,
            local_files_only=True, trust_remote_code=False, attn_implementation='sdpa').eval()
        engine = 'qwen3_asr_0.6b_cpu'
        # The product caps input at one minute. Transcribe that excerpt together:
        # cutting at arbitrary 12-second boundaries clipped sung/spoken words.
        step = 60 * 16000
        for index, start in enumerate(range(0, len(samples), step)):
            end = min(len(samples), start + step)
            chunk = samples[start:end]
            report(.12 + .38 * start / len(samples), f'가사 전사 중 · {start / 16000:.0f} / {duration:.0f}초')
            inputs = processor.apply_transcription_request(audio=chunk)
            with torch.inference_mode():
                output = model.generate(**inputs, max_new_tokens=min(840, max(256, round(duration * 14))), do_sample=False)
            token_ids = output[:, inputs['input_ids'].shape[1]:]
            parsed = processor.decode(token_ids, return_format='parsed')[0]
            content = parsed.get('transcription', '').strip()
            if content:
                parts.append({'start': start / 16000, 'end': end / 16000, 'text': content})
                language = language or parsed.get('language')
            del inputs, output, token_ids
        del model
        gc.collect()
        text = ' '.join(part['text'] for part in parts)
        if not text:
            raise ValueError('가사를 인식하지 못했습니다. 보컬이 들리는 구간을 선택하거나 선택 가사를 입력해주세요.')
    del processor
    gc.collect()
    report(.55, '부른 단어의 시점을 맞추는 중')
    align_processor = AutoProcessor.from_pretrained(align_dir, local_files_only=True, trust_remote_code=False)
    align_model = AutoModelForTokenClassification.from_pretrained(align_dir, dtype=torch.float32,
        local_files_only=True, trust_remote_code=False, attn_implementation='sdpa').eval()
    if not parts:
        parts = [{'start': 0, 'end': duration, 'text': text}]
    words = []
    for index, part in enumerate(parts):
        chunk = samples[round(part['start'] * 16000):round(part['end'] * 16000)]
        aligned_inputs, word_lists = align_processor.prepare_forced_aligner_inputs(
            audio=chunk, transcript=part['text'], language=language or 'English')
        with torch.inference_mode():
            outputs = align_model(**aligned_inputs)
        values = align_processor.decode_forced_alignment(logits=outputs.logits,
            input_ids=aligned_inputs['input_ids'], word_lists=word_lists,
            timestamp_token_id=align_model.config.timestamp_token_id)[0]
        for value in values:
            words.append({'text': value['text'], 'start_time': float(value['start_time']) + part['start'],
                          'end_time': float(value['end_time']) + part['start']})
        report(.55 + .4 * (index + 1) / len(parts), '가사의 발음 시점을 정리하는 중')
        del aligned_inputs, outputs
    segments = group_words(words, duration)
    if not segments:
        raise ValueError('인식된 가사의 발음 시점을 확인하지 못했습니다. 다른 구간을 선택해주세요.')
    return {'engine': engine, 'language': language or 'Unknown', 'text': text,
            'segments': segments, 'warnings': warnings,
            'timing_source': 'qwen3_forced_aligner_0.6b_cpu',
            'device': 'cpu', 'threads': 2, 'elapsed_seconds': round(time.monotonic() - begun, 3)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--lyrics-file', type=Path)
    args = parser.parse_args()
    def emit(value, message):
        print(json.dumps({'progress': value, 'message': message}, ensure_ascii=False), flush=True)
    try:
        result = transcribe(args.audio, args.lyrics_file.read_text(encoding='utf-8') if args.lyrics_file else None, emit)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        emit(1, '가사와 타이밍 분석 완료')
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), flush=True)
        raise
