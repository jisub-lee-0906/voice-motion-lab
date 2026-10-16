import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserAnalyzer } from '../../web/play/analysis-client.mjs';

class FakeWorker extends EventTarget {
  messages = [];
  terminated = false;
  postMessage(message, transfer) { this.messages.push(structuredClone(message, { transfer })); }
  terminate() { this.terminated = true; }
  emit(data) { this.dispatchEvent(new MessageEvent('message', { data })); }
  fail() { const event = new Event('error'); event.message = 'test worker crash'; this.dispatchEvent(event); }
}

test('copies caller audio before transfer and returns the exact actual-engine result', async () => {
  const worker = new FakeWorker();
  const analyzer = new BrowserAnalyzer({ workerFactory: () => worker });
  const original = new Float32Array([0, .2, -.4]);
  const pending = analyzer.analyze(original, 22050);
  assert.equal(original.byteLength, 12);
  assert.deepEqual([...original], [0, Math.fround(.2), Math.fround(-.4)]);
  assert.equal(worker.messages[0].samples.byteLength, 12);
  const result = { engine: 'signal', frames: [], warnings: ['explicit fallback'] };
  worker.emit({ id: worker.messages[0].id, type: 'result', result });
  assert.deepEqual(await pending, result);
  analyzer.dispose();
});

test('warmup is deduplicated, and progress cannot prevent completion', async () => {
  const worker = new FakeWorker();
  const analyzer = new BrowserAnalyzer({ workerFactory: () => worker, onProgress: () => { throw new Error('UI callback'); } });
  const first = analyzer.warmup(), second = analyzer.warmup();
  assert.equal(first, second);
  assert.equal(worker.messages.length, 1);
  worker.emit({ id: worker.messages[0].id, type: 'progress', message: 'loading' });
  worker.emit({ id: worker.messages[0].id, type: 'result', result: { available: false, engine: 'signal', warning: 'AI 모델 분석 결과가 아닙니다.' } });
  assert.equal((await first).engine, 'signal');
  analyzer.dispose();
});

test('cancel terminates CPU work, rejects stale promises and permits a fresh Worker', async () => {
  const workers = [];
  const analyzer = new BrowserAnalyzer({ workerFactory: () => { const worker = new FakeWorker(); workers.push(worker); return worker; } });
  const first = analyzer.analyze(new Float32Array(100), 16000);
  const rejected = assert.rejects(first, { name: 'AbortError' });
  analyzer.cancel();
  await rejected;
  assert.equal(workers[0].terminated, true);
  const fresh = analyzer.analyze(new Float32Array(100), 16000);
  workers[0].emit({ id: workers[0].messages[0].id, type: 'result', result: { stale: true } });
  const expected = { engine: 'signal', fresh: true };
  workers[1].emit({ id: workers[1].messages[0].id, type: 'result', result: expected });
  assert.deepEqual(await fresh, expected);
  analyzer.dispose();
});

test('AbortSignal, worker crash, invalid input and disposal never leave work pending', async () => {
  const workers = [];
  const analyzer = new BrowserAnalyzer({ workerFactory: () => { const worker = new FakeWorker(); workers.push(worker); return worker; } });
  await assert.rejects(analyzer.analyze([NaN], 16000));
  assert.equal(workers.length, 0);
  const controller = new AbortController();
  const pending = analyzer.analyze([0], 16000, { signal: controller.signal });
  const aborted = assert.rejects(pending, { name: 'AbortError' });
  controller.abort();
  await aborted;
  const warm = analyzer.warmup();
  const crashed = assert.rejects(warm, /worker crash/);
  workers.at(-1).fail();
  await crashed;
  analyzer.dispose();
  await assert.rejects(analyzer.warmup(), /종료/);
  await assert.rejects(analyzer.analyze([0], 16000), /종료/);
});
