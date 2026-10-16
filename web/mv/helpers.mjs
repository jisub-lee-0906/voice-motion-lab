export const MAX_SECONDS = 60;
export const MAX_UPLOAD_BYTES = 24 * 1024 * 1024;
export const MULTIPART_RESERVE_BYTES = 64 * 1024;
export const THEMES = new Set(['auto', 'bloom', 'fracture', 'orbit']);
export const JOB_ID = /^[a-zA-Z0-9_-]{1,80}$/;

export function formatTime(seconds) {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.round(seconds)) : 0;
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function validateDuration(duration, maxSeconds = MAX_SECONDS) {
  if (!Number.isFinite(duration) || duration < 1) throw new Error('1초 이상의 재생 가능한 음악 파일을 선택해 주세요.');
  if (duration > maxSeconds + 0.001) {
    throw new Error(`음악은 ${maxSeconds}초까지 만들 수 있어요. 15–45초 정도의 구간을 잘라 다시 선택해 주세요. 선택한 음악: ${formatTime(duration)}`);
  }
}

export function maxAudioFileBytes(maxBodyBytes = MAX_UPLOAD_BYTES) {
  const limit = Number.isFinite(maxBodyBytes) && maxBodyBytes > 0 ? Math.min(maxBodyBytes, MAX_UPLOAD_BYTES) : MAX_UPLOAD_BYTES;
  return Math.max(0, Math.floor(limit) - MULTIPART_RESERVE_BYTES);
}

export function validateAudioFile(file, maxBodyBytes = MAX_UPLOAD_BYTES) {
  const limit = maxAudioFileBytes(maxBodyBytes);
  if (!Number.isSafeInteger(file?.size) || file.size <= 0 || file.size > limit) {
    throw new Error(`비어 있지 않은 ${(limit / (1024 * 1024)).toFixed(1)}MB 이하 음악을 선택해 주세요. 15–45초 구간을 권장합니다.`);
  }
}

export function inspectDecodedAudio(audio, maxSeconds = MAX_SECONDS) {
  validateDuration(audio?.duration, maxSeconds);
  const rate = audio.sampleRate;
  const channels = audio.numberOfChannels;
  const length = audio.length;
  if (!Number.isFinite(rate) || rate < 8000 || rate > 96000 || !Number.isInteger(channels) || ![1,2].includes(channels)) {
    throw new Error('음악은 8–96kHz 모노 또는 스테레오여야 합니다.');
  }
  if (!Number.isSafeInteger(length) || length <= 0 || Math.abs(length/rate-audio.duration) > .05) {
    throw new Error('음악의 샘플 형식이나 채널 길이를 읽을 수 없어요.');
  }
  let squareSum = 0;
  let peak = 0;
  // Inspect each channel in place. Never mix, resample, copy or replace the upload.
  for (let channel = 0; channel < channels; channel += 1) {
    const samples = audio.getChannelData(channel);
    if (samples.length !== length) throw new Error('음악 채널 길이가 올바르지 않아요.');
    for (let index = 0; index < samples.length; index += 1) {
      const sample = samples[index];
      if (!Number.isFinite(sample) || Math.abs(sample) > 16) throw new Error('음악 파일에 올바르지 않은 소리 값이 있어요.');
      squareSum += sample * sample;
      peak = Math.max(peak, Math.abs(sample));
    }
  }
  const rms = Math.sqrt(squareSum / (length * channels));
  if (rms < .0008) throw new Error('소리가 거의 없는 파일이에요. 보컬이나 음악이 들리는 구간을 선택해 주세요.');
  return {duration:audio.duration, sample_rate:rate, channels, rms, peak};
}

export function uploadMetadata(value) {
  const title = typeof value?.title === 'string' ? value.title.trim() : '';
  const theme = value?.theme ?? 'auto';
  const seed = value?.seed ?? 7;
  const lyrics = value?.lyrics ?? '';
  if (!title || title.length > 100 || /[\u0000-\u001f]/.test(title)) throw new Error('작품 이름은 제어 문자 없이 1–100자로 입력해 주세요.');
  if (!THEMES.has(theme)) throw new Error('장면 테마를 다시 선택해 주세요.');
  if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) throw new Error('시드가 올바르지 않아요.');
  if (typeof lyrics !== 'string' || lyrics.length > 6000 || lyrics.includes('\0')) throw new Error('선택 가사는 최대 6,000자로 입력해 주세요.');
  const result = {title, theme, seed};
  if (lyrics.trim()) result.lyrics = lyrics.trim();
  return result;
}

export function createUploadBody(file, metadata, maxBodyBytes = MAX_UPLOAD_BYTES) {
  validateAudioFile(file, maxBodyBytes);
  const body = new FormData();
  body.append('metadata', JSON.stringify(uploadMetadata(metadata)));
  // Preserve original bytes/type. Never send a user-controlled filesystem name.
  body.append('audio', file, 'source-audio');
  return body;
}

export function artifactUrl(value, id, kind, origin) {
  if (!JOB_ID.test(id) || !['video.mp4', 'preview.webm', 'project.json', 'storyboard.jpg'].includes(kind)) throw new Error('결과 주소가 올바르지 않아요.');
  const url = new URL(value, origin);
  const expected = `/api/mv/jobs/${id}/artifacts/${kind}`;
  if (url.origin !== new URL(origin).origin || url.pathname !== expected || url.search || url.hash || url.username || url.password) throw new Error('안전하지 않은 결과 주소를 차단했어요.');
  return url.href;
}

export function fileTitle(name) {
  return String(name || 'Untitled').replace(/\.[^.]+$/, '').slice(0, 100);
}

export function validateJob(value, expectedId) {
  if (!value || !JOB_ID.test(value.id || '') || (expectedId && value.id !== expectedId)) throw new Error('작업 응답을 확인할 수 없어요. 다시 연결해 주세요.');
  if (!['queued', 'transcribing', 'directing', 'rendering', 'succeeded', 'failed'].includes(value.status)) throw new Error('알 수 없는 작업 상태를 받았어요.');
  return value;
}

export function completedElapsedSeconds(job) {
  if (!['succeeded', 'failed'].includes(job?.status)) return null;
  if (!Number.isFinite(job.created_at) || !Number.isFinite(job.updated_at) || job.updated_at < job.created_at) return null;
  return job.updated_at - job.created_at;
}

export function healthMessage(health) {
  if (!health) return '로컬 서버에 연결되지 않았어요. 프로젝트의 MV 서버를 실행한 뒤 다시 확인해 주세요.';
  if (!health.asr?.available) return '음성 인식 엔진이 아직 준비되지 않았어요. 프로젝트의 ASR 설치 상태를 확인해 주세요.';
  if (!health.comfy?.available) return '영상 제작 엔진에 연결되지 않았어요. 이 프로젝트 전용 Comfy 및 SDK 프록시 상태를 확인해 주세요.';
  return '';
}

export function waveformPeaks(samples, count = 80) {
  if (!samples?.length) return [];
  const blocks = Math.min(Math.max(1, Math.floor(count)), samples.length);
  return Array.from({length: blocks}, (_, index) => {
    const start = Math.floor(index * samples.length / blocks);
    const end = Math.min(samples.length, Math.max(start + 1, Math.floor((index + 1) * samples.length / blocks)));
    let maximum = 0;
    for (let point = start; point < end; point += 1) maximum = Math.max(maximum, Math.abs(samples[point]));
    return maximum;
  });
}
