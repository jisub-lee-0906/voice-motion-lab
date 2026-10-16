import * as ort from './vendor/ort.wasm.min.mjs';

ort.env.wasm.numThreads = 1;
ort.env.wasm.proxy = false;
ort.env.wasm.wasmPaths = new URL('./vendor/', import.meta.url).href;
ort.env.logLevel = 'warning';

let session;
let busy = false;
const progress = (message) => self.postMessage({ type: 'progress', message });

async function localBytes(relative) {
  const url = new URL(relative, import.meta.url);
  if (url.origin !== self.location.origin) throw new Error('Only same-origin static assets are allowed.');
  const response = await fetch(url, { credentials: 'same-origin', mode: 'same-origin' });
  if (!response.ok) throw new Error(`Static asset ${url.pathname}: HTTP ${response.status}`);
  return { url: url.pathname, bytes: await response.arrayBuffer() };
}

async function localJson(relative) {
  const { bytes } = await localBytes(relative);
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function sha256(bytes) {
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].map((value) => value.toString(16).padStart(2, '0')).join('');
}

function firstPcmChunk(buffer) {
  const view = new DataView(buffer);
  const text = (at, length) => String.fromCharCode(...new Uint8Array(buffer, at, length));
  if (view.byteLength < 12 || text(0, 4) !== 'RIFF' || text(8, 4) !== 'WAVE') throw new Error('Expected RIFF WAV.');
  let format;
  let data;
  for (let at = 12; at + 8 <= view.byteLength;) {
    const size = view.getUint32(at + 4, true);
    const start = at + 8;
    if (start + size > view.byteLength) throw new Error('Truncated WAV chunk.');
    const name = text(at, 4);
    if (name === 'fmt ' && size >= 16) {
      format = { codec: view.getUint16(start, true), channels: view.getUint16(start + 2, true),
        sample_rate: view.getUint32(start + 4, true), bits: view.getUint16(start + 14, true) };
    }
    if (name === 'data') data = { start, size };
    at = start + size + (size % 2);
  }
  if (!format || format.codec !== 1 || format.channels !== 1 || format.sample_rate !== 22050 || format.bits !== 16) {
    throw new Error('This exact proof expects mono PCM16 at 22050 Hz; it does not silently resample.');
  }
  if (!data || data.size < 43844 * 2) throw new Error('Fixture is shorter than 43844 samples.');
  const samples = new Float32Array(43844);
  for (let index = 0; index < samples.length; index++) samples[index] = view.getInt16(data.start + index * 2, true) / 32768;
  return samples;
}

function float32LittleEndian(values) {
  const bytes = new ArrayBuffer(values.length * 4);
  const view = new DataView(bytes);
  for (let index = 0; index < values.length; index++) view.setFloat32(index * 4, values[index], true);
  return bytes;
}

async function summarize(tensor) {
  const values = tensor.data;
  let min = Infinity;
  let max = -Infinity;
  let sum = 0;
  let squares = 0;
  let checksum = 0;
  let finite = true;
  for (let index = 0; index < values.length; index++) {
    const value = Number(values[index]);
    finite &&= Number.isFinite(value);
    min = Math.min(min, value);
    max = Math.max(max, value);
    sum += value;
    squares += value * value;
    checksum += value * (1 + (index % 251) / 251);
  }
  return { shape: [...tensor.dims], dtype: tensor.type, count: values.length, finite,
    min, max, sum, mean: sum / values.length, rms: Math.sqrt(squares / values.length),
    weighted_checksum: checksum, sha256_informational: await sha256(float32LittleEndian(values)) };
}

function compare(outputs, reference, inputHash, modelHash, audioHash) {
  const tolerance = reference.comparison_tolerances;
  const checks = [];
  checks.push({ name: 'same_model_sha256', pass: modelHash === reference.model_sha256 });
  checks.push({ name: 'same_fixture_sha256', pass: audioHash === reference.fixture.file_sha256 });
  checks.push({ name: 'same_input_float32_sha256', pass: inputHash === reference.fixture.input_float32_sha256 });
  for (const [name, expected] of Object.entries(reference.outputs)) {
    const actual = outputs[name];
    checks.push({ name: `${name}:shape`, pass: Boolean(actual) && JSON.stringify(actual.shape) === JSON.stringify(expected.shape) });
    checks.push({ name: `${name}:finite_float32`, pass: Boolean(actual?.finite) && actual.dtype === 'float32' });
    if (!actual) continue;
    for (const metric of ['min', 'max', 'sum', 'mean', 'rms', 'weighted_checksum']) {
      const error = Math.abs(actual[metric] - expected[metric]);
      const allowed = tolerance.absolute + tolerance.relative * Math.abs(expected[metric]);
      checks.push({ name: `${name}:${metric}`, pass: error <= allowed, absolute_error: error, allowed_error: allowed });
    }
  }
  return { pass: checks.every((check) => check.pass), scope: 'Aggregate numeric equivalence, not elementwise/full-analysis validation.',
    tolerances: tolerance, output_sha256_is_informational: true, checks };
}

self.addEventListener('message', async ({ data }) => {
  if (data?.type !== 'run' || busy) return;
  busy = true;
  try {
    progress('공식 런타임·모델·합성 데모 확인…');
    const [manifest, reference, model, audio] = await Promise.all([
      localJson('./manifest.json'), localJson('./reference.json'),
      localBytes('./vendor/basic_pitch.onnx'), localBytes('../production/audio/bop-bounce.wav'),
    ]);
    const [modelHash, audioHash] = await Promise.all([sha256(model.bytes), sha256(audio.bytes)]);
    if (modelHash !== manifest.model.sha256) throw new Error('Model SHA-256 differs from the approved project file.');
    const samples = firstPcmChunk(audio.bytes);
    const inputHash = await sha256(float32LittleEndian(samples));
    const prepareStarted = performance.now();
    if (!session) {
      progress('실제 Basic Pitch ONNX 세션 생성 · CPU WASM 1 thread…');
      session = await ort.InferenceSession.create(model.bytes, { executionProviders: ['wasm'], graphOptimizationLevel: 'all' });
    }
    const modelPrepareMs = performance.now() - prepareStarted;
    const inputName = reference.input.name;
    const input = new ort.Tensor('float32', samples, [1, 43844, 1]);
    progress('실제 ONNX 모델 추론 중…');
    const started = performance.now();
    const raw = await session.run({ [inputName]: input });
    const inferenceMs = performance.now() - started;
    const outputs = {};
    for (const [name, tensor] of Object.entries(raw)) outputs[name] = await summarize(tensor);
    const result = {
      format: 'voice-motion-browser-inference-proof', version: 1,
      scope: reference.scope,
      runtime: { package: manifest.package, version: manifest.version, provider: 'wasm', threads: ort.env.wasm.numThreads,
        worker: true, actual_inference: true, cross_origin_isolated: self.crossOriginIsolated,
        user_agent: self.navigator.userAgent },
      model_sha256: modelHash,
      input: { name: inputName, shape: [1, 43844, 1], dtype: 'float32', sample_rate: 22050,
        input_float32_sha256: inputHash, fixture_sha256: audioHash },
      timing: { model_prepare_ms: modelPrepareMs, inference_ms: inferenceMs },
      outputs,
      features: { note_peak: outputs['StatefulPartitionedCall:1'].max,
        onset_peak: outputs['StatefulPartitionedCall:2'].max, contour_peak: outputs['StatefulPartitionedCall:0'].max },
      comparison: compare(outputs, reference, inputHash, modelHash, audioHash),
      privacy: { audio_transmitted: false, microphone_requested: false, static_same_origin_resources_only: true,
        note: 'Fixture/model/runtime fetched locally; no audio or inference arrays are sent over the network.' },
    };
    for (const tensor of Object.values(raw)) tensor.dispose();
    input.dispose();
    self.postMessage({ type: 'result', result });
  } catch (error) {
    self.postMessage({ type: 'error', message: String(error?.message || error), stack: String(error?.stack || '') });
  } finally {
    busy = false;
  }
});
