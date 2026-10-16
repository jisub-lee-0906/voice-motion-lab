import { analyze, inferModelPitch, ModelUnavailableError } from './analysis.mjs';

const VENDOR = new URL('../browser-proof/vendor/', import.meta.url);
const MODEL_SHA256 = '2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec';
let ort;
let session;
let sessionPromise;
let sessionError;
const progressRequests = new Set();

const progress = (id, message) => self.postMessage({ id, type: 'progress', message });
function broadcast(message) { for (const id of progressRequests) progress(id, message); }

async function modelBytes() {
  const url = new URL('basic_pitch.onnx', VENDOR);
  if (url.origin !== self.location.origin) throw new ModelUnavailableError('외부 모델 주소는 사용할 수 없습니다.');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, { mode: 'same-origin', credentials: 'same-origin', signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength !== 230444) throw new Error('모델 파일 크기가 다릅니다.');
    if (!crypto?.subtle) throw new Error('모델 검증에는 HTTPS 또는 localhost가 필요합니다.');
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hash = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('');
    if (hash !== MODEL_SHA256) throw new Error('모델 SHA-256 검증에 실패했습니다.');
    return bytes;
  } catch (error) {
    throw new ModelUnavailableError(`Basic Pitch 모델을 이 브라우저에서 불러오지 못했습니다. ${error?.message || ''}`);
  } finally { clearTimeout(timer); }
}

async function getSession() {
  if (session) return session;
  if (sessionError) throw sessionError;
  if (!sessionPromise) {
    sessionPromise = (async () => {
      broadcast('작은 AI 모델을 브라우저에 준비하는 중…');
      const runtimeUrl = new URL('ort.wasm.min.mjs', VENDOR);
      if (runtimeUrl.origin !== self.location.origin) throw new Error('동일 출처 런타임만 사용할 수 있습니다.');
      ort = await import(runtimeUrl.href);
      if (ort.env.versions.web !== '1.30.0') throw new Error('검증한 ONNX Runtime Web 버전이 아닙니다.');
      ort.env.wasm.numThreads = 1;
      ort.env.wasm.proxy = false;
      ort.env.wasm.wasmPaths = VENDOR.href;
      ort.env.logLevel = 'error';
      const bytes = await modelBytes();
      broadcast('AI 모델을 CPU WASM으로 여는 중…');
      const candidate = await ort.InferenceSession.create(bytes, { executionProviders: ['wasm'], graphOptimizationLevel: 'all' });
      if (candidate.inputNames.length !== 1 || candidate.inputNames[0] !== 'serving_default_input_2:0') {
        await candidate.release();
        throw new Error('Basic Pitch 입력 이름이 다릅니다.');
      }
      session = candidate;
      return session;
    })().catch((error) => {
      sessionError = error instanceof ModelUnavailableError ? error : new ModelUnavailableError(`브라우저 Basic Pitch 실행기를 사용할 수 없습니다. ${error?.message || ''}`);
      throw sessionError;
    });
  }
  return sessionPromise;
}

async function runModel(chunk) {
  const input = new ort.Tensor('float32', chunk, [1, 43844, 1]);
  let outputs;
  try {
    outputs = await session.run({ 'serving_default_input_2:0': input });
    const copied = {};
    for (const [name, tensor] of Object.entries(outputs)) copied[name] = { data: Float32Array.from(tensor.data), dims: [...tensor.dims] };
    return copied;
  } catch (error) { throw new ModelUnavailableError(`Basic Pitch 브라우저 추론에 실패했습니다. ${error?.message || ''}`); }
  finally { input.dispose(); if (outputs) for (const tensor of Object.values(outputs)) tensor.dispose(); }
}

self.addEventListener('message', async ({ data }) => {
  if (!data || !Number.isInteger(data.id)) return;
  const { id, action } = data;
  progressRequests.add(id);
  try {
    if (action === 'warmup') {
      let result;
      try {
        await getSession();
        result = { available: true, engine: 'basic_pitch', provider: 'wasm', runtime: 'onnxruntime-web@1.30.0', threads: 1 };
      } catch (error) {
        result = { available: false, engine: 'signal', provider: 'signal', warning: `${error.message} 신호 분석을 사용할 수 있습니다. AI 모델 분석 결과가 아닙니다.` };
      }
      self.postMessage({ id, type: 'result', result });
    } else if (action === 'analyze') {
      progress(id, '소리의 크기와 리듬을 읽는 중…');
      const model = async (audio, rate, times) => {
        await getSession();
        if (!audio.length) return { pitch: new Float64Array(times.length).fill(.5), confidence: new Float64Array(times.length), onset: new Float64Array(times.length) };
        return inferModelPitch(audio, rate, times, runModel, (message) => progress(id, message));
      };
      const result = await analyze(data.samples, data.sampleRate, data.options, model);
      for (const warning of data.inputWarnings || []) if (!result.warnings.includes(warning)) result.warnings.unshift(warning);
      self.postMessage({ id, type: 'result', result });
    } else throw new Error('알 수 없는 브라우저 분석 요청입니다.');
  } catch (error) {
    self.postMessage({ id, type: 'error', name: error?.name || 'Error', message: String(error?.message || error) });
  } finally { progressRequests.delete(id); }
});
