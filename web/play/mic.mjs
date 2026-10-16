export const RECORD_SECONDS = 5;
export const ANALYSIS_SAMPLE_RATE = 22050;
const aborted = () => new DOMException('녹음을 취소했어요.', 'AbortError');

/** Area-average downsampling avoids aliasing; output always has exactly five seconds. */
export function fiveSecondMono(source, sourceRate, targetRate = ANALYSIS_SAMPLE_RATE) {
  if (!source || !Number.isFinite(sourceRate) || sourceRate < 8000 || sourceRate > 192000 || !Number.isFinite(targetRate) || targetRate < 8000) throw new Error('오디오 샘플레이트를 확인해주세요.');
  const length = Math.round(targetRate * RECORD_SECONDS), result = new Float32Array(length), ratio = sourceRate / targetRate;
  for (let i = 0; i < length; i++) {
    const start = i * ratio, end = (i + 1) * ratio;
    let sum = 0;
    for (let n = Math.floor(start); n < Math.ceil(end); n++) { const weight = Math.min(end, n + 1) - Math.max(start, n); const value = n < source.length ? source[n] : 0; sum += (Number.isFinite(value) ? Math.max(-1, Math.min(1, value)) : 0) * weight; }
    result[i] = sum / ratio;
  }
  return result;
}

export function microphoneError(error) {
  const code = error?.name;
  if (code === 'NotAllowedError' || code === 'PermissionDeniedError') return '마이크 권한이 꺼져 있어요. 주소창의 마이크 권한을 허용하고 다시 눌러주세요. 데모로 먼저 놀아도 좋아요.';
  if (code === 'NotFoundError' || code === 'DevicesNotFoundError') return '마이크를 찾지 못했어요. 마이크가 있는 기기에서 열거나 합성음 데모로 놀아보세요.';
  if (code === 'NotReadableError' || code === 'TrackStartError') return '마이크를 열지 못했어요. 다른 앱의 마이크 사용을 마치고 다시 눌러주세요.';
  if (code === 'SecurityError') return '녹음하려면 안전한 HTTPS 주소에서 열어주세요. 지금은 합성음 데모를 이용할 수 있어요.';
  return error?.message || '녹음이 중단됐어요. 한 번 더 눌러주세요.';
}

/** No microphone access occurs until record() is called by a trusted button click. */
export class BrowserMicrophone {
  constructor({ mediaDevices = globalThis.navigator?.mediaDevices, AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext, secure = globalThis.isSecureContext } = {}) { this.mediaDevices = mediaDevices; this.AudioContextClass = AudioContextClass; this.secure = secure; this.session = null; }
  record({ onTick = () => {}, onLevel = () => {}, onStarted = () => {}, signal, audioContext } = {}) {
    this.cancel();
    if (signal?.aborted) return Promise.reject(aborted());
    if (!this.secure) return Promise.reject(new DOMException('녹음하려면 HTTPS 주소에서 열어주세요. 합성음 데모는 바로 쓸 수 있어요.', 'SecurityError'));
    if (!this.mediaDevices?.getUserMedia || !this.AudioContextClass) return Promise.reject(new Error('이 브라우저는 마이크 녹음을 지원하지 않아요. 최신 Safari·Chrome에서 열거나 데모를 눌러주세요.'));
    return new Promise((resolve, reject) => {
      const session = { active: true, stream: null, context: audioContext || new this.AudioContextClass(), ownContext: !audioContext, parts: [], length: 0, nodes: [], timer: null };
      this.session = session;
      const finish = (error) => {
        if (!session.active) return;
        session.active = false; clearInterval(session.timer); signal?.removeEventListener('abort', session.abort);
        for (const node of session.nodes) { try { node.disconnect(); } catch {} if ('onaudioprocess' in node) node.onaudioprocess = null; }
        for (const track of session.stream?.getTracks() || []) { track.onended = null; track.stop(); }
        if (session.ownContext) session.context.close().catch(() => {});
        if (this.session === session) this.session = null;
        if (error) reject(error);
        else { const raw = new Float32Array(session.length); let offset = 0; for (const part of session.parts) { raw.set(part, offset); offset += part.length; } resolve({ samples: fiveSecondMono(raw, session.context.sampleRate), sampleRate: ANALYSIS_SAMPLE_RATE }); }
      };
      session.abort = () => finish(aborted()); session.finish = finish; signal?.addEventListener('abort', session.abort, { once: true });
      // Request permission and unlock audio during the same user gesture.
      let resumed, requested;
      try {
        resumed = session.context.resume();
        requested = this.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false });
      } catch (error) { finish(error); return; }
      requested.then(stream => { if (session.active) session.stream = stream; else for (const track of stream.getTracks()) track.stop(); }).catch(() => {});
      Promise.all([requested, resumed]).then(([stream]) => {
        if (!session.active) { for (const track of stream.getTracks()) track.stop(); return; }
        session.stream = stream;
        for (const track of stream.getTracks()) track.onended = () => finish(new Error('마이크 연결이 끊겼어요. 연결한 다음 다시 녹음해주세요.'));
        const source = session.context.createMediaStreamSource(stream);
        const processor = session.context.createScriptProcessor(2048, 1, 1);
        const mute = session.context.createGain(); mute.gain.value = 0;
        session.nodes = [source, processor, mute]; source.connect(processor); processor.connect(mute); mute.connect(session.context.destination);
        const target = Math.round(session.context.sampleRate * RECORD_SECONDS); let lastInput = performance.now();
        processor.onaudioprocess = event => {
          if (!session.active) return;
          const input = event.inputBuffer.getChannelData(0), needed = Math.min(input.length, target - session.length);
          if (needed > 0) { const part = input.slice(0, needed); session.parts.push(part); session.length += part.length; let energy = 0; for (const value of part) energy += value * value; onLevel(Math.min(1, Math.sqrt(energy / part.length) * 7)); }
          lastInput = performance.now(); onTick(Math.min(RECORD_SECONDS, session.length / session.context.sampleRate));
          if (session.length >= target) finish();
        };
        session.timer = setInterval(() => { if (performance.now() - lastInput > 3500) finish(new Error('녹음이 잠시 멈췄어요. 이 화면을 켜둔 채로 다시 녹음해주세요.')); }, 500);
        onStarted(); onTick(0);
      }).catch(finish);
    });
  }
  cancel() { this.session?.finish(aborted()); }
  dispose() { this.cancel(); }
}
