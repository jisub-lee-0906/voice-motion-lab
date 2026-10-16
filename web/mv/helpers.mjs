export const MAX_SECONDS = 60;
export const SAMPLE_RATE = 16000;
export const THEMES = new Set(['auto', 'bloom', 'fracture', 'orbit']);
export const JOB_ID = /^[a-zA-Z0-9_-]{1,80}$/;

export function formatTime(seconds) {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.round(seconds)) : 0;
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function validateDuration(duration, maxSeconds = MAX_SECONDS) {
  if (!Number.isFinite(duration) || duration <= 0) throw new Error('재생할 수 있는 음악 파일을 선택해 주세요.');
  if (duration > maxSeconds + 0.001) {
    throw new Error(`음악은 ${maxSeconds}초까지 만들 수 있어요. 15–45초 정도의 구간을 잘라 다시 선택해 주세요. 선택한 음악: ${formatTime(duration)}`);
  }
}

export function downmix(channels) {
  if (!Array.isArray(channels) || channels.length === 0 || channels.length > 32) throw new Error('오디오 채널을 읽을 수 없어요.');
  const length = channels[0].length;
  if (!length || channels.some(channel => channel.length !== length)) throw new Error('오디오 채널 길이가 올바르지 않아요.');
  const result = new Float32Array(length);
  for (const channel of channels) {
    for (let index = 0; index < length; index += 1) {
      const sample = channel[index];
      if (!Number.isFinite(sample)) throw new Error('음악 파일에 올바르지 않은 소리 값이 있어요.');
      result[index] += sample / channels.length;
    }
  }
  for (let index = 0; index < length; index += 1) result[index] = Math.max(-1, Math.min(1, result[index]));
  return result;
}

export function validateSamples(samples, sampleRate = SAMPLE_RATE) {
  if (sampleRate !== SAMPLE_RATE || !samples?.length || samples.length > MAX_SECONDS * SAMPLE_RATE) throw new Error('음악 길이 또는 샘플 형식이 올바르지 않아요.');
  for (const sample of samples) if (!Number.isFinite(sample) || sample < -1 || sample > 1) throw new Error('음악 데이터가 올바르지 않아요.');
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
