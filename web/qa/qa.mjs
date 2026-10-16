import { BrowserMicrophone } from '../play/mic.mjs';
import { BrowserAnalyzer } from '../play/analysis-client.mjs';
import { createMemeVideo, downloadVideo } from '../play/export-video.mjs';
import { CHARACTERS, createSceneRenderer, STAGE } from '../play/scene.mjs';

const $ = id => document.getElementById(id);
const nodes = Object.fromEntries(['run', 'late-abort', 'codecs', 'cancel', 'download', 'status', 'progress', 'preview', 'video', 'result'].map(id => [id, $(id)]));
let controller = null, readyVideo = null, blobUrl = null, previewLoop = null, currentRecord = null;
const elapsed = start => Math.round((performance.now() - start) * 10) / 10;
const abortError = () => new DOMException('QA를 취소했습니다.', 'AbortError');

function show(record) { currentRecord = record; nodes.result.textContent = JSON.stringify(record, null, 2); }
function status(message, progress) { nodes.status.textContent = message; if (typeof progress === 'number') nodes.progress.value = progress; }
function busy(active) {
  for (const name of ['run', 'late-abort', 'codecs']) nodes[name].disabled = active;
  nodes.cancel.disabled = !active;
  nodes.download.disabled = active || !readyVideo;
}
function stopPreview() { if (previewLoop !== null) cancelAnimationFrame(previewLoop); previewLoop = null; }
function startPreview(renderFrame) {
  stopPreview();
  const context = nodes.preview.getContext('2d'), start = performance.now();
  const draw = () => { renderFrame(context, ((performance.now() - start) / 1000) % 5); previewLoop = requestAnimationFrame(draw); };
  draw();
}
async function imageForCharacter(character = 'bounce') {
  const image = new Image();
  await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error('BOP! 원본 SVG를 읽을 수 없습니다.')); image.src = CHARACTERS[character].asset; });
  return image;
}
function syntheticGraph(context) {
  const destination = context.createMediaStreamDestination();
  const oscillator = context.createOscillator(), gain = context.createGain();
  oscillator.type = 'triangle'; oscillator.frequency.value = 220; gain.gain.value = 0;
  oscillator.connect(gain); gain.connect(destination); // No connection to speakers.
  oscillator.start();
  return { destination, oscillator, gain,
    perform() {
      const start = context.currentTime;
      gain.gain.cancelScheduledValues(start); gain.gain.setValueAtTime(0, start);
      for (const [time, end, from, to, level] of [[.18, 1.45, 196, 392, .27], [1.72, 2.13, 330, 247, .30], [2.85, 4.37, 392, 220, .26], [4.55, 4.88, 294, 392, .28]]) {
        oscillator.frequency.setValueAtTime(from, start + time);
        oscillator.frequency.exponentialRampToValueAtTime(to, start + end);
        gain.gain.setValueAtTime(0, start + time);
        gain.gain.linearRampToValueAtTime(level, start + time + .025);
        gain.gain.setValueAtTime(level, start + end - .065);
        gain.gain.linearRampToValueAtTime(0, start + end);
      }
    },
    dispose() { try { oscillator.stop(); } catch {} oscillator.disconnect(); gain.disconnect(); destination.disconnect(); destination.stream.getTracks().forEach(track => track.stop()); },
  };
}

function waitForMetadata(url, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); nodes.video.removeEventListener('loadedmetadata', loaded); nodes.video.removeEventListener('error', failed); signal?.removeEventListener('abort', aborted); };
    const loaded = () => { cleanup(); resolve({ duration: nodes.video.duration, finiteDuration: Number.isFinite(nodes.video.duration), width: nodes.video.videoWidth, height: nodes.video.videoHeight, readyState: nodes.video.readyState }); };
    const failed = () => { cleanup(); reject(new Error('브라우저가 생성한 영상 Blob을 읽지 못했습니다.')); };
    const aborted = () => { cleanup(); reject(abortError()); };
    const timer = setTimeout(() => { cleanup(); reject(new Error('영상 metadata 대기 시간이 초과됐습니다.')); }, 8000);
    nodes.video.addEventListener('loadedmetadata', loaded, { once: true }); nodes.video.addEventListener('error', failed, { once: true }); signal?.addEventListener('abort', aborted, { once: true });
    nodes.video.src = url; nodes.video.load();
    if (signal?.aborted) aborted();
  });
}

nodes.run.addEventListener('click', async () => {
  if (controller) return;
  const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!Context) { status('AudioContext가 없는 브라우저입니다.'); return; }
  controller = new AbortController();
  const activeController = controller, signal = controller.signal, start = performance.now();
  const context = new Context();
  const resumed = context.resume(); // Called synchronously inside the trusted click.
  const graph = syntheticGraph(context);
  const microphone = new BrowserMicrophone({ mediaDevices: { getUserMedia: async () => graph.destination.stream }, secure: true });
  const analyzer = new BrowserAnalyzer({ onProgress: message => status(`분석: ${message}`) });
  const record = { status: 'recording', input: 'Synthetic OscillatorNode → GainNode → MediaStreamDestination', realMicrophoneUsed: false, speakerConnection: false };
  readyVideo = null; busy(true); show(record); status('합성음을 실제 녹음 모듈에 넣고 있어요…', 0);
  if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl = null; }
  const warmup = analyzer.warmup().catch(error => ({ error: error.message }));
  try {
    await resumed;
    const recordingStart = performance.now();
    const audio = await microphone.record({ signal, audioContext: context,
      onStarted: () => graph.perform(), onTick: seconds => status(`합성 입력 녹음 ${seconds.toFixed(1)} / 5.0초`, .35 * seconds / 5) });
    record.recording = { elapsedMs: elapsed(recordingStart), sampleCount: audio.samples.length, expectedSampleCount: 22050 * 5, sampleRate: audio.sampleRate,
      tracks: graph.destination.stream.getTracks().map(track => ({ kind: track.kind, readyState: track.readyState })),
      allTracksEnded: graph.destination.stream.getTracks().every(track => track.readyState === 'ended'),
      peak: audio.samples.reduce((peak, sample) => Math.max(peak, Math.abs(sample)), 0), rms: Math.sqrt(audio.samples.reduce((sum, sample) => sum + sample * sample, 0) / audio.samples.length) };
    record.status = 'analyzing'; show(record); status('실제 BrowserAnalyzer로 분석하고 있어요…', .38);
    const analysisStart = performance.now();
    const analysis = await analyzer.analyze(audio.samples, audio.sampleRate, { engine: 'auto', signal });
    record.warmup = await warmup;
    record.analysis = { elapsedMs: elapsed(analysisStart), engine: analysis.engine, duration: analysis.duration, frames: analysis.frames.length, impacts: analysis.events.length, warnings: analysis.warnings };
    record.status = 'encoding'; show(record);
    const image = await imageForCharacter();
    const renderFrame = createSceneRenderer({ character: 'bounce', image, analysis, synthetic: true });
    startPreview(renderFrame);
    const exportStart = performance.now();
    const video = await createMemeVideo({ renderFrame, samples: audio.samples, sampleRate: audio.sampleRate, duration: 5, width: STAGE.width, height: STAGE.height, fps: STAGE.fps, audioContext: context, signal,
      onProgress({ progress, message }) { status(message, .45 + .50 * progress); } });
    record.export = { elapsedMs: elapsed(exportStart), bytes: video.blob.size, mimeType: video.mimeType, extension: video.extension, method: video.method, width: video.width, height: video.height, duration: video.duration, callerContextPreserved: context.state !== 'closed' };
    blobUrl = URL.createObjectURL(video.blob);
    record.video = await waitForMetadata(blobUrl, signal);
    record.checks = { exactFiveSecondSamples: audio.samples.length === 110250 && audio.sampleRate === 22050,
      recordingTracksStopped: record.recording.allTracksEnded, actualBasicPitch: analysis.engine === 'basic_pitch',
      realPlayableBlob: video.blob.size > 0 && record.video.width === STAGE.width && record.video.height === STAGE.height,
      finiteFiveSecondVideo: record.video.finiteDuration && Math.abs(record.video.duration - 5) < .15,
      audioContextWasPreserved: record.export.callerContextPreserved };
    record.status = Object.values(record.checks).every(Boolean) ? 'passed' : 'review';
    record.totalElapsedMs = elapsed(start); readyVideo = video;
    show(record); status(`검사 ${record.status}: ${video.method}, ${record.video.duration.toFixed(3)}초, ${video.blob.size.toLocaleString()} bytes`, 1);
  } catch (error) {
    record.status = error.name === 'AbortError' ? 'cancelled' : 'failed'; record.error = { name: error.name, message: error.message }; record.totalElapsedMs = elapsed(start); show(record); status(`${record.status}: ${error.message}`);
  } finally {
    microphone.dispose(); analyzer.dispose(); graph.dispose();
    if (context.state !== 'closed') await context.close().catch(() => {});
    if (controller === activeController) controller = null;
    busy(false);
  }
});

nodes['late-abort'].addEventListener('click', async () => {
  if (controller) return;
  const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (!Context) { status('AudioContext가 없는 브라우저입니다.'); return; }
  controller = new AbortController(); const activeController = controller;
  const context = new Context(), resumed = context.resume();
  const destination = context.createMediaStreamDestination(), stream = destination.stream;
  let permissionTimer, abortTimer, requested = false, recordError;
  let deliverStream;
  const arrived = new Promise(resolve => { deliverStream = resolve; });
  const microphone = new BrowserMicrophone({ mediaDevices: { getUserMedia() { requested = true; permissionTimer = setTimeout(() => deliverStream(stream), 450); return arrived; } }, secure: true });
  const record = { status: 'testing-late-abort', realMicrophoneUsed: false }; show(record); busy(true); status('50ms에 취소하고 450ms에 stream을 전달합니다…', 0);
  try {
    await resumed;
    const pending = microphone.record({ signal: controller.signal, audioContext: context });
    abortTimer = setTimeout(() => activeController.abort(), 50);
    try { await pending; } catch (error) { recordError = error; }
    await arrived; await new Promise(resolve => setTimeout(resolve, 30));
    record.permissionRequested = requested;
    record.error = { name: recordError?.name || null, message: recordError?.message || null };
    record.lateStreamTracks = stream.getTracks().map(track => ({ kind: track.kind, readyState: track.readyState }));
    record.callerContextPreserved = context.state !== 'closed';
    record.checks = { rejectsAbortError: recordError?.name === 'AbortError', lateStreamTracksStopped: stream.getTracks().every(track => track.readyState === 'ended'), callerContextPreserved: record.callerContextPreserved };
    record.status = Object.values(record.checks).every(Boolean) ? 'passed' : 'failed'; show(record); status(`늦은 권한 응답 취소 검사: ${record.status}`, 1);
  } catch (error) { record.status = 'failed'; record.error = { name: error.name, message: error.message }; show(record); status(error.message); }
  finally { clearTimeout(permissionTimer); clearTimeout(abortTimer); microphone.dispose(); destination.disconnect(); stream.getTracks().forEach(track => track.stop()); if (context.state !== 'closed') await context.close().catch(() => {}); if (controller === activeController) controller = null; busy(false); }
});

nodes.codecs.addEventListener('click', async () => {
  if (controller) return;
  const record = { status: 'probing-codecs', realMicrophoneUsed: false, videoEncoder: typeof VideoEncoder !== 'undefined', audioEncoder: typeof AudioEncoder !== 'undefined', configs: [] };
  busy(true); show(record); status('브라우저가 실제 지원하는 인코더 구성을 확인합니다…');
  try {
    for (const [type, config] of [
      ['video', { codec: 'avc1.42001f', width: 720, height: 960, bitrate: 2600000, framerate: 24, hardwareAcceleration: 'prefer-software' }],
      ['video', { codec: 'vp8', width: 720, height: 960, bitrate: 2600000, framerate: 24, hardwareAcceleration: 'prefer-software' }],
      ['audio', { codec: 'mp4a.40.2', sampleRate: 48000, numberOfChannels: 1, bitrate: 96000 }],
      ['audio', { codec: 'opus', sampleRate: 48000, numberOfChannels: 1, bitrate: 96000 }],
    ]) {
      const Encoder = type === 'video' ? globalThis.VideoEncoder : globalThis.AudioEncoder;
      try { const support = Encoder ? await Encoder.isConfigSupported(config) : { supported: false }; record.configs.push({ type, requested: config, supported: support.supported, recognizedConfig: support.config }); }
      catch (error) { record.configs.push({ type, requested: config, supported: false, error: error.message }); }
    }
    record.mediaRecorder = ['video/mp4', 'video/webm;codecs=vp8,opus', 'video/webm;codecs=vp9,opus'].map(mimeType => ({ mimeType, supported: globalThis.MediaRecorder?.isTypeSupported(mimeType) || false }));
    record.status = 'complete'; show(record); status('인코더 지원 검사를 마쳤습니다.', 1);
  } finally { busy(false); }
});

nodes.cancel.addEventListener('click', () => controller?.abort());
nodes.download.addEventListener('click', () => { if (readyVideo) { const saved = downloadVideo(readyVideo, 'bop-synthetic-browser-qa'); if (currentRecord) { currentRecord.download = saved; show(currentRecord); } } });
addEventListener('pagehide', () => { controller?.abort(); stopPreview(); if (blobUrl) URL.revokeObjectURL(blobUrl); });
