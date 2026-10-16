/** Deterministic local analysis. No DOM, network, storage or model downloads. */
export const FRAME_RATE = 60;
export const MODEL_RATE = 22050;
export const WINDOW_SAMPLES = 43844;
export const OVERLAP_SAMPLES = 30 * 256;
export const MAX_DURATION = 15;
const MIN_EVENT_SPACING = .14;
const clip = (value, low = 0, high = 1) => Math.min(high, Math.max(low, value));

export class AnalysisError extends Error { constructor(message) { super(message); this.name = 'AnalysisError'; } }
export class ModelUnavailableError extends Error { constructor(message) { super(message); this.name = 'ModelUnavailableError'; } }

// Python round()/numpy.rint use ties-to-even, unlike Math.round().
function evenRound(value) {
  const lower = Math.floor(value);
  return value - lower === .5 ? (lower % 2 === 0 ? lower : lower + 1) : Math.round(value);
}
const round6 = (value) => evenRound(value * 1e6) / 1e6;

export function validateInput(samples, sampleRate, { engine = 'auto', sensitivity = 1, smoothing = .35 } = {}) {
  if (!Number.isInteger(sampleRate) || sampleRate < 8000 || sampleRate > 96000) throw new AnalysisError('sample_rate는 8000–96000 범위의 정수여야 합니다.');
  if (!['auto', 'signal', 'basic_pitch'].includes(engine)) throw new AnalysisError('engine은 auto, signal, basic_pitch 중 하나여야 합니다.');
  for (const [name, value, low, high] of [['sensitivity', sensitivity, .1, 3], ['smoothing', smoothing, 0, 1]]) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < low || value > high) throw new AnalysisError(`${name} 허용 범위는 ${low}–${high}의 유한한 숫자입니다.`);
  }
  if (!Array.isArray(samples) && !(ArrayBuffer.isView(samples) && !(samples instanceof DataView))) throw new AnalysisError('samples는 1차원 숫자 배열이어야 합니다.');
  if (samples.length > sampleRate * MAX_DURATION) throw new AnalysisError('오디오는 최대 15초까지 분석할 수 있습니다.');
  const audio = new Float64Array(samples.length);
  let clipped = false;
  for (let index = 0; index < samples.length; index++) {
    const value = samples[index];
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new AnalysisError('samples에는 NaN/Infinity가 없는 유한한 숫자만 사용할 수 있습니다.');
    clipped ||= Math.abs(value) > 1;
    audio[index] = clip(value, -1, 1);
  }
  return { audio, rate: sampleRate, requested: engine, sensitivity, smoothing,
    warnings: clipped ? ['-1~1을 넘는 입력 샘플을 클리핑했습니다.'] : [] };
}

function quantile(values, fraction) {
  if (!values.length) return 0;
  const sorted = Array.from(values).sort((a, b) => a - b);
  const position = (sorted.length - 1) * fraction;
  const low = Math.floor(position);
  return sorted[low] + (sorted[Math.ceil(position)] - sorted[low]) * (position - low);
}

function smooth(values, active, smoothing, initial = 0) {
  const result = new Float64Array(values.length).fill(initial);
  const alpha = 1 - .88 * smoothing;
  let previous = null;
  for (let index = 0; index < values.length; index++) {
    if (!active[index]) { previous = null; continue; }
    previous = previous === null ? values[index] : alpha * values[index] + (1 - alpha) * previous;
    result[index] = previous;
  }
  return result;
}

function resample(audio, fromRate, toRate, count, zeroOutside = false) {
  const result = new Float64Array(count);
  if (!audio.length) return result;
  for (let index = 0; index < count; index++) {
    const time = index / toRate;
    if (time > (audio.length - 1) / fromRate) { result[index] = zeroOutside ? 0 : audio[audio.length - 1]; continue; }
    if (fromRate === toRate) { result[index] = audio[index]; continue; }
    let low = Math.floor(time * fromRate);
    while (low > 0 && low / fromRate > time) low--;
    while (low + 1 < audio.length && (low + 1) / fromRate <= time) low++;
    const high = Math.min(low + 1, audio.length - 1);
    const width = high / fromRate - low / fromRate;
    result[index] = width > 0 ? audio[low] + (audio[high] - audio[low]) / width * (time - low / fromRate) : audio[low];
  }
  return result;
}

function autocorrelationPitch(audio, rate, times, rms, sensitivity) {
  const reduced = resample(audio, rate, 8000, Math.max(1, Math.ceil(audio.length * 8000 / rate)));
  const pitch = new Float64Array(times.length).fill(.5);
  const confidence = new Float64Array(times.length);
  const hann = Float64Array.from({ length: 640 }, (_, index) => .5 - .5 * Math.cos(2 * Math.PI * index / 639));
  const window = new Float64Array(640);
  const correlation = new Float64Array(124);
  for (let frame = 0; frame < times.length; frame++) {
    if (rms[frame] < .004 / sensitivity) continue;
    const center = Math.min(evenRound(times[frame] * 8000), reduced.length - 1);
    let mean = 0;
    for (let index = 0; index < 640; index++) {
      const at = center + index - 320;
      window[index] = at >= 0 && at < reduced.length ? reduced[at] : 0;
      mean += window[index];
    }
    mean /= 640;
    let zero = 0;
    for (let index = 0; index < 640; index++) { window[index] = (window[index] - mean) * hann[index]; zero += window[index] ** 2; }
    if (zero < 1e-10) continue;
    // This bounded direct correlation equals the zero-padded FFT correlation
    // used by Python, at the only lags it reads (8..123).
    let lag = 8;
    let best = -Infinity;
    for (let candidate = 8; candidate <= 123; candidate++) {
      let value = 0;
      for (let index = 0; index < 640 - candidate; index++) value += window[index] * window[index + candidate];
      correlation[candidate] = value;
      if (value > best) { best = value; lag = candidate; }
    }
    const strength = best / zero;
    if (strength < .55) continue;
    let offset = 0;
    if (lag > 8 && lag < 123) {
      const a = correlation[lag - 1], b = correlation[lag], c = correlation[lag + 1];
      const denominator = a - 2 * b + c;
      if (Math.abs(denominator) > 1e-12) offset = clip(.5 * (a - c) / denominator, -.5, .5);
    }
    const frequency = 8000 / (lag + offset);
    pitch[frame] = clip(Math.log2(frequency / 70) / Math.log2(1000 / 70));
    confidence[frame] = clip(strength);
  }
  return { pitch, confidence };
}

function prepare(samples, sampleRate, options) {
  const state = validateInput(samples, sampleRate, options);
  const { audio, rate, sensitivity, smoothing } = state;
  const duration = audio.length / rate;
  const count = duration ? Math.ceil(duration * FRAME_RATE) + 1 : 1;
  const times = Float64Array.from({ length: count }, (_, index) => Math.min(index / FRAME_RATE, duration));
  let energy = new Float64Array(count), pitch = new Float64Array(count).fill(.5);
  let confidence = new Float64Array(count), onset = new Float64Array(count);
  const active = new Uint8Array(count);
  if (audio.length) {
    const squared = new Float64Array(audio.length + 1);
    for (let index = 0; index < audio.length; index++) squared[index + 1] = squared[index] + audio[index] ** 2;
    const radius = Math.max(1, evenRound(.020 * rate));
    const rms = Float64Array.from(times, (time) => {
      const center = evenRound(time * rate), start = Math.max(0, center - radius), end = Math.min(audio.length, center + radius);
      return Math.sqrt(Math.max(0, (squared[end] - squared[start]) / Math.max(1, end - start)));
    });
    ({ pitch, confidence } = autocorrelationPitch(audio, rate, times, rms, sensitivity));
    const nonvoiced = Array.from(rms).filter((_, index) => confidence[index] === 0);
    const noise = nonvoiced.length >= Math.max(5, count * .1) ? quantile(nonvoiced, .2) : quantile(rms, .2) * .05;
    const threshold = Math.max(.006 / sensitivity, noise * 2.4 / Math.sqrt(sensitivity));
    const voicedRms = [];
    for (let index = 0; index < count; index++) { active[index] = Number(rms[index] > threshold); if (active[index]) voicedRms.push(rms[index]); }
    if (voicedRms.length) {
      const reference = Math.max(quantile(voicedRms, .95), threshold + 1e-8);
      const rawEnergy = Float64Array.from(rms, (value, index) => active[index] ? Math.sqrt(clip((value - threshold) / (reference - threshold))) : 0);
      energy = smooth(rawEnergy, active, smoothing);
      onset = Float64Array.from(rms, (value, index) => active[index] ? clip(Math.max(0, value - (index ? rms[index - 1] : 0)) / Math.max(reference * .30, 1e-8)) : 0);
    }
    for (let index = 0; index < count; index++) { if (!active[index]) confidence[index] = 0; if (!confidence[index]) pitch[index] = .5; }
  }
  return { ...state, duration, times, energy, pitch, confidence, onset, active };
}

function finalize(state, modelResult) {
  let { pitch, confidence, onset } = state;
  const { audio, rate, times, energy, active, smoothing } = state;
  if (modelResult) {
    pitch = Float64Array.from(times, (_, index) => active[index] && modelResult.confidence[index] > 0 ? modelResult.pitch[index] : .5);
    confidence = Float64Array.from(times, (_, index) => active[index] ? modelResult.confidence[index] : 0);
    // A note change alone is not a physical impact. Preserve the RMS attack gate.
    onset = Float64Array.from(onset, (value, index) => active[index] ? clip(value * (.9 + .2 * modelResult.onset[index])) : 0);
  }
  pitch = smooth(pitch, Uint8Array.from(active, (value, index) => Number(value && confidence[index] > 0)), smoothing, .5);
  const candidates = [];
  for (let index = 0; index < onset.length; index++) {
    if (onset[index] >= .35 && (!index || onset[index] > onset[index - 1]) && (index === onset.length - 1 || onset[index] >= onset[index + 1])) candidates.push(index);
  }
  candidates.sort((a, b) => onset[b] - onset[a]);
  const selected = [];
  for (const index of candidates) if (selected.every((other) => Math.abs(times[index] - times[other]) >= MIN_EVENT_SPACING)) selected.push(index);
  const events = [];
  for (const index of selected.sort((a, b) => a - b)) {
    let eventTime = times[index];
    const start = Math.max(0, evenRound((eventTime - .025) * rate));
    const stop = Math.min(audio.length, evenRound((eventTime + .025) * rate));
    if (stop > start) {
      let maximum = -1, best = start;
      for (let at = start; at < stop; at++) if (Math.abs(audio[at]) > maximum) { maximum = Math.abs(audio[at]); best = at; }
      eventTime = best / rate;
    }
    if (!events.length || eventTime - events[events.length - 1].time >= MIN_EVENT_SPACING - 1e-6) events.push({ time: round6(eventTime), kind: 'impact', strength: round6(onset[index]) });
  }
  const progress = new Float64Array(times.length);
  for (let index = 1; index < times.length; index++) progress[index] = progress[index - 1] + (times[index] - times[index - 1]) * energy[index];
  const total = progress[progress.length - 1];
  if (total > 0) { for (let index = 0; index < progress.length; index++) progress[index] /= total; }
  else { const first = energy.findIndex((value) => value > 0); if (first >= 0) progress.fill(1, first); }
  const frames = Array.from(times, (time, index) => ({ time: round6(time), energy: round6(energy[index]), pitch: round6(pitch[index]),
    pitch_confidence: round6(confidence[index]), onset: round6(onset[index]), progress: round6(progress[index]) }));
  return { version: 1, duration: state.duration, sample_rate: rate, engine: modelResult ? 'basic_pitch' : 'signal', frames, events, warnings: state.warnings };
}

export function analyzeSignal(samples, sampleRate, options = {}) { return finalize(prepare(samples, sampleRate, { ...options, engine: 'signal' })); }

/** model(samples, rate, times) must run a real model, never a DSP impersonation. */
export async function analyze(samples, sampleRate, options = {}, model = null) {
  const state = prepare(samples, sampleRate, options);
  let modelResult;
  if (state.requested !== 'signal') {
    try {
      if (!model) throw new ModelUnavailableError('브라우저 Basic Pitch 모델을 사용할 수 없습니다.');
      modelResult = await model(state.audio, state.rate, state.times);
      for (const key of ['pitch', 'confidence', 'onset']) {
        if (!modelResult?.[key] || modelResult[key].length !== state.times.length || !Array.from(modelResult[key]).every(Number.isFinite)) throw new ModelUnavailableError('Basic Pitch 출력 시간축이나 수치가 올바르지 않습니다.');
      }
    } catch (error) {
      modelResult = undefined;
      if (error?.name === 'AbortError') throw error;
      if (state.requested === 'basic_pitch') throw error;
      state.warnings.push(`${error?.message || 'Basic Pitch 모델 분석에 실패했습니다.'} 신호 분석으로 전환했습니다. AI 모델 분석 결과가 아닙니다.`);
    }
  }
  return finalize(state, modelResult);
}

function interpolate(times, values, at, zeroOutside = false) {
  const output = new Float64Array(at.length);
  let cursor = 0;
  for (let index = 0; index < at.length; index++) {
    const time = at[index];
    if (time < times[0]) { output[index] = zeroOutside ? 0 : values[0]; continue; }
    if (time > times[times.length - 1]) { output[index] = zeroOutside ? 0 : values[values.length - 1]; continue; }
    while (cursor + 1 < times.length && times[cursor + 1] <= time) cursor++;
    const next = Math.min(cursor + 1, times.length - 1);
    const width = times[next] - times[cursor];
    output[index] = width > 0 ? values[cursor] + (values[next] - values[cursor]) * (time - times[cursor]) / width : values[cursor];
  }
  return output;
}

function identifyOutputs(raw) {
  const named = { 'StatefulPartitionedCall:0': 'contour', 'StatefulPartitionedCall:1': 'note', 'StatefulPartitionedCall:2': 'onset' };
  const result = {};
  for (const [name, tensor] of Object.entries(raw)) {
    const dims = tensor.dims || tensor.shape;
    if (!dims || dims.length !== 3 || dims[0] !== 1 || tensor.data.length !== dims[1] * dims[2]) throw new ModelUnavailableError('Basic Pitch 출력 형태가 올바르지 않습니다.');
    let kind = ['contour', 'onset', 'note'].find((candidate) => name.toLowerCase().includes(candidate)) || named[name];
    if (dims[2] === 264) kind = 'contour';
    if ((kind === 'note' || kind === 'onset') && dims[2] !== 88) throw new ModelUnavailableError('Basic Pitch 음표 출력 크기가 다릅니다.');
    for (const value of tensor.data) if (!Number.isFinite(value)) throw new ModelUnavailableError('Basic Pitch 출력에 유한하지 않은 값이 있습니다.');
    if (kind) result[kind] = { data: tensor.data, frames: dims[1], bins: dims[2] };
  }
  if (!result.note || !result.onset || result.note.frames !== result.onset.frames) throw new ModelUnavailableError('Basic Pitch note/onset 출력 이름 또는 시간축을 확인할 수 없습니다.');
  return result;
}

/** Full Python-compatible overlap/absolute-time postprocessing, bounded to 12 chunks. */
export async function inferModelPitch(audio, rate, times, runModel, onProgress = () => {}) {
  const duration = audio.length / rate;
  const count = Math.max(1, evenRound(duration * MODEL_RATE));
  const resampled = resample(audio, rate, MODEL_RATE, count, true);
  const halfOverlap = OVERLAP_SAMPLES / 2;
  const hop = WINDOW_SAMPLES - OVERLAP_SAMPLES;
  const starts = [];
  for (let start = 0; start < Math.max(1, count); start += hop) starts.push(start);
  if (starts.length > 12) throw new ModelUnavailableError('Basic Pitch 분석 창 수가 안전 한도를 넘었습니다.');
  const predictions = [];
  for (let index = 0; index < starts.length; index++) {
    const start = starts[index];
    const chunk = new Float32Array(WINDOW_SAMPLES);
    for (let at = 0; at < WINDOW_SAMPLES; at++) {
      const source = start + at - halfOverlap;
      if (source >= 0 && source < resampled.length) chunk[at] = resampled[source];
    }
    onProgress(`소리를 움직임으로 바꾸는 중… ${index + 1}/${starts.length}`);
    const raw = await runModel(chunk);
    const { note, onset } = identifyOutputs(raw);
    for (let frame = 0; frame < note.frames; frame++) {
      const time = (start - halfOverlap + frame * 256) / MODEL_RATE;
      if (time < 0 || time > duration || (index > 0 && frame < 15) || (index < starts.length - 1 && frame >= note.frames - 15)) continue;
      let maximum = -Infinity, midiIndex = 0, onsetMaximum = 0;
      for (let bin = 0; bin < 88; bin++) {
        const value = clip(note.data[frame * 88 + bin]);
        if (value > maximum) { maximum = value; midiIndex = bin; }
        onsetMaximum = Math.max(onsetMaximum, clip(onset.data[frame * 88 + bin]));
      }
      const frequency = 440 * 2 ** ((21 + midiIndex - 69) / 12);
      predictions.push({ time, pitch: clip(Math.log2(frequency / 70) / Math.log2(1000 / 70)), confidence: maximum, onset: onsetMaximum });
    }
  }
  if (!predictions.length) return { pitch: new Float64Array(times.length).fill(.5), confidence: new Float64Array(times.length), onset: new Float64Array(times.length) };
  predictions.sort((a, b) => a.time - b.time);
  const sourceTimes = predictions.map((item) => item.time);
  const pitch = interpolate(sourceTimes, predictions.map((item) => item.pitch), times);
  const confidence = interpolate(sourceTimes, predictions.map((item) => item.confidence), times, true);
  const onset = interpolate(sourceTimes, predictions.map((item) => item.onset), times, true);
  for (let index = 0; index < confidence.length; index++) if (confidence[index] < .20) { confidence[index] = 0; pitch[index] = .5; }
  return { pitch, confidence, onset };
}
