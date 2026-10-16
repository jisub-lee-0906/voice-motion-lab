import { abortable, abortError, checkAbort, frameTiming, normalizeVideoOptions, resampleMono, safeVideoName } from './media-utils.mjs';
import { finalizeWebM } from './webm-finalize.mjs';

const AUDIO_RATE = 48000;
const VIDEO_BITRATE = 2600000;
const AUDIO_BITRATE = 96000;
const report = (options, progress, message) => { try { options.onProgress?.({ progress: Math.max(0, Math.min(1, progress)), message }); } catch { /* A UI observer must not leak media resources. */ } };
const closeEncoder = encoder => { if (encoder && encoder.state !== 'closed') { try { encoder.close(); } catch {} } };
const stopTracks = stream => stream?.getTracks().forEach(track => { try { track.stop(); } catch {} });

function createCanvas(options) {
  if (!globalThis.document?.createElement) throw new Error('이 브라우저에서 영상 캔버스를 만들 수 없어요.');
  const canvas = document.createElement('canvas');
  canvas.width = options.width; canvas.height = options.height;
  const context = canvas.getContext('2d', { alpha: false });
  if (!context) throw new Error('영상 캔버스를 열 수 없어요.');
  return { canvas, context };
}

async function codecSupported(Encoder, config, signal) {
  try { return (await abortable(Encoder.isConfigSupported(config), signal, 3000)).supported; }
  catch (error) { if (error.name === 'AbortError') throw error; return false; }
}

async function chooseCodecs(options) {
  if (![globalThis.VideoEncoder, globalThis.AudioEncoder, globalThis.VideoFrame, globalThis.AudioData].every(Boolean)) return null;
  const commonVideo = { width: options.width, height: options.height, bitrate: VIDEO_BITRATE, framerate: options.fps, hardwareAcceleration: 'prefer-software', latencyMode: 'quality' };
  const commonAudio = { sampleRate: AUDIO_RATE, numberOfChannels: 1, bitrate: AUDIO_BITRATE };
  const aac = { ...commonAudio, codec: 'mp4a.40.2' };
  if (await codecSupported(AudioEncoder, aac, options.signal)) {
    for (const codec of ['avc1.42001f', 'avc1.4d001f']) {
      const video = { ...commonVideo, codec, avc: { format: 'avc' } };
      if (await codecSupported(VideoEncoder, video, options.signal)) return { type: 'mp4', mimeType: 'video/mp4', video, audio: aac };
    }
  }
  const opus = { ...commonAudio, codec: 'opus' };
  if (await codecSupported(AudioEncoder, opus, options.signal)) {
    for (const codec of ['vp8', 'vp09.00.10.08']) {
      const video = { ...commonVideo, codec };
      if (await codecSupported(VideoEncoder, video, options.signal)) return { type: 'webm', mimeType: 'video/webm', video, audio: opus };
    }
  }
  return null;
}

async function encodeOffline(options, codecs) {
  report(options, .02, `${codecs.type.toUpperCase()} 영상을 빠르게 만들고 있어요…`);
  const library = await abortable(codecs.type === 'mp4' ? import('./vendor/mp4-muxer.mjs') : import('./vendor/webm-muxer.mjs'), options.signal);
  checkAbort(options.signal);
  const target = new library.ArrayBufferTarget();
  const muxer = new library.Muxer({ target,
    video: { codec: codecs.type === 'mp4' ? 'avc' : codecs.video.codec === 'vp8' ? 'V_VP8' : 'V_VP9', width: options.width, height: options.height, frameRate: options.fps },
    audio: { codec: codecs.type === 'mp4' ? 'aac' : 'A_OPUS', sampleRate: AUDIO_RATE, numberOfChannels: 1 },
    ...(codecs.type === 'mp4' ? { fastStart: 'in-memory' } : { streaming: false }), firstTimestampBehavior: 'offset' });
  const { canvas, context } = createCanvas(options);
  let videoEncoder, audioEncoder, failure, videoChunks = 0, audioChunks = 0;
  const fail = error => { failure ||= error instanceof Error ? error : new Error('영상 코덱을 실행할 수 없어요.'); };
  const check = () => { checkAbort(options.signal); if (failure) throw failure; };
  const cancel = () => { closeEncoder(videoEncoder); closeEncoder(audioEncoder); };
  options.signal?.addEventListener('abort', cancel, { once: true });
  try {
    videoEncoder = new VideoEncoder({ output(chunk, meta) { try { muxer.addVideoChunk(chunk, meta); videoChunks++; } catch (error) { fail(error); } }, error: fail });
    audioEncoder = new AudioEncoder({ output(chunk, meta) { try { muxer.addAudioChunk(chunk, meta); audioChunks++; } catch (error) { fail(error); } }, error: fail });
    videoEncoder.configure(codecs.video); audioEncoder.configure(codecs.audio);
    const audio = resampleMono(options.samples, options.sampleRate, AUDIO_RATE, options.duration);
    const audioBlock = codecs.type === 'mp4' ? 1024 : 960;
    for (let start = 0; start < audio.length; start += audioBlock) {
      check();
      const data = new AudioData({ format: 'f32-planar', sampleRate: AUDIO_RATE, numberOfChannels: 1,
        numberOfFrames: Math.min(audioBlock, audio.length - start), timestamp: Math.round(start / AUDIO_RATE * 1e6),
        data: audio.subarray(start, Math.min(audio.length, start + audioBlock)) });
      try { audioEncoder.encode(data); } finally { data.close(); }
      if (audioEncoder.encodeQueueSize > 16) await abortable(audioEncoder.flush(), options.signal);
    }
    await abortable(audioEncoder.flush(), options.signal); check();
    closeEncoder(audioEncoder);
    const count = Math.ceil(options.duration * options.fps);
    for (let index = 0; index < count; ++index) {
      check();
      const timing = frameTiming(index, options.fps, options.duration);
      const drawing = options.renderFrame(context, timing.time);
      if (drawing?.then) await abortable(drawing, options.signal, 5000);
      check();
      const frame = new VideoFrame(canvas, { timestamp: timing.timestamp, duration: timing.duration });
      try { videoEncoder.encode(frame, { keyFrame: index % (options.fps * 2) === 0 }); } finally { frame.close(); }
      if (videoEncoder.encodeQueueSize > 5) await abortable(videoEncoder.flush(), options.signal);
      if (index % 4 === 0) {
        report(options, .10 + .78 * (index + 1) / count, '소리와 움직임을 영상에 담고 있어요…');
        // Yield even on synchronous software encoders so cancel/UI remain usable.
        await abortable(new Promise(resolve => setTimeout(resolve, 0)), options.signal);
      }
    }
    await abortable(videoEncoder.flush(), options.signal); check();
    if (!videoChunks || !audioChunks) throw new Error('영상 또는 원음 트랙이 비어 있어요.');
    report(options, .94, '저장할 파일을 마무리하고 있어요…');
    muxer.finalize(); check();
    let blob = new Blob([target.buffer], { type: codecs.mimeType });
    if (codecs.type === 'webm') blob = await abortable(finalizeWebM(blob, options.duration), options.signal);
    if (!blob.size) throw new Error('영상 파일이 만들어지지 않았어요.');
    report(options, 1, '영상이 준비됐어요!');
    return { blob, mimeType: codecs.mimeType, extension: codecs.type, width: options.width, height: options.height, duration: options.duration,
      method: `webcodecs-${codecs.type === 'mp4' ? 'h264-aac-mp4' : 'vp-opus-webm'}` };
  } finally {
    options.signal?.removeEventListener('abort', cancel);
    cancel(); canvas.width = 1; canvas.height = 1;
  }
}

const RECORDER_TYPES = ['video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4', 'video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus', 'video/webm'];

async function encodeRealtime(options) {
  if (!globalThis.MediaRecorder || !globalThis.MediaStream) throw new Error('이 브라우저는 영상 저장을 지원하지 않아요. 다른 최신 브라우저에서 다시 열어 주세요.');
  const mimeType = RECORDER_TYPES.find(type => { try { return MediaRecorder.isTypeSupported(type); } catch { return false; } });
  if (!mimeType) throw new Error('이 브라우저에서 MP4 또는 WebM을 만들 수 없어요.');
  const { canvas, context } = createCanvas(options);
  if (typeof canvas.captureStream !== 'function') throw new Error('이 브라우저는 캔버스 영상 저장을 지원하지 않아요.');
  const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!AudioContextClass && !options.audioContext) throw new Error('이 브라우저에서 원음을 영상에 담을 수 없어요.');
  const ownsContext = !options.audioContext;
  const audioContext = options.audioContext || new AudioContextClass();
  let destination, source, canvasStream, stream, recorder, timer;
  let abortListener, visibilityListener, stateListener;
  try {
    checkAbort(options.signal);
    if (audioContext.state === 'closed') throw new Error('오디오가 닫혔어요. 다시 녹음해 주세요.');
    if (audioContext.state !== 'running') {
      try { await abortable(audioContext.resume(), options.signal, 1500); }
      catch (error) { if (error.name === 'AbortError') throw error; throw new Error('소리 재생 권한이 필요해요. 버튼을 눌러 영상을 다시 준비해 주세요.'); }
    }
    if (audioContext.state !== 'running') throw new Error('소리 재생 권한이 필요해요. 버튼을 눌러 다시 시도해 주세요.');
    destination = audioContext.createMediaStreamDestination();
    const buffer = audioContext.createBuffer(1, options.samples.length, options.sampleRate);
    buffer.copyToChannel(options.samples, 0);
    source = audioContext.createBufferSource(); source.buffer = buffer;
    // Deliberately connect ONLY to the recording destination, never speakers.
    source.connect(destination);
    const firstDrawing = options.renderFrame(context, 0);
    if (firstDrawing?.then) await abortable(firstDrawing, options.signal, 5000);
    canvasStream = canvas.captureStream(options.fps);
    stream = new MediaStream([...canvasStream.getVideoTracks(), ...destination.stream.getAudioTracks()]);
    if (!stream.getVideoTracks().length || !stream.getAudioTracks().length) throw new Error('영상 또는 원음 트랙을 만들 수 없어요.');
    recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: VIDEO_BITRATE, audioBitsPerSecond: AUDIO_BITRATE });
    const chunks = [];
    report(options, 0, '이 브라우저에서는 5초 동안 영상을 준비해요. 잠깐만요…');
    await new Promise((resolve, reject) => {
      let settled = false, watchdog;
      const finish = (error) => {
        if (settled) return;
        settled = true; clearTimeout(timer); clearTimeout(watchdog);
        source.onended = null;
        if (error) reject(error); else resolve();
      };
      abortListener = () => finish(abortError());
      visibilityListener = () => { if (document.hidden) finish(new Error('화면을 벗어나 영상 준비가 중단됐어요. 돌아와서 다시 시도해 주세요.')); };
      stateListener = () => { if (audioContext.state !== 'running') finish(new Error('오디오가 중단됐어요. 다시 준비해 주세요.')); };
      options.signal?.addEventListener('abort', abortListener, { once: true });
      document.addEventListener('visibilitychange', visibilityListener);
      audioContext.addEventListener('statechange', stateListener);
      recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
      recorder.onerror = event => finish(event.error || new Error('영상 저장이 중단됐어요.'));
      recorder.onstop = () => finish();
      const stop = () => {
        clearTimeout(timer);
        try { options.renderFrame(context, options.duration); if (recorder.state !== 'inactive') recorder.stop(); }
        catch (error) { finish(error); }
      };
      const start = audioContext.currentTime;
      const tick = () => {
        if (settled) return;
        const elapsed = Math.min(options.duration, Math.max(0, audioContext.currentTime - start));
        try { options.renderFrame(context, elapsed); }
        catch (error) { finish(error); return; }
        report(options, .02 + .90 * elapsed / options.duration, '원음과 움직임을 녹화하고 있어요…');
        if (elapsed >= options.duration) { stop(); return; }
        timer = setTimeout(tick, Math.max(4, 1000 / options.fps));
      };
      try {
        recorder.start();
        source.onended = stop;
        source.start();
        timer = setTimeout(tick, 0);
        watchdog = setTimeout(() => finish(new Error('영상 준비 시간이 초과됐어요. 다시 시도해 주세요.')), options.duration * 1000 + 5000);
        if (options.signal?.aborted) abortListener();
      } catch (error) { finish(error); }
    });
    checkAbort(options.signal);
    const actualType = recorder.mimeType || mimeType;
    const extension = /^video\/mp4\b/i.test(actualType) ? 'mp4' : /^video\/webm\b/i.test(actualType) ? 'webm' : null;
    if (!extension || !chunks.length) throw new Error('올바른 영상 파일이 만들어지지 않았어요.');
    let blob = new Blob(chunks, { type: actualType });
    report(options, .95, '저장할 파일 정보를 정리하고 있어요…');
    if (extension === 'webm') blob = await abortable(finalizeWebM(blob, options.duration), options.signal);
    report(options, 1, '영상이 준비됐어요!');
    return { blob, mimeType: actualType, extension, width: options.width, height: options.height, duration: options.duration, method: `mediarecorder-${extension}` };
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', abortListener);
    if (visibilityListener) document.removeEventListener('visibilitychange', visibilityListener);
    if (stateListener) audioContext.removeEventListener('statechange', stateListener);
    if (recorder) { recorder.onstop = recorder.ondataavailable = recorder.onerror = null; if (recorder.state !== 'inactive') { try { recorder.stop(); } catch {} } }
    if (source) { source.onended = null; try { source.stop(); } catch {} try { source.disconnect(); } catch {} }
    try { destination?.disconnect(); } catch {}
    stopTracks(stream); stopTracks(canvasStream); stopTracks(destination?.stream);
    if (ownsContext && audioContext.state !== 'closed') { try { await audioContext.close(); } catch {} }
    canvas.width = 1; canvas.height = 1;
  }
}

/** Local-only video creation. Optional audioContext must have been gesture-unlocked by the caller. */
export async function createMemeVideo(input) {
  const options = normalizeVideoOptions(input);
  checkAbort(options.signal);
  const codecs = await chooseCodecs(options);
  if (codecs) {
    try { return await encodeOffline(options, codecs); }
    catch (error) { if (error.name === 'AbortError' || options.signal?.aborted) throw abortError();
      report(options, 0, '빠른 저장을 사용할 수 없어 기본 영상 저장으로 전환해요…'); }
  }
  return encodeRealtime(options);
}

/** Call only from an explicit save click. Returns the real format's filename. */
export function downloadVideo(video, name = 'bop-meme') {
  if (!(video?.blob instanceof Blob) || !video.blob.size || !['mp4', 'webm'].includes(video.extension)) throw new TypeError('저장할 영상이 아직 없어요.');
  const filename = safeVideoName(name, video.extension);
  const url = URL.createObjectURL(video.blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.rel = 'noopener';
  document.body.append(link); link.click(); link.remove();
  // Safari may read the Blob after the click handler returns.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  return { method: 'download', filename };
}

/** Native file sharing is initiated immediately inside the caller's trusted click. */
export async function shareVideo(video, name = 'bop-meme') {
  if (!(video?.blob instanceof Blob) || !video.blob.size) throw new TypeError('공유할 영상이 아직 없어요.');
  const filename = safeVideoName(name, video.extension);
  const file = new File([video.blob], filename, { type: video.mimeType || video.blob.type });
  let canShare = false;
  try { canShare = typeof navigator.share === 'function' && typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] }); } catch {}
  if (canShare) {
    try { await navigator.share({ files: [file], title: 'BOP! 내 소리로 만든 밈' }); return { method: 'share', filename }; }
    catch (error) { if (error.name === 'AbortError') return { method: 'cancelled', filename }; }
  }
  return downloadVideo(video, name);
}
