import { validateInput } from './analysis.mjs';

const abortError = (message = '분석을 취소했습니다.') => {
  const error = new Error(message); error.name = 'AbortError'; return error;
};

/** One private Worker/session per instance. Recorded audio never leaves it. */
export class BrowserAnalyzer {
  constructor({ onProgress = () => {}, workerFactory } = {}) {
    if (typeof onProgress !== 'function') throw new TypeError('onProgress must be a function.');
    this.onProgress = onProgress;
    this._workerFactory = workerFactory || (() => new Worker(new URL('./analysis.worker.mjs', import.meta.url), { type: 'module' }));
    this._worker = null;
    this._requests = new Map();
    this._nextId = 1;
    this._disposed = false;
    this._warmupPromise = null;
  }

  _ensureWorker() {
    if (this._disposed) throw new Error('이 분석기는 종료되었습니다. 새로 시작해 주세요.');
    if (this._worker) return this._worker;
    let worker;
    try { worker = this._workerFactory(); }
    catch (error) { throw new Error(`브라우저 Worker를 시작할 수 없습니다: ${error?.message || error}`); }
    this._worker = worker;
    worker.addEventListener('message', ({ data }) => {
      if (this._worker !== worker || !data || !this._requests.has(data.id)) return;
      if (data.type === 'progress') {
        try { this.onProgress(String(data.message)); } catch { /* UI progress must not break analysis. */ }
        return;
      }
      const request = this._requests.get(data.id);
      this._requests.delete(data.id);
      request.cleanup();
      if (data.type === 'result') request.resolve(data.result);
      else {
        const error = new Error(data.message || '브라우저 분석에 실패했습니다.');
        error.name = data.name || 'Error';
        request.reject(error);
      }
    });
    const fail = (event) => {
      if (this._worker !== worker) return;
      const error = new Error(event?.message || '브라우저 분석 Worker가 중단되었습니다. 다시 시도해 주세요.');
      this._reset(error);
    };
    worker.addEventListener('error', fail);
    worker.addEventListener('messageerror', fail);
    return worker;
  }

  _reset(error) {
    const worker = this._worker;
    this._worker = null;
    this._warmupPromise = null;
    worker?.terminate();
    for (const request of this._requests.values()) { request.cleanup(); request.reject(error); }
    this._requests.clear();
  }

  _request(action, payload = {}, transfer = [], signal) {
    if (signal?.aborted) return Promise.reject(abortError());
    if (signal && (typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function')) return Promise.reject(new TypeError('signal must be an AbortSignal.'));
    let worker;
    try { worker = this._ensureWorker(); } catch (error) { return Promise.reject(error); }
    const id = this._nextId++;
    return new Promise((resolve, reject) => {
      const abort = () => this.cancel();
      const timer = setTimeout(() => this._reset(new Error('브라우저 분석 시간이 초과되었습니다. 다시 시도해 주세요.')), 120000);
      const cleanup = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); };
      this._requests.set(id, { action, resolve, reject, cleanup });
      signal?.addEventListener('abort', abort, { once: true });
      try { worker.postMessage({ id, action, ...payload }, transfer); }
      catch (error) { this._requests.delete(id); cleanup(); reject(error); }
    });
  }

  warmup() {
    if (this._disposed) return Promise.reject(new Error('이 분석기는 종료되었습니다.'));
    if (!this._warmupPromise) {
      const pending = this._request('warmup');
      this._warmupPromise = pending;
      pending.catch(() => { if (this._warmupPromise === pending) this._warmupPromise = null; });
    }
    return this._warmupPromise;
  }

  async analyze(samples, sampleRate, { engine = 'auto', sensitivity = 1, smoothing = .35, signal } = {}) {
    if (this._disposed) throw new Error('이 분석기는 종료되었습니다.');
    if (signal?.aborted) throw abortError();
    const validated = validateInput(samples, sampleRate, { engine, sensitivity, smoothing });
    // Never transfer the caller's microphone/export buffer. Float32 is also the
    // actual model input precision; clipping warnings survive this boundary.
    const copy = Float32Array.from(validated.audio);
    if ([...this._requests.values()].some((request) => request.action === 'analyze')) this.cancel();
    return this._request('analyze', { samples: copy, sampleRate,
      options: { engine, sensitivity, smoothing }, inputWarnings: validated.warnings }, [copy.buffer], signal);
  }

  cancel() { this._reset(abortError()); }
  dispose() { if (!this._disposed) { this._disposed = true; this._reset(abortError('분석기를 종료했습니다.')); } }
}
