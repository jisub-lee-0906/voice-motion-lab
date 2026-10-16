// Pure helpers shared by both browser encoding paths. No microphone or network.
export function abortError() { return new DOMException('영상 만들기를 취소했어요.', 'AbortError'); }
export function checkAbort(signal) { if (signal?.aborted) throw abortError(); }

export function abortable(promise, signal, timeout = 15000) {
  checkAbort(signal);
  return new Promise((resolve, reject) => {
    let finished = false;
    const finish = (callback, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
      callback(value);
    };
    const cancel = () => finish(reject, abortError());
    const timer = setTimeout(() => finish(reject, new Error('영상 준비가 멈췄어요. 다시 시도해 주세요.')), timeout);
    signal?.addEventListener('abort', cancel, { once: true });
    Promise.resolve(promise).then(value => finish(resolve, value), error => finish(reject, error));
  });
}

export function normalizeVideoOptions(options) {
  if (!options || typeof options.renderFrame !== 'function') throw new TypeError('영상 그리기 함수가 필요해요.');
  const { duration = 5, width = 720, height = 960, fps = 24, sampleRate = 22050 } = options;
  if (!Number.isFinite(duration) || duration <= 0 || duration > 15) throw new RangeError('영상은 15초 이내여야 해요.');
  if (![width, height, fps, sampleRate].every(Number.isInteger) || width < 2 || height < 2 || width > 1920 || height > 1920 || width % 2 || height % 2 || fps < 1 || fps > 60 || sampleRate < 8000 || sampleRate > 96000) throw new RangeError('영상 크기나 샘플레이트가 올바르지 않아요.');
  if (!(Array.isArray(options.samples) || ArrayBuffer.isView(options.samples)) || options.samples instanceof DataView || options.samples.length > sampleRate * 15) throw new TypeError('녹음 샘플이 필요해요.');
  const samples = new Float32Array(Math.round(duration * sampleRate));
  for (let index = 0; index < options.samples.length; ++index) {
    const sample = options.samples[index];
    if (typeof sample !== 'number' || !Number.isFinite(sample)) throw new TypeError('녹음 샘플에 유효하지 않은 값이 있어요.');
    if (index < samples.length) samples[index] = Math.min(1, Math.max(-1, sample));
  }
  return { ...options, duration, width, height, fps, sampleRate, samples };
}

export function resampleMono(samples, sourceRate, targetRate, duration) {
  const output = new Float32Array(Math.round(duration * targetRate));
  for (let index = 0; index < output.length; ++index) {
    const position = index * sourceRate / targetRate;
    const left = Math.floor(position), fraction = position - left;
    const a = samples[Math.min(left, samples.length - 1)] || 0;
    const b = samples[Math.min(left + 1, samples.length - 1)] || 0;
    output[index] = a + fraction * (b - a);
  }
  return output;
}

export function frameTiming(index, fps, duration) {
  const timestamp = Math.round(index / fps * 1e6);
  const end = Math.min(Math.round(duration * 1e6), Math.round((index + 1) / fps * 1e6));
  return { timestamp, duration: end - timestamp, time: index / fps };
}

export function safeVideoName(name, extension) {
  const base = String(name || 'bop-meme').replace(/\.(mp4|webm)$/i, '').replace(/[^\p{L}\p{N}_-]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 72) || 'bop-meme';
  return `${base}.${extension === 'mp4' ? 'mp4' : 'webm'}`;
}
