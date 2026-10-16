const button = document.querySelector('#run');
const status = document.querySelector('#status');
const result = document.querySelector('#result');
const summary = document.querySelector('#summary');
let worker;

function addField(list, label, value) {
  const term = document.createElement('dt');
  term.textContent = label;
  const description = document.createElement('dd');
  description.textContent = value;
  list.append(term, description);
}

button.addEventListener('click', () => {
  button.disabled = true;
  summary.hidden = true;
  result.textContent = '정적 자산 로드 및 실제 ONNX 추론 중…';
  status.textContent = 'Worker 시작…';
  if (!worker) {
    worker = new Worker(new URL('./proof.worker.mjs', import.meta.url), { type: 'module' });
    worker.addEventListener('message', ({ data }) => {
      if (data.type === 'progress') {
        status.textContent = data.message;
        return;
      }
      button.disabled = false;
      if (data.type === 'error') {
        status.textContent = '검증 실행 실패';
        result.textContent = JSON.stringify(data, null, 2);
        return;
      }
      const proof = data.result;
      status.textContent = proof.comparison.pass ? '실제 WASM 추론 + 수치 비교 통과' : '추론 완료 · 수치 비교 확인 필요';
      summary.hidden = false;
      summary.dataset.pass = String(proof.comparison.pass);
      const list = document.createElement('dl');
      addField(list, '실제 실행기', `${proof.runtime.package}@${proof.runtime.version} · ${proof.runtime.provider} · ${proof.runtime.threads} thread`);
      addField(list, '모델', `Spotify Basic Pitch · SHA-256 ${proof.model_sha256}`);
      addField(list, '입력', `${proof.input.shape.join(' × ')} float32 · 22,050 Hz · 합성 PCM 데모`);
      addField(list, '모델 준비', `${proof.timing.model_prepare_ms.toFixed(1)} ms`);
      addField(list, '추론 시간', `${proof.timing.inference_ms.toFixed(1)} ms`);
      addField(list, '출력 형태', Object.entries(proof.outputs).map(([name, output]) => `${name}: ${output.shape.join(' × ')}`).join(' / '));
      addField(list, '수치 비교', `${proof.comparison.pass ? '통과' : '확인 필요'} · 동일 입력/모델 해시, 유한값, 형태와 출력 요약 통계 비교`);
      addField(list, '연산 특징', `note peak ${proof.features.note_peak.toFixed(6)} · onset peak ${proof.features.onset_peak.toFixed(6)} · contour peak ${proof.features.contour_peak.toFixed(6)}`);
      summary.replaceChildren(list);
      result.textContent = JSON.stringify(proof, null, 2);
      // Read-only automation evidence, without exposing audio or output arrays.
      window.browserProofResult = proof;
    });
    worker.addEventListener('error', (event) => {
      button.disabled = false;
      status.textContent = 'Worker 초기화 실패';
      result.textContent = event.message || 'Worker import / WASM / CSP 오류를 개발자 도구에서 확인하세요.';
      worker.terminate();
      worker = undefined;
    });
  }
  worker.postMessage({ type: 'run' });
});
