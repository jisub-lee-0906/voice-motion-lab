import { BrowserAnalyzer } from './analysis-client.mjs';
import { createMemeVideo, shareVideo, downloadVideo } from './export-video.mjs';
import { BrowserMicrophone, fiveSecondMono, microphoneError } from './mic.mjs';
import { STAGE, CHARACTERS, createSceneRenderer } from './scene.mjs';
import { registerOffline } from './offline.mjs';

const $ = id => document.getElementById(id);
const el = Object.fromEntries(['stage', 'stage-shell', 'stage-tag', 'stage-bottom', 'record', 'record-label', 'record-hint', 'cancel-record', 'countdown', 'listening-overlay', 'listening-label', 'status', 'status-box', 'status-mark', 'demo', 'preview-caption', 'result-video', 'sound-toggle', 'result-actions', 'save', 'share', 'remix-link', 'format-note', 'engine-info', 'export-progress'].map(id => [id.replace(/-([a-z])/g, (_, c) => c.toUpperCase()), $(id)]));
const ctx = el.stage.getContext('2d', { alpha: false });
const AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext;
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const initialCharacter = new URL(location.href).searchParams.get('character');
const state = { character: CHARACTERS[initialCharacter] ? initialCharacter : 'bounce', phase: 'idle', audio: null, analysis: null, video: null, objectURL: null, synthetic: false, take: 0, export: 0, muted: true, previewStart: performance.now(), images: {}, renderer: null, audioContext: null, playbackContext: null, playbackSource: null, abort: null, exportAbort: null, disposed: false };
const mic = new BrowserMicrophone();
const analyzer = new BrowserAnalyzer({ onProgress: message => { if (state.phase === 'analyzing') status(message); } });

function status(message, error = false) { el.status.textContent = message; el.statusBox.classList.toggle('error', error); el.statusMark.textContent = error ? '!' : '✳'; }
function setPhase(phase) {
  state.phase = phase;
  document.body.classList.toggle('is-analyzing', phase === 'analyzing'); document.body.classList.toggle('is-exporting', phase === 'exporting');
  const listening = phase === 'requesting' || phase === 'recording';
  el.record.disabled = listening; el.record.classList.toggle('listening', listening);
  el.cancelRecord.hidden = !listening; el.listeningOverlay.hidden = !listening;
  el.demo.disabled = listening; el.recordLabel.textContent = phase === 'requesting' ? '마이크 허용을 기다리는 중' : phase === 'recording' ? '지금, 마음껏 소리 내봐요!' : state.audio ? '다시 5초 녹음하기' : '5초 녹음하고 만들기';
  el.recordHint.textContent = listening ? '5초 뒤에 자동으로 멈춰요' : '허밍, 비트박스, 아무 소리!';
  el.exportProgress.hidden = phase !== 'exporting';
}
function unlockAudio() {
  if (!AudioContextClass) return null;
  if (!state.audioContext || state.audioContext.state === 'closed') state.audioContext = new AudioContextClass();
  state.audioContext.resume().catch(() => {});
  return state.audioContext;
}
function closePipelineAudio() { const audio = state.audioContext; state.audioContext = null; if (audio && audio.state !== 'closed') audio.close().catch(() => {}); }
function stopSound() { try { state.playbackSource?.stop(); } catch {} state.playbackSource = null; state.playbackContext?.close().catch(() => {}); state.playbackContext = null; }
function clearVideo() { el.resultVideo.pause(); el.resultVideo.hidden = true; el.resultVideo.removeAttribute('src'); el.resultVideo.load(); if (state.objectURL) URL.revokeObjectURL(state.objectURL); state.objectURL = null; state.video = null; el.resultActions.hidden = true; }
function resetSoundButton() { el.soundToggle.hidden = !state.analysis; el.soundToggle.setAttribute('aria-pressed', String(!state.muted)); el.soundToggle.innerHTML = state.muted ? '소리 켜기 <span aria-hidden="true">♪</span>' : '소리 끄기 <span aria-hidden="true">♪</span>'; }
function rebuildRenderer() { state.renderer = createSceneRenderer({ character: state.character, image: state.images[state.character], analysis: state.analysis, synthetic: state.synthetic }); }
function updateCharacterUI() {
  for (const button of document.querySelectorAll('[data-character]')) { const chosen = button.dataset.character === state.character; button.classList.toggle('active', chosen); button.setAttribute('aria-pressed', String(chosen)); }
  const selected = CHARACTERS[state.character];
  el.previewCaption.textContent = `${selected.name} · ${state.synthetic ? '합성음 데모' : state.analysis ? '내 목소리로 완성' : '아직은 얌전함'}`;
  el.stageTag.textContent = state.synthetic ? '합성음 데모 · 목소리 아님' : state.analysis ? '이 소리의 주인은 나' : '목소리 주인 구함';
  el.stageTag.hidden = !!state.analysis; el.stageBottom.hidden = !!state.analysis;
  rebuildRenderer();
}
function abortExport() { state.export++; state.exportAbort?.abort(); state.exportAbort = null; }
function newTake() {
  state.take++; state.abort?.abort(); state.abort = new AbortController(); abortExport(); analyzer.cancel(); mic.cancel(); stopSound(); clearVideo(); closePipelineAudio();
  state.audio = null; state.analysis = null; state.synthetic = false; state.muted = true; state.previewStart = performance.now(); resetSoundButton(); updateCharacterUI();
  return state.take;
}
function markEngine(analysis) { el.engineInfo.textContent = analysis.engine === 'basic_pitch' ? '현재 분석: 공개 모델 Basic Pitch · 이 기기의 브라우저에서 실행' : '현재 분석: 로컬 소리 신호 분석 · AI 모델을 사용하지 않음'; }

async function loadImages() {
  await Promise.all(Object.values(CHARACTERS).map(character => new Promise((resolve, reject) => { const image = new Image(); image.onload = () => { state.images[character.id] = image; resolve(); }; image.onerror = () => reject(new Error('캐릭터를 불러오지 못했어요. 연결을 확인한 뒤 새로고침해주세요.')); image.src = character.asset; })));
  updateCharacterUI();
}
const imagesReady = loadImages().catch(error => { status(error.message, true); throw error; });
imagesReady.catch(() => {});

async function processTake(audio, take, synthetic) {
  if (take !== state.take || state.disposed) return;
  state.audio = audio; state.synthetic = synthetic; setPhase('analyzing'); status(synthetic ? '합성음으로 캐릭터를 깨우는 중…' : '네 소리에 맞춰 움직임을 만드는 중…');
  const analysis = await analyzer.analyze(audio.samples, audio.sampleRate, { engine: 'auto', sensitivity: 1, smoothing: .35, signal: state.abort.signal });
  if (take !== state.take || state.disposed) return;
  await imagesReady; if (take !== state.take) return;
  state.analysis = analysis; state.previewStart = performance.now(); updateCharacterUI(); resetSoundButton(); markEngine(analysis);
  if (matchMedia('(max-width: 680px)').matches) document.querySelector('.preview-panel').scrollIntoView({ behavior: reduceMotion.matches ? 'auto' : 'smooth', block: 'start' });
  const silent = !analysis.frames.some(frame => frame.energy > .035);
  if (silent) {
    setPhase('quiet'); status('아주 조용한 5초였어요. 마이크를 가까이 두고 “아아아↗” 해볼까요? 합성 데모도 바로 즐길 수 있어요.'); closePipelineAudio(); return;
  }
  status('움직임 완성! 저장할 영상을 준비하고 있어요.');
  await prepareVideo(take);
}

async function prepareVideo(take = state.take) {
  abortExport(); clearVideo(); stopSound(); state.muted = true; resetSoundButton();
  const exportID = state.export, abort = new AbortController(); state.exportAbort = abort;
  const renderer = createSceneRenderer({ character: state.character, image: state.images[state.character], analysis: state.analysis, synthetic: state.synthetic });
  setPhase('exporting'); el.exportProgress.setAttribute('aria-valuenow', '0'); el.exportProgress.firstElementChild.style.width = '0%';
  try {
    const video = await createMemeVideo({ renderFrame: renderer, samples: state.audio.samples, sampleRate: state.audio.sampleRate, duration: STAGE.duration, width: STAGE.width, height: STAGE.height, fps: STAGE.fps, signal: abort.signal, audioContext: state.audioContext, onProgress: ({ progress, message }) => { if (take !== state.take || exportID !== state.export) return; const percent = Math.round(Math.max(0, Math.min(1, progress || 0)) * 100); el.exportProgress.setAttribute('aria-valuenow', String(percent)); el.exportProgress.firstElementChild.style.width = `${percent}%`; status(message || `영상 준비 중 · ${percent}%`); } });
    if (take !== state.take || exportID !== state.export || state.disposed) return;
    stopSound(); state.video = video; state.objectURL = URL.createObjectURL(video.blob); el.resultVideo.src = state.objectURL; el.resultVideo.muted = state.muted; el.resultVideo.hidden = false; el.resultActions.hidden = false; resetSoundButton();
    el.formatNote.textContent = `${video.extension.toUpperCase()} · 720 × 960 · 5초 · 원음 포함${state.synthetic ? ' · 합성음 데모' : ''}`;
    el.share.hidden = !navigator.share;
    el.save.parentElement.classList.toggle('single', el.share.hidden);
    setPhase('ready'); status(state.synthetic ? '합성음 밈 완성! 이제 네 목소리로 한 번 더?' : '네 목소리 밈 완성! 소리를 켜고 확인해봐요.');
    if (!reduceMotion.matches || !state.muted) el.resultVideo.play().catch(() => { if (!state.muted) status('영상의 재생 버튼을 누르면 원음과 함께 볼 수 있어요.'); });
  } catch (error) {
    if (error.name === 'AbortError' || take !== state.take || exportID !== state.export) return;
    setPhase('preview'); status(`${error.message || '이 브라우저에서 영상을 준비하지 못했어요.'} 움직임은 미리 볼 수 있어요. 캐릭터를 다시 누르면 영상 준비를 재시도해요.`, true);
  } finally { if (take === state.take && exportID === state.export) { state.exportAbort = null; closePipelineAudio(); } }
}

function startRecording() {
  const take = newTake(); const audioContext = unlockAudio(); setPhase('requesting'); status('브라우저에서 마이크를 허용해주세요. 허용한 뒤부터 5초를 셉니다.'); el.countdown.textContent = '5'; el.listeningLabel.textContent = '마이크를 기다려요';
  const recording = mic.record({ audioContext, signal: state.abort.signal, onStarted: () => { if (take !== state.take) return; setPhase('recording'); el.listeningLabel.textContent = '듣고 있어요'; status('녹음 중이에요. 이 화면을 켜둔 채로 마음껏 소리 내보세요!'); }, onTick: elapsed => { if (take !== state.take) return; const remaining = Math.max(1, Math.ceil(STAGE.duration - elapsed)); el.countdown.textContent = String(remaining); el.recordLabel.textContent = `녹음 중 · ${remaining}초 남았어요`; }, onLevel: level => { if (take === state.take) document.querySelector('.level-meter').style.setProperty('--level', String(level)); } });
  // Same-origin model assets warm while the actual five-second take is recorded.
  analyzer.warmup().catch(() => {});
  recording.then(audio => processTake(audio, take, false)).catch(error => { if (take !== state.take || error.name === 'AbortError') return; setPhase('error'); status(microphoneError(error), true); closePipelineAudio(); });
}
el.record.addEventListener('click', startRecording);
$('record-again').addEventListener('click', startRecording);

el.cancelRecord.addEventListener('click', () => { newTake(); setPhase('idle'); status('녹음을 취소했어요. 준비되면 다시 눌러주세요.'); });

el.demo.addEventListener('click', () => {
  const take = newTake(); const audioContext = unlockAudio(); setPhase('analyzing'); status('합성음 데모를 불러오는 중… 마이크는 켜지지 않아요.');
  (async () => {
    if (!audioContext) throw new Error('이 브라우저는 오디오 재생을 지원하지 않아요. 최신 Safari·Chrome에서 열어주세요.');
    const response = await fetch(new URL('../production/audio/bop-bounce.wav', import.meta.url), { signal: state.abort.signal }); if (!response.ok) throw new Error('데모 소리를 불러오지 못했어요. 연결을 확인하고 다시 눌러주세요.');
    const decoded = await audioContext.decodeAudioData(await response.arrayBuffer());
    const mono = new Float32Array(decoded.length); for (let c = 0; c < decoded.numberOfChannels; c++) { const channel = decoded.getChannelData(c); for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / decoded.numberOfChannels; }
    await processTake({ samples: fiveSecondMono(mono, decoded.sampleRate), sampleRate: 22050 }, take, true);
  })().catch(error => { if (take !== state.take || error.name === 'AbortError') return; setPhase('error'); status(error.message, true); closePipelineAudio(); });
});

for (const button of document.querySelectorAll('[data-character]')) button.addEventListener('click', () => {
  state.character = button.dataset.character; updateCharacterUI();
  const url = new URL(location.href); url.searchParams.set('character', state.character); history.replaceState(null, '', url);
  if (state.analysis && state.audio && !['recording', 'requesting', 'analyzing', 'quiet'].includes(state.phase)) { unlockAudio(); state.previewStart = performance.now(); prepareVideo(); }
});

el.soundToggle.addEventListener('click', () => {
  state.muted = !state.muted; resetSoundButton();
  if (state.video) { el.resultVideo.muted = state.muted; if (!state.muted) el.resultVideo.play().catch(() => { status('영상의 재생 버튼을 눌러 소리를 들어보세요.'); }); }
  else if (state.muted) stopSound();
  else if (state.audio && AudioContextClass) {
    stopSound(); const audio = new AudioContextClass(); state.playbackContext = audio; audio.resume().catch(() => {});
    const buffer = audio.createBuffer(1, state.audio.samples.length, state.audio.sampleRate); buffer.copyToChannel(state.audio.samples, 0); const source = audio.createBufferSource(); source.buffer = buffer; source.loop = true; source.connect(audio.destination); state.playbackSource = source;
    const offset = ((performance.now() - state.previewStart) / 1000) % STAGE.duration; source.start(0, offset);
  }
});
el.resultVideo.addEventListener('volumechange', () => { if (state.video) { state.muted = el.resultVideo.muted; resetSoundButton(); } });

const filename = () => `bop-${state.character}-${state.synthetic ? 'demo' : 'my-voice'}`;
el.save.addEventListener('click', () => { if (state.video) { downloadVideo(state.video, filename()); status('영상 저장을 시작했어요. 휴대폰에서는 다운로드 폴더나 파일 앱을 확인해보세요.'); } });
el.share.addEventListener('click', () => { if (!state.video) return; shareVideo(state.video, filename()).then(result => { if (result?.method === 'cancelled') return; status(result?.method === 'download' ? '이 기기는 파일 공유 대신 영상을 저장했어요. 저장한 영상을 친구에게 보내주세요.' : '친구에게 보낼 영상을 준비했어요.'); }).catch(error => { if (error.name === 'AbortError') return; status('이 기기에서 영상 공유가 안 되면 “영상 저장”으로 저장한 뒤 보내주세요.'); }); });
el.remixLink.addEventListener('click', async () => {
  const url = new URL('./', location.href); url.searchParams.set('character', state.character);
  try { await navigator.clipboard.writeText(url.href); status('도전 링크를 복사했어요! 네 목소리는 링크에 담기지 않아요.'); }
  catch { if (navigator.share) { try { await navigator.share({ title: 'BOP! 내 목소리로 만드는 5초 밈', text: '이 캐릭터로 네 소리도 들려줘!', url: url.href }); } catch (error) { if (error.name !== 'AbortError') status(`도전 주소: ${url.href}`); } } else status(`도전 주소: ${url.href}`); }
});

let lastDraw = -1000, lastRenderer = null;
function draw(now) {
  if (state.disposed) return;
  if (el.resultVideo.hidden && !document.hidden && now - lastDraw >= 1000 / STAGE.fps && (!reduceMotion.matches || lastRenderer !== state.renderer)) { const elapsed = state.analysis ? ((now - state.previewStart) / 1000) % STAGE.duration : now / 1000; state.renderer?.(ctx, reduceMotion.matches ? state.analysis ? 1.8 : 0 : elapsed); lastDraw = now; lastRenderer = state.renderer; }
  requestAnimationFrame(draw);
}
function interrupt() {
  if (['requesting', 'recording'].includes(state.phase)) { newTake(); setPhase('idle'); status('화면을 벗어나 녹음을 멈췄어요. 준비되면 다시 눌러주세요.'); }
  else { stopSound(); el.resultVideo.pause(); if (state.phase === 'exporting') { abortExport(); closePipelineAudio(); setPhase('preview'); status('화면을 벗어나 영상 준비를 멈췄어요. 캐릭터를 누르면 다시 준비해요.'); } }
}
document.addEventListener('visibilitychange', () => { if (document.hidden) interrupt(); });
window.addEventListener('pagehide', () => { interrupt(); state.disposed = true; state.abort?.abort(); abortExport(); mic.dispose(); analyzer.dispose(); closePipelineAudio(); stopSound(); if (state.objectURL) URL.revokeObjectURL(state.objectURL); });
window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });

updateCharacterUI(); requestAnimationFrame(draw);
registerOffline().catch(() => {});
analyzer.warmup().then(metadata => { if (!state.analysis) el.engineInfo.textContent = metadata.engine === 'basic_pitch' ? '공개 모델 Basic Pitch 준비 완료 · 브라우저 내부 처리' : '로컬 신호 분석 준비 완료 · AI 모델을 사용하지 않음'; }).catch(() => { el.engineInfo.textContent = '기본 소리 분석은 이 브라우저 안에서 처리됩니다.'; });
