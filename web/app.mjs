import { STAGE, DEFAULT_SETTINGS, clamp, normalizeSettings, motionAt, sampleKeyframes, sanitizeSvg, assetBox, validateAnalysis, validateProject, downsampleMono, animatedSvg } from './core.mjs';

const $ = id => document.getElementById(id);
const defaultSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 220"><path fill="#d2ff57" d="M110 14c9 55 41 87 96 96-55 9-87 41-96 96-9-55-41-87-96-96 55-9 87-41 96-96Z"/><path fill="#f4f3eb" d="m113 57 10 39 39 14-39 10-10 43-13-43-38-10 38-14Z"/></svg>';
const state = { asset: { name: 'Studio spark.svg', svg: defaultSvg }, image: null, settings: { ...DEFAULT_SETTINGS }, audio: null, analysis: null, time: 0, playing: false, busy: false, recording: false, exporting: false, health: null, source: null, playbackContext: null, recorder: null, stream: null, abort: null, synthetic: false };
const urls = new Set();
let recordTimer = null, recordInterval = null, frameRequest = null;
let playbackEpoch = 0, disposed = false;
const timedContexts = new Set();

function status(message, type = '') { $('status-text').textContent = message; $('status').className = `status ${type}`.trim(); }
function controls() {
  const locked = state.busy || state.exporting || state.recording;
  for (const id of ['asset-trigger', 'audio-trigger', 'demo', 'project-trigger', 'engine', 'use-comfy', 'intensity', 'smoothing', 'background', 'transparent']) $(id).disabled = locked;
  $('use-comfy').disabled = locked || !state.health?.comfy?.configured;
  $('record').disabled = state.busy || state.exporting;
  $('analyze').disabled = locked || !state.audio;
  $('play').disabled = locked || !state.analysis;
  $('timeline').disabled = locked || !state.audio;
  for (const id of ['export-svg', 'export-json', 'export-webm']) $(id).disabled = locked || !state.analysis;
  for (const button of document.querySelectorAll('.preset')) button.disabled = locked;
}
function setBusy(value, message) { state.busy = value; controls(); if (message) status(message, value ? 'busy' : ''); }
function clock(t) { return Math.max(0, t).toFixed(2).padStart(5, '0'); }
function duration() { return state.analysis?.duration ?? (state.audio ? state.audio.samples.length / state.audio.sample_rate : 5); }
function render() {
  drawScene($('stage').getContext('2d'), state.time, $('show-guide').checked);
  drawWaveform();
  $('time-current').textContent = clock(state.time); $('time-total').textContent = clock(duration());
  $('timeline').max = duration(); $('timeline').value = state.time;
  $('ruler-mid').textContent = `${(duration() / 2).toFixed(1)}s`; $('ruler-end').textContent = `${duration().toFixed(1)}s`;
  $('play').textContent = state.playing ? 'Ⅱ' : '▶'; $('play').setAttribute('aria-label', state.playing ? '미리보기 일시정지' : '미리보기 재생');
  $('stage-hint').classList.toggle('hidden', Boolean(state.audio));
  document.querySelector('.stage-wrap').classList.toggle('transparent', state.settings.transparent);
}

function drawScene(ctx, time, guides = false) {
  const s = state.settings;
  ctx.save(); ctx.clearRect(0, 0, STAGE.width, STAGE.height);
  if (!s.transparent) { ctx.fillStyle = s.background; ctx.fillRect(0, 0, STAGE.width, STAGE.height); }
  if (guides) {
    ctx.strokeStyle = '#89978216'; ctx.lineWidth = 1;
    for (let x = 60; x < 960; x += 60) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 540); ctx.stroke(); }
    for (let y = 30; y < 540; y += 60) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(960, y); ctx.stroke(); }
    ctx.strokeStyle = '#c5d59e38'; ctx.setLineDash([4, 7]); ctx.beginPath();
    const guide = { duration: 1, frames: [{ time: 0, progress: 0, pitch: .5, pitch_confidence: 0 }, { time: 1, progress: 1, pitch: .5, pitch_confidence: 0 }], events: [] };
    for (let n = 0; n <= 120; n++) { const p = motionAt(n / 120, guide, s); n ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); }
    ctx.stroke(); ctx.setLineDash([]);
  }
  const m = motionAt(time, state.analysis || { duration: 5, frames: [], events: [] }, s), box = assetBox(state.asset.svg);
  ctx.translate(m.x, m.y); ctx.rotate(m.rotation * Math.PI / 180); ctx.scale(m.scaleX, m.scaleY); ctx.globalAlpha = m.opacity;
  if (state.image) ctx.drawImage(state.image, -box.width / 2, -box.height / 2, box.width, box.height);
  ctx.restore();
}

function drawWaveform() {
  const canvas = $('waveform'), ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h); const center = h / 2;
  ctx.strokeStyle = '#c5cdb6'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(0, center); ctx.lineTo(w, center); ctx.stroke();
  if (state.audio) {
    const samples = state.audio.samples, bars = 300, step = samples.length / bars, playX = state.time / duration() * w;
    for (let n = 0; n < bars; n++) {
      let peak = 0; const start = Math.floor(n * step), end = Math.min(samples.length, Math.ceil((n + 1) * step));
      for (let k = start; k < end; k++) peak = Math.max(peak, Math.abs(samples[k]));
      const height = Math.max(2, Math.sqrt(peak) * (h - 22)), x = n / bars * w;
      ctx.fillStyle = x <= playX ? '#7a9644' : '#bec9a8'; ctx.fillRect(x + 1, center - height / 2, Math.max(1, w / bars - 2), height);
    }
    for (const event of state.analysis?.events || []) { const x = event.time / duration() * w; ctx.fillStyle = '#b99c58'; ctx.beginPath(); ctx.moveTo(x, 3); ctx.lineTo(x - 4, 11); ctx.lineTo(x + 4, 11); ctx.closePath(); ctx.fill(); }
    ctx.fillStyle = '#283e20'; ctx.fillRect(Math.min(w - 2, playX), 0, 2, h);
  }
}

async function setAsset(svg, name) {
  const clean = sanitizeSvg(svg), box = assetBox(clean);
  const renderSvg = clean.replace('width="100%"', `width="${Math.ceil(box.width * 4)}"`).replace('height="100%"', `height="${Math.ceil(box.height * 4)}"`);
  const url = URL.createObjectURL(new Blob([renderSvg], { type: 'image/svg+xml' })); urls.add(url);
  const image = new Image();
  try { await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(new Error('이 SVG를 그릴 수 없습니다.')); image.src = url; }); }
  finally { URL.revokeObjectURL(url); urls.delete(url); }
  state.asset = { name: name || 'Untitled.svg', svg: clean }; state.image = image;
  $('asset-name').textContent = state.asset.name; $('asset-detail').textContent = '검증된 벡터 · 원본 형태 유지';
  const thumb = document.createElement('img'); thumb.alt = ''; thumb.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(renderSvg)}`; $('asset-thumb').replaceChildren(thumb);
  render();
}

function stopPlayback() {
  playbackEpoch++;
  state.playing = false;
  if (state.source) { try { state.source.stop(); } catch {} state.source.disconnect(); state.source = null; }
  if (state.playbackContext) { state.playbackContext.close().catch(() => {}); state.playbackContext = null; }
  if (frameRequest) cancelAnimationFrame(frameRequest); frameRequest = null;
  render();
}

async function play() {
  if (!state.analysis || state.busy || state.exporting) return;
  if (state.playing) { stopPlayback(); return; }
  if (state.time >= duration() - .01) state.time = 0;
  const epoch = ++playbackEpoch; state.playing = true;
  let start = performance.now() - state.time * 1000;
  try {
    if ($('play-audio').checked && state.audio) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx(); state.playbackContext = ctx; await ctx.resume();
      if (disposed || epoch !== playbackEpoch || !state.playing) { await ctx.close().catch(() => {}); return; }
      const buffer = ctx.createBuffer(1, state.audio.samples.length, state.audio.sample_rate); buffer.copyToChannel(state.audio.samples, 0);
      const source = ctx.createBufferSource(); source.buffer = buffer; source.connect(ctx.destination); source.start(0, state.time); state.source = source;
    }
    state.playing = true; start = performance.now() - state.time * 1000;
    const tick = now => { if (!state.playing) return; state.time = Math.min(duration(), (now - start) / 1000); render(); if (state.time >= duration()) stopPlayback(); else frameRequest = requestAnimationFrame(tick); };
    frameRequest = requestAnimationFrame(tick); render();
  } catch (error) { stopPlayback(); status(`오디오 재생을 시작하지 못했습니다. ${error.message}`, 'error'); }
}

function setAudio(samples, sampleRate, name, synthetic = false) {
  stopPlayback(); state.audio = { samples, sample_rate: sampleRate }; state.analysis = null; state.time = 0; state.synthetic = synthetic;
  $('take-label').textContent = synthetic ? 'SYNTHETIC DEMO · 실제 녹음 아님' : name;
  $('take-label').classList.toggle('synthetic', synthetic);
  $('audio-note').textContent = synthetic ? '수학적으로 만든 5초 합성 신호입니다. 실제 녹음이나 모델의 예측 결과가 아닙니다.' : `${name} · ${(samples.length / sampleRate).toFixed(2)}초 · ${sampleRate.toLocaleString()} Hz · 로컬 처리`;
  $('stage-engine').textContent = '분석 대기'; controls(); render();
}

async function decodeAudio(blob, name) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) throw new Error('이 브라우저는 오디오 디코딩을 지원하지 않습니다.');
  const ctx = new Ctx(); timedContexts.add(ctx);
  try {
    const buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
    const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index));
    const samples = downsampleMono(channels, buffer.sampleRate, 22050, 15);
    if (!samples.length) throw new Error('비어 있는 오디오입니다.');
    setAudio(samples, 22050, name);
    return buffer.duration > 15;
  } finally { await ctx.close(); timedContexts.delete(ctx); }
}

async function analyze() {
  if (!state.audio || state.busy || state.exporting || state.recording) return;
  stopPlayback(); setBusy(true, '발성의 시간·음높이·타격을 분석하고 있습니다.');
  const abort = new AbortController(); state.abort = abort;
  const timer = setTimeout(() => abort.abort(), 150000);
  try {
    const viaComfy = $('use-comfy').checked;
    const response = await fetch(viaComfy ? '/api/analyze-comfy' : '/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: abort.signal, body: JSON.stringify({ samples: Array.from(state.audio.samples, value => Number(value.toFixed(5))), sample_rate: state.audio.sample_rate, engine: $('engine').value, sensitivity: 1, smoothing: state.settings.smoothing }) });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.error === 'string' ? result.error : result.error?.message || result.detail || `분석 요청 실패 (${response.status})`);
    validateAnalysis(result); state.analysis = result; state.time = 0;
    const engine = result.engine === 'basic_pitch' ? 'Basic Pitch · ONNX / CPU' : '신호 분석 · AI 모델 아님';
    $('stage-engine').textContent = engine;
    const warnings = (result.warnings || []).map(String).join(' ');
    status(`${viaComfy ? 'Comfy 실행 · ' : ''}${engine} · 타격 ${result.events.length}개 · 모션 준비 완료.${warnings ? ` ${warnings}` : ''}`);
  } catch (error) { status(error.name === 'AbortError' ? '분석 제한 시간을 넘었습니다. 짧은 오디오로 다시 시도해 주세요.' : error.message, 'error'); }
  finally { clearTimeout(timer); state.abort = null; state.busy = false; controls(); render(); }
}

async function record() {
  if (state.recording) { state.recorder?.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { status('녹음은 localhost의 Chrome·Edge 등 지원 브라우저에서 사용할 수 있습니다. 오디오 파일도 가져올 수 있습니다.', 'error'); return; }
  stopPlayback(); setBusy(true, '마이크 권한을 허용하면 5초 녹음을 시작합니다.');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    if (disposed) { stream.getTracks().forEach(track => track.stop()); return; }
    state.stream = stream;
    const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined), chunks = [];
    state.recorder = recorder; state.recording = true; state.busy = false; controls();
    $('record').classList.add('recording'); $('record-label').textContent = '녹음 중 · 눌러서 끝내기';
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    recorder.onerror = event => { status(event.error?.message || '마이크 녹음 오류가 발생했습니다.', 'error'); };
    recorder.onstop = async () => {
      clearTimeout(recordTimer); clearInterval(recordInterval); stream.getTracks().forEach(track => track.stop());
      state.recording = false; state.recorder = null; state.stream = null; $('record').classList.remove('recording'); $('record-label').textContent = '5초 녹음하고 분석'; $('record-clock').textContent = '↗';
      setBusy(true, '녹음한 오디오를 로컬에서 준비하고 있습니다.');
      try { if (!chunks.length) throw new Error('녹음된 소리가 없습니다.'); await decodeAudio(new Blob(chunks, { type: recorder.mimeType }), '마이크 녹음'); state.busy = false; await analyze(); }
      catch (error) { status(error.message, 'error'); }
      finally { state.busy = false; controls(); render(); }
    };
    recorder.start(100); const started = performance.now();
    status('녹음 중입니다. 허밍하고, 쉬고, 짧게 타격음을 내보세요.', 'busy');
    recordInterval = setInterval(() => { $('record-clock').textContent = `${Math.max(0, 5 - (performance.now() - started) / 1000).toFixed(1)}s`; }, 100);
    recordTimer = setTimeout(() => { if (recorder.state !== 'inactive') recorder.stop(); }, 5000);
  } catch (error) {
    state.stream?.getTracks().forEach(track => track.stop()); state.stream = null; state.busy = false; state.recording = false; controls();
    status(error.name === 'NotAllowedError' ? '마이크 권한이 허용되지 않았습니다. 오디오 가져오기 또는 합성 데모를 사용할 수 있습니다.' : `녹음을 시작하지 못했습니다. ${error.message}`, 'error');
  }
}

function syntheticTake() {
  const rate = 22050, samples = new Float32Array(rate * 5);
  for (let n = 0; n < samples.length; n++) {
    const t = n / rate; let value = 0;
    for (const [start, end, base] of [[.25, 1.35, 145], [1.9, 2.95, 195], [3.55, 4.8, 125]]) if (t >= start && t < end) {
      const local = t - start, u = local / (end - start), envelope = Math.min(1, local / .035, (end - t) / .07);
      value += .36 * envelope * (Math.sin(2 * Math.PI * (base * local + 28 * local * local)) + .22 * Math.sin(4 * Math.PI * base * local));
      value *= .85 + .15 * Math.sin(u * Math.PI);
    }
    for (const hit of [1.52, 3.18, 4.92]) { const dt = t - hit; if (dt >= 0 && dt < .045) value += .7 * Math.exp(-dt * 120) * Math.sin(2 * Math.PI * 900 * dt); }
    samples[n] = clamp(value, -1, 1);
  }
  return samples;
}

function download(content, type, name) {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob); urls.add(url); const link = document.createElement('a'); link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
  setTimeout(() => { URL.revokeObjectURL(url); urls.delete(url); }, 1500);
}
function exportProject() {
  if (!state.analysis) return;
  const project = { format: 'voice-motion-project', version: 1, asset: state.asset, audio: { sample_rate: state.audio.sample_rate, samples: Array.from(state.audio.samples) }, analysis: state.analysis, settings: state.settings, keyframes: sampleKeyframes(state.analysis, state.settings), provenance: { audio: state.synthetic ? 'synthetic-demo' : 'user-provided' } };
  download(JSON.stringify(project), 'application/json', `voice-motion-${state.settings.preset}.json`); status('프로젝트 JSON을 저장했습니다. 이 파일에는 원음 샘플과 모션 키프레임이 포함됩니다.');
}
function syncSettingsUi() {
  for (const button of document.querySelectorAll('.preset')) { const active = button.dataset.preset === state.settings.preset; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); }
  for (const id of ['intensity', 'smoothing', 'background']) $(id).value = state.settings[id];
  $('transparent').checked = state.settings.transparent; $('intensity-value').value = `${Math.round(state.settings.intensity * 100)}%`; $('smoothing-value').value = `${Math.round(state.settings.smoothing * 100)}%`;
}

async function exportWebm() {
  if (!state.analysis || !window.MediaRecorder || !$('stage').captureStream) { status('이 브라우저는 WebM 내보내기를 지원하지 않습니다. SVG와 JSON을 이용해 주세요.', 'error'); return; }
  stopPlayback(); state.exporting = true; state.time = 0; controls(); status('실제 캔버스 프레임을 WebM으로 녹화합니다. 탭을 열어 두세요.', 'busy');
  const canvas = document.createElement('canvas'); canvas.width = 960; canvas.height = 540; const ctx = canvas.getContext('2d');
  drawScene(ctx, 0, false); const stream = canvas.captureStream(30); let audioContext = null, audioSource = null, raf = null;
  try {
    if ($('export-audio').checked && state.audio) {
      const Ctx = window.AudioContext || window.webkitAudioContext; audioContext = new Ctx(); timedContexts.add(audioContext); await audioContext.resume();
      const dest = audioContext.createMediaStreamDestination(), buffer = audioContext.createBuffer(1, state.audio.samples.length, state.audio.sample_rate); buffer.copyToChannel(state.audio.samples, 0);
      audioSource = audioContext.createBufferSource(); audioSource.buffer = buffer; audioSource.connect(dest); for (const track of dest.stream.getAudioTracks()) stream.addTrack(track);
    }
    const mimeType = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find(type => MediaRecorder.isTypeSupported(type));
    if (!mimeType) throw new Error('지원하는 WebM 인코더가 없습니다.');
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 5_000_000 }), chunks = [];
    const finished = new Promise((resolve, reject) => { recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); }; recorder.onerror = e => reject(e.error || new Error('영상 인코딩 오류')); recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType })); });
    recorder.start(100); audioSource?.start(); const started = performance.now();
    await new Promise(resolve => {
      const tick = now => { const t = Math.min(duration(), (now - started) / 1000); state.time = t; drawScene(ctx, t, false); render(); if (t >= duration()) { setTimeout(() => { recorder.stop(); resolve(); }, 50); } else raf = requestAnimationFrame(tick); }; raf = requestAnimationFrame(tick);
    });
    const result = await finished; download(result, mimeType, `voice-motion-${state.settings.preset}.webm`);
    status(`WebM을 저장했습니다.${state.settings.transparent ? ' 투명도 재생은 플레이어의 지원 여부에 따라 달라집니다.' : ''} 원음 ${$('export-audio').checked ? '포함' : '제외'} · 안내선 제외.`);
  } catch (error) { status(`영상 내보내기 실패: ${error.message}`, 'error'); }
  finally {
    if (raf) cancelAnimationFrame(raf); stream.getTracks().forEach(track => track.stop()); if (audioSource) { try { audioSource.stop(); } catch {} audioSource.disconnect(); }
    if (audioContext) { await audioContext.close(); timedContexts.delete(audioContext); } state.exporting = false; controls(); render();
  }
}

async function health() {
  try {
    const response = await fetch('/api/health'); if (!response.ok) throw new Error('로컬 서버를 확인해 주세요.'); state.health = await response.json();
    const model = Boolean(state.health.engines?.basic_pitch);
    $('health-label').textContent = model ? 'BASIC PITCH / CPU READY' : 'SIGNAL / CPU READY';
    const comfy = Boolean(state.health.comfy?.configured); $('use-comfy').checked = comfy;
    $('comfy-label').textContent = comfy ? state.health.comfy.url || '연결됨' : '미설정';
    status(model ? '공개 모델 Basic Pitch가 준비됐습니다. 녹음하거나 합성 데모로 시작하세요.' : '신호 분석이 준비됐습니다. 모델 분석을 사용할 수 없으면 실제 신호 분석으로 명확히 표시합니다.');
  } catch (error) { $('health-label').textContent = 'SERVER OFFLINE'; status(error.message || '로컬 서버에 연결하지 못했습니다.', 'error'); }
  controls();
}

$('play').addEventListener('click', play);
$('play-audio').addEventListener('change', () => { if (state.playing) stopPlayback(); });
$('timeline').addEventListener('input', () => { const time = Number($('timeline').value); stopPlayback(); state.time = time; render(); });
$('show-guide').addEventListener('change', render);
$('record').addEventListener('click', record);
$('analyze').addEventListener('click', analyze);
$('demo').addEventListener('click', async () => { setAudio(syntheticTake(), 22050, '합성 데모', true); await analyze(); });
$('asset-trigger').addEventListener('click', () => $('asset-file').click());
$('audio-trigger').addEventListener('click', () => $('audio-file').click());
$('project-trigger').addEventListener('click', () => $('project-file').click());
$('asset-file').addEventListener('change', async e => {
  const file = e.target.files[0]; e.target.value = ''; if (!file) return; stopPlayback(); setBusy(true, 'SVG를 안전하게 확인하고 있습니다.');
  try { if (file.size > 1_000_000) throw new Error('SVG는 최대 1MB까지 지원합니다.'); await setAsset(await file.text(), file.name); status('원본 SVG를 적용했습니다. 외부 콘텐츠와 실행 요소는 허용하지 않습니다.'); }
  catch (error) { status(error.message, 'error'); } finally { state.busy = false; controls(); }
});
$('audio-file').addEventListener('change', async e => {
  const file = e.target.files[0]; e.target.value = ''; if (!file) return; stopPlayback(); setBusy(true, '오디오를 모노 신호로 준비하고 있습니다.');
  try { if (file.size > 25_000_000) throw new Error('오디오 파일은 최대 25MB까지 지원합니다.'); const trimmed = await decodeAudio(file, file.name); state.busy = false; await analyze(); if (trimmed && state.analysis) status(`${$('status-text').textContent} 긴 오디오는 앞 15초만 사용했습니다.`); }
  catch (error) { status(`오디오를 열지 못했습니다. ${error.message}`, 'error'); } finally { state.busy = false; controls(); render(); }
});
$('project-file').addEventListener('change', async e => {
  const file = e.target.files[0]; e.target.value = ''; if (!file) return; stopPlayback(); setBusy(true, '프로젝트와 원본 SVG를 확인하고 있습니다.');
  try { if (file.size > 25_000_000) throw new Error('프로젝트는 최대 25MB까지 지원합니다.'); const project = validateProject(JSON.parse(await file.text())); await setAsset(project.asset.svg, project.asset.name); setAudio(new Float32Array(project.audio.samples), project.audio.sample_rate, '불러온 프로젝트', project.provenance?.audio === 'synthetic-demo'); state.analysis = project.analysis; state.settings = normalizeSettings(project.settings); syncSettingsUi(); $('stage-engine').textContent = project.analysis.engine === 'basic_pitch' ? 'Basic Pitch · ONNX / CPU' : '신호 분석 · AI 모델 아님'; status('프로젝트를 다시 열었습니다. 원본·분석·설정을 계속 편집할 수 있습니다.'); }
  catch (error) { status(`프로젝트를 열지 못했습니다. ${error.message}`, 'error'); } finally { state.busy = false; controls(); render(); }
});
for (const button of document.querySelectorAll('.preset')) button.addEventListener('click', () => { state.settings.preset = button.dataset.preset; syncSettingsUi(); render(); });
for (const id of ['intensity', 'smoothing', 'background', 'transparent']) $(id).addEventListener('input', () => { state.settings[id] = id === 'transparent' ? $(id).checked : id === 'background' ? $(id).value : Number($(id).value); syncSettingsUi(); render(); });
$('export-json').addEventListener('click', exportProject);
$('export-svg').addEventListener('click', () => { try { download(animatedSvg(state.asset.svg, state.analysis, state.settings, state.asset.name), 'image/svg+xml', `voice-motion-${state.settings.preset}.svg`); status('60fps 샘플 키프레임을 담은 애니메이션 SVG를 저장했습니다. 원음은 포함되지 않습니다.'); } catch (error) { status(error.message, 'error'); } });
$('export-webm').addEventListener('click', exportWebm);
window.addEventListener('pagehide', () => {
  disposed = true; playbackEpoch++;
  state.abort?.abort(); clearTimeout(recordTimer); clearInterval(recordInterval); if (frameRequest) cancelAnimationFrame(frameRequest);
  if (state.recorder?.state === 'recording') state.recorder.stop(); state.stream?.getTracks().forEach(track => track.stop());
  try { state.source?.stop(); } catch {} state.playbackContext?.close().catch(() => {});
  for (const ctx of timedContexts) ctx.close().catch(() => {}); for (const url of urls) URL.revokeObjectURL(url); urls.clear();
});

await setAsset(defaultSvg, 'Studio spark.svg'); syncSettingsUi(); render(); await health();
