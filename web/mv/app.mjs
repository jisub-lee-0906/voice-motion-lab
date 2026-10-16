import {MAX_SECONDS, SAMPLE_RATE, THEMES, JOB_ID, formatTime, validateDuration, downmix, validateSamples, artifactUrl, fileTitle, validateJob, completedElapsedSeconds, healthMessage} from './helpers.mjs';

const $ = id => document.getElementById(id);
const ui = Object.fromEntries(['health-badge','create-form','audio-file','dropzone','file-name','file-detail','demo-button','source-note','source-audio','mv-title','lyrics','create-button','empty-stage','result-video','job-panel','job-status','job-elapsed','job-progress','job-message','error-panel','error-message','health-retry','result-panel','result-title','result-meta','download-video','download-project','view-storyboard','engine-detail','result-warnings','storyboard-image','concept-strip','screen-label'].map(id => [id, $(id)]));
const lifetime = new AbortController();
const SESSION_KEY = 'bop-mv-current-job-v1';
const STATUS_LABEL = {queued:'제작 순서를 기다리고 있어요',transcribing:'노래 속 가사를 듣고 있어요',directing:'음악에 맞춰 장면을 연출해요',rendering:'장면을 이어 영상으로 만들어요',succeeded:'당신의 세계가 완성됐어요',failed:'제작을 완료하지 못했어요'};
const STEP_ORDER = ['transcribing','directing','rendering'];
let selected = null;
let sourceUrl = null;
let health = null;
let busy = false;
let activeId = null;
let startedAt = null;
let elapsedTimer = null;
let healthBusy = false;

function errorMessage(error) {
  if (error?.name === 'AbortError') return '연결이 종료됐어요. 페이지를 다시 열면 진행 중인 작업을 확인할 수 있습니다.';
  return String(error?.message || '요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.').slice(0, 1000);
}

function showError(message) {
  ui['error-message'].textContent = message;
  ui['error-panel'].hidden = false;
}

function hideError() { ui['error-panel'].hidden = true; }

async function request(url, options = {}, timeout = 15000) {
  const controller = new AbortController();
  const abort = () => controller.abort(lifetime.signal.reason);
  lifetime.signal.addEventListener('abort', abort, {once:true});
  if (lifetime.signal.aborted) abort();
  const timer = setTimeout(() => controller.abort(new Error('로컬 서버 응답이 지연되고 있어요. 서버 상태를 확인해 주세요.')), timeout);
  try {
    const response = await fetch(url, {...options, signal:controller.signal, credentials:'same-origin', cache:'no-store'});
    if (!response.ok) {
      let detail;
      try { detail = await response.json(); } catch { /* Never display server HTML. */ }
      const serverMessage = [detail?.error, detail?.message, detail?.detail].find(value => typeof value === 'string');
      const fallback = response.status === 429 ? '다른 영상이 제작 중이에요. 현재 작업이 끝난 뒤 다시 시도해 주세요.' : `로컬 서버 요청에 실패했어요 (${response.status}). 엔진 상태를 확인해 주세요.`;
      throw new Error(serverMessage?.slice(0, 1000) || fallback);
    }
    return response;
  } finally {
    clearTimeout(timer);
    lifetime.signal.removeEventListener('abort', abort);
  }
}

async function checkHealth({announce = false} = {}) {
  if (healthBusy) return;
  healthBusy = true;
  ui['health-retry'].disabled = true;
  try {
    health = await (await request('/api/mv/health', {}, 7000)).json();
    if (!health || typeof health.asr !== 'object' || typeof health.comfy !== 'object') throw new Error('엔진 상태 응답이 올바르지 않아요.');
    const message = healthMessage(health);
    ui['health-badge'].className = `engine-badge ${message ? 'unavailable' : 'ready'}`;
    ui['health-badge'].lastElementChild.textContent = message ? '엔진 준비 필요' : '로컬 엔진 준비 완료';
    if (announce) message ? showError(message) : hideError();
  } catch (error) {
    health = null;
    ui['health-badge'].className = 'engine-badge unavailable';
    ui['health-badge'].lastElementChild.textContent = '로컬 서버 연결 필요';
    if (announce) showError(healthMessage(null));
  } finally {
    healthBusy = false;
    ui['health-retry'].disabled = false;
  }
}

function setBusy(value) {
  busy = value;
  document.body.classList.toggle('busy', value);
  for (const element of ui['create-form'].querySelectorAll('input,textarea,button')) element.disabled = value;
  ui['create-button'].disabled = value || !selected;
  ui['create-button'].firstElementChild.textContent = value ? '당신의 세계를 만드는 중' : '뮤직비디오 만들기';
}

function selectFile(file, isDemo = false) {
  if (busy) return;
  if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  sourceUrl = null;
  selected = null;
  ui['source-audio'].hidden = true;
  ui['source-audio'].removeAttribute('src');
  ui['source-note'].hidden = true;
  ui['dropzone'].classList.remove('selected');
  ui['file-name'].textContent = '음악을 여기에 놓아 주세요';
  ui['file-detail'].textContent = '또는 눌러서 파일 선택 · 최대 60초';
  ui['create-button'].disabled = true;
  if (!file) return;
  const fileLimit = Math.min(Number(health?.limits?.max_bytes) || 24 * 1024 * 1024, 40 * 1024 * 1024);
  if (!file.size || file.size > fileLimit) {
    showError(`비어 있지 않은 ${Math.floor(fileLimit / (1024 * 1024))}MB 이하 음악을 선택해 주세요. 15–45초 구간을 권장합니다.`);
    return;
  }
  if (file.type && !file.type.startsWith('audio/') && !/\.(wav|mp3|m4a|ogg|flac|aac|opus|aiff?)$/i.test(file.name)) {
    showError('WAV, MP3, M4A 등 음악 파일을 선택해 주세요.');
    return;
  }
  selected = {file, isDemo};
  sourceUrl = URL.createObjectURL(file);
  ui['source-audio'].src = sourceUrl;
  ui['source-audio'].hidden = false;
  ui['file-name'].textContent = isDemo ? 'BOP! 오리지널 데모 음악' : file.name;
  ui['file-detail'].textContent = `${(file.size / (1024 * 1024)).toFixed(1)} MB · 음악을 확인한 뒤 만들기를 눌러 주세요`;
  ui['dropzone'].classList.add('selected');
  ui['source-note'].textContent = isDemo ? '직접 만든 합성 음악·TTS 데모입니다. 이 음원도 실제 자동 전사를 거쳐 제작되며, 가사는 미리 채워 넣지 않습니다.' : '선택한 음악은 아직 서버로 전송되지 않았어요.';
  ui['source-note'].className = 'source-note';
  ui['source-note'].hidden = false;
  ui['mv-title'].value = isDemo ? '작은 소리, 커다란 세계' : fileTitle(file.name);
  ui['create-button'].disabled = false;
  hideError();
}

async function decodeAudio(file) {
  const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
  const OfflineContextClass = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
  if (!AudioContextClass || !OfflineContextClass) throw new Error('이 브라우저는 음악 디코딩을 지원하지 않아요. 최신 Chrome, Edge 또는 Safari를 사용해 주세요.');
  const context = new AudioContextClass();
  try {
    let decoded;
    try { decoded = await context.decodeAudioData(await file.arrayBuffer()); }
    catch { throw new Error('이 음악 형식을 읽을 수 없어요. WAV나 MP3로 변환해서 다시 선택해 주세요.'); }
    validateDuration(decoded.duration, Math.min(MAX_SECONDS, Number(health?.limits?.max_seconds) || MAX_SECONDS));
    const mono = downmix(Array.from({length:decoded.numberOfChannels}, (_, channel) => decoded.getChannelData(channel)));
    const sampleCount = Math.min(MAX_SECONDS * SAMPLE_RATE, Math.round(decoded.duration * SAMPLE_RATE));
    const renderer = new OfflineContextClass(1, sampleCount, SAMPLE_RATE);
    const monoBuffer = renderer.createBuffer(1, mono.length, decoded.sampleRate);
    monoBuffer.copyToChannel(mono, 0);
    const source = renderer.createBufferSource();
    source.buffer = monoBuffer;
    source.connect(renderer.destination);
    source.start();
    const rendered = await renderer.startRendering();
    const samples = new Float32Array(rendered.getChannelData(0));
    // Resampling filters may ring slightly beyond full scale; retain finite bounded PCM.
    for (let index = 0; index < samples.length; index += 1) samples[index] = Math.max(-1, Math.min(1, samples[index]));
    validateSamples(samples);
    return samples;
  } finally { await context.close().catch(() => {}); }
}

function setJobDisplay(job) {
  ui['job-panel'].hidden = false;
  ui['job-status'].textContent = STATUS_LABEL[job.status] || '작업 상태 확인 중';
  ui['job-message'].textContent = String(job.message || '').slice(0, 1000);
  if (['succeeded', 'failed'].includes(job.status)) {
    finishClock();
    const elapsed = completedElapsedSeconds(job);
    ui['job-elapsed'].textContent = elapsed === null ? '—' : formatTime(elapsed);
  } else if (!elapsedTimer) {
    startClock(Number.isFinite(job.created_at) ? job.created_at * 1000 : startedAt || Date.now());
  }
  const progress = typeof job.progress === 'number' && Number.isFinite(job.progress) ? job.progress : null;
  if (progress === null) ui['job-progress'].removeAttribute('value');
  else ui['job-progress'].value = Math.max(0, Math.min(1, progress));
  const current = job.status === 'succeeded' ? STEP_ORDER.length : STEP_ORDER.indexOf(job.status);
  for (const step of document.querySelectorAll('[data-step]')) {
    const index = STEP_ORDER.indexOf(step.dataset.step);
    step.classList.toggle('active', current === index);
    step.classList.toggle('done', current > index);
  }
}

function startClock(timestamp = Date.now()) {
  startedAt = timestamp;
  clearInterval(elapsedTimer);
  const tick = () => { ui['job-elapsed'].textContent = formatTime((Date.now() - startedAt) / 1000); };
  tick();
  elapsedTimer = setInterval(tick, 1000);
}

function finishClock() { clearInterval(elapsedTimer); elapsedTimer = null; }

function persistJob() {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify({id:activeId, startedAt})); } catch { /* Private browsing may block storage. */ }
}

function clearSavedJob() { try { sessionStorage.removeItem(SESSION_KEY); } catch { /* Optional. */ } }

function showResult(job) {
  const result = job.result;
  if (!result) throw new Error('제작은 끝났지만 결과 파일 정보가 없어요. 로컬 서버 로그를 확인해 주세요.');
  const origin = location.origin;
  const video = artifactUrl(result.video_url, job.id, 'video.mp4', origin);
  const preview = result.preview_url ? artifactUrl(result.preview_url, job.id, 'preview.webm', origin) : video;
  const project = artifactUrl(result.project_url, job.id, 'project.json', origin);
  const storyboard = artifactUrl(result.storyboard_url, job.id, 'storyboard.jpg', origin);
  ui['result-video'].src = preview;
  ui['result-video'].hidden = false;
  ui['empty-stage'].hidden = true;
  ui['result-video'].load();
  ui['result-title'].textContent = String(job.title || '나의 뮤직비디오');
  const dimensions = Number.isFinite(result.width) && Number.isFinite(result.height) ? `${result.width} × ${result.height}` : 'MP4';
  ui['result-meta'].textContent = `${formatTime(result.duration)} / ${dimensions} / ${Number.isFinite(result.shots) ? `${result.shots} CUTS` : '2D MV'}`;
  ui['download-video'].href = video;
  ui['download-project'].href = project;
  ui['view-storyboard'].href = storyboard;
  ui['storyboard-image'].src = storyboard;
  const engines = result.engines;
  const engineText = typeof engines === 'object' && engines ? Object.entries(engines).map(([key,value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`).join(' · ') : String(engines || '엔진 정보 없음');
  ui['engine-detail'].textContent = `사용한 엔진 — ${engineText.slice(0, 1600)}`;
  ui['result-warnings'].replaceChildren();
  for (const warning of (Array.isArray(result.warnings) ? result.warnings : []).slice(0, 25)) {
    const item = document.createElement('li');
    item.textContent = String(warning).slice(0, 600);
    ui['result-warnings'].append(item);
  }
  ui['result-panel'].hidden = false;
  ui['concept-strip'].hidden = true;
  ui['screen-label'].textContent = '02 — YOUR MUSIC VIDEO';
}

function wait(ms) {
  return new Promise((resolve,reject) => {
    const timer = setTimeout(() => { lifetime.signal.removeEventListener('abort', stop); resolve(); }, ms);
    function stop() { clearTimeout(timer); reject(new DOMException('페이지 연결 종료', 'AbortError')); }
    if (lifetime.signal.aborted) return stop();
    lifetime.signal.addEventListener('abort', stop, {once:true});
  });
}

async function pollJob(id) {
  let networkErrors = 0;
  while (!lifetime.signal.aborted) {
    let job;
    try {
      job = validateJob(await (await request(`/api/mv/jobs/${encodeURIComponent(id)}`)).json(), id);
      networkErrors = 0;
    } catch (error) {
      if (lifetime.signal.aborted) throw error;
      networkErrors += 1;
      if (networkErrors > 3) throw new Error(`작업 상태를 다시 연결하지 못했어요. 서버가 실행 중인지 확인한 뒤 이 페이지를 새로고침해 주세요. ${errorMessage(error)}`);
      ui['job-message'].textContent = '서버의 작업 상태를 다시 확인하고 있어요. 작업 취소 요청은 보내지 않았습니다.';
      await wait(2000);
      continue;
    }
    setJobDisplay(job);
    if (job.status === 'failed') {
      clearSavedJob();
      throw new Error(String(job.error || job.message || '영상 제작 중 오류가 발생했어요. 다른 짧은 곡으로 다시 시도해 주세요.'));
    }
    if (job.status === 'succeeded') {
      showResult(job);
      persistJob();
      return;
    }
    await wait(1250);
  }
}

async function createVideo(event) {
  event.preventDefault();
  if (busy || !selected) return;
  const source = selected;
  const title = ui['mv-title'].value.trim() || fileTitle(source.file.name);
  const theme = new FormData(ui['create-form']).get('theme') || 'auto';
  const lyrics = ui['lyrics'].value.trim();
  if (!THEMES.has(theme)) return showError('장면 테마를 다시 선택해 주세요.');
  setBusy(true);
  hideError();
  ui['source-audio'].pause();
  ui['result-video'].pause();
  try {
    if (!health || healthMessage(health)) await checkHealth();
    const engineError = healthMessage(health);
    if (engineError) throw new Error(engineError);
    ui['source-note'].textContent = '브라우저에서 음악 길이와 형식을 확인하고 있어요. 아직 전송 전입니다.';
    ui['source-note'].hidden = false;
    const samples = await decodeAudio(source.file);
    if (lifetime.signal.aborted) return;
    ui['source-note'].textContent = `${formatTime(samples.length / SAMPLE_RATE)} · 16kHz 모노 · 이 PC의 로컬 서버로 전달 중`;
    const payload = {audio:{samples:Array.from(samples),sample_rate:SAMPLE_RATE},title,theme,seed:7};
    if (lyrics) payload.lyrics = lyrics;
    const body = JSON.stringify(payload);
    const maxBytes = Number(health?.limits?.max_bytes) || 24 * 1024 * 1024;
    if (new TextEncoder().encode(body).byteLength > maxBytes) throw new Error('음악 전송 크기가 제한을 넘었어요. 더 짧은 구간으로 다시 시도해 주세요.');
    const accepted = validateJob(await (await request('/api/mv/jobs', {method:'POST',headers:{'Content-Type':'application/json'},body}, 60000)).json());
    activeId = accepted.id;
    startClock();
    persistJob();
    setJobDisplay(accepted);
    ui['source-note'].textContent = '이 PC의 로컬 제작 서버에서 처리하고 있어요. 외부 서비스로 업로드하지 않습니다.';
    await pollJob(activeId);
  } catch (error) {
    if (!lifetime.signal.aborted) showError(errorMessage(error));
  } finally {
    finishClock();
    setBusy(false);
    activeId = null;
  }
}

ui['create-form'].addEventListener('submit', createVideo);
ui['audio-file'].addEventListener('change', event => selectFile(event.target.files?.[0]));
ui['dropzone'].addEventListener('dragover', event => { event.preventDefault(); if (!busy) ui['dropzone'].classList.add('dragging'); });
ui['dropzone'].addEventListener('dragleave', () => ui['dropzone'].classList.remove('dragging'));
ui['dropzone'].addEventListener('drop', event => { event.preventDefault(); ui['dropzone'].classList.remove('dragging'); if (!busy) selectFile(event.dataTransfer?.files?.[0]); });
ui['health-retry'].addEventListener('click', () => checkHealth({announce:true}));
ui['demo-button'].addEventListener('click', async () => {
  if (busy) return;
  ui['demo-button'].disabled = true;
  ui['demo-button'].textContent = '데모 불러오는 중…';
  try {
    const response = await request('/api/mv/demo.wav', {}, 30000);
    const blob = await response.blob();
    selectFile(new File([blob], 'bop-original-demo.wav', {type:'audio/wav'}), true);
  } catch (error) { if (!lifetime.signal.aborted) showError(errorMessage(error)); }
  finally {
    ui['demo-button'].disabled = busy;
    ui['demo-button'].textContent = '오리지널 데모 들어보기 ↗';
  }
});

window.addEventListener('pagehide', () => {
  lifetime.abort();
  finishClock();
  if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  ui['source-audio'].pause();
  ui['result-video'].pause();
}, {once:true});
window.addEventListener('pageshow', event => {
  // A bfcache restore follows pagehide; reconnect with a fresh lifecycle signal.
  if (event.persisted) location.reload();
});

async function initialize() {
  await checkHealth();
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch { /* Optional recovery. */ }
  if (!saved || !JOB_ID.test(saved.id || '') || !Number.isFinite(saved.startedAt)) return;
  activeId = saved.id;
  setBusy(true);
  startedAt = saved.startedAt;
  ui['job-elapsed'].textContent = '—';
  ui['job-panel'].hidden = false;
  ui['job-status'].textContent = '진행하던 작업을 확인하고 있어요';
  ui['job-progress'].removeAttribute('value');
  try { await pollJob(activeId); }
  catch (error) { if (!lifetime.signal.aborted) showError(errorMessage(error)); }
  finally { finishClock(); setBusy(false); activeId = null; }
}

initialize();
