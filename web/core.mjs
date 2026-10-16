export const STAGE = Object.freeze({ width: 960, height: 540, assetSize: 196 });
export const DEFAULT_SETTINGS = Object.freeze({ preset: 'fly', intensity: 1, smoothing: .35, background: '#10151f', transparent: false });
export const clamp = (value, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, Number.isFinite(Number(value)) ? Number(value) : lo));
const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]));

export function normalizeSettings(settings = {}) {
  return {
    preset: ['fly', 'bounce', 'orbit'].includes(settings.preset) ? settings.preset : 'fly',
    intensity: clamp(settings.intensity ?? 1, 0, 1.8),
    smoothing: clamp(settings.smoothing ?? .35),
    background: /^#[a-f\d]{6}$/i.test(settings.background || '') ? settings.background : '#10151f',
    transparent: settings.transparent === true,
  };
}

export function validateAnalysis(analysis) {
  if (!analysis || analysis.version !== 1 || !['signal', 'basic_pitch'].includes(analysis.engine)) throw new Error('지원하지 않는 분석 형식입니다.');
  const duration = Number(analysis.duration);
  if (!Number.isInteger(analysis.sample_rate) || analysis.sample_rate < 8000 || analysis.sample_rate > 96000) throw new Error('분석 샘플레이트가 올바르지 않습니다.');
  if (!Number.isFinite(duration) || duration <= 0 || duration > 15 || !Array.isArray(analysis.frames) || analysis.frames.length > 2000) throw new Error('분석 길이 또는 프레임이 올바르지 않습니다.');
  let previous = -1, previousProgress = 0;
  for (const frame of analysis.frames) {
    if (!Number.isFinite(frame.time) || frame.time < previous || frame.time < 0 || frame.time > duration + .05) throw new Error('분석 시간 순서가 올바르지 않습니다.');
    previous = frame.time;
    for (const key of ['energy', 'pitch', 'pitch_confidence', 'onset', 'progress']) if (!Number.isFinite(frame[key]) || frame[key] < 0 || frame[key] > 1) throw new Error('분석 수치가 올바르지 않습니다.');
    if (frame.progress < previousProgress - 1e-8) throw new Error('경로 진행 값이 뒤로 이동할 수 없습니다.');
    previousProgress = frame.progress;
  }
  if (!Array.isArray(analysis.events) || analysis.events.length > 1000 || analysis.events.some(e => e.kind !== 'impact' || !Number.isFinite(e.time) || e.time < 0 || e.time > duration || !Number.isFinite(e.strength) || e.strength < 0 || e.strength > 1)) throw new Error('타격 이벤트가 올바르지 않습니다.');
  return analysis;
}

function frameAt(time, frames) {
  if (!frames?.length) return { progress: 0, pitch: .5, pitch_confidence: 0, energy: 0 };
  let lo = 0, hi = frames.length - 1;
  while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (frames[mid].time <= time) lo = mid; else hi = mid - 1; }
  const a = frames[lo], b = frames[Math.min(lo + 1, frames.length - 1)];
  const u = b.time > a.time ? clamp((time - a.time) / (b.time - a.time)) : 0;
  let pitchFrame = a;
  if (finite(pitchFrame.pitch_confidence) < .1) { for (let i = lo; i >= 0; --i) { if (finite(frames[i].pitch_confidence) >= .1) { pitchFrame = frames[i]; break; } } }
  return {
    progress: clamp(finite(a.progress) + (finite(b.progress) - finite(a.progress)) * u),
    pitch: clamp(pitchFrame.pitch ?? .5), pitch_confidence: clamp(pitchFrame.pitch_confidence),
    energy: clamp(finite(a.energy) + (finite(b.energy) - finite(a.energy)) * u),
  };
}

// Reserve the worst rotated/scaled footprint of a 196px square for the ENTIRE
// take, not the current frame. This prevents impact-dependent path breathing.
// For a fixed angle, each extent is linear in impact, so its maximum occurs at
// either zero or maximum impact. Each angle maximum has an analytic solution.
function safeTravel(intensity, rotationDegrees) {
  const half = STAGE.assetSize / 2, limit = rotationDegrees * Math.PI / 180;
  let halfX = 0, halfY = 0;
  for (const impact of [0, intensity]) {
    const sx = 1 + impact * .24, sy = 1 - impact * .18;
    const angleX = Math.min(limit, Math.atan2(sy, sx)), angleY = Math.min(limit, Math.atan2(sx, sy));
    halfX = Math.max(halfX, half * (sx * Math.cos(angleX) + sy * Math.sin(angleX)));
    halfY = Math.max(halfY, half * (sx * Math.sin(angleY) + sy * Math.cos(angleY)));
  }
  const inset = 12;
  return { x: STAGE.width / 2 - halfX - inset, y: STAGE.height / 2 - halfY - inset };
}

// Keep the original <=100% motion. Above that point, smoothly spend the
// remaining travel budget; never clamp a moving object against a stage edge.
function fittedIntensity(intensity, maximum) {
  if (intensity <= 1) return intensity;
  const headroom = Math.max(0, maximum - 1);
  return 1 + (headroom > 0 ? headroom * -Math.expm1(-(intensity - 1) / headroom) : 0);
}

/** The single transform evaluator used by live canvas, SVG and recorded video. */
export function motionAt(time, analysis, settings = DEFAULT_SETTINGS) {
  const s = normalizeSettings(settings), t = clamp(time, 0, Math.max(0, finite(analysis?.duration)));
  const f = frameAt(t, analysis?.frames), p = f.progress, expression = (f.pitch - .5) * f.pitch_confidence;
  const i = s.intensity;
  const rotationLimit = { fly: 7, bounce: 8, orbit: 13 }[s.preset] * i;
  const travel = safeTravel(i, rotationLimit);
  let x = 480, y = 270, rotation = 0;
  if (s.preset === 'fly') {
    const ix = fittedIntensity(i, travel.x / 280);
    x += (p - .5) * 560 * ix; y -= expression * 80 * i; rotation = expression * 14 * i;
  }
  if (s.preset === 'bounce') {
    const ix = fittedIntensity(i, travel.x / 260);
    // The bounce baseline is y=330. Reserve both the 138px jump and the
    // 17.5px pitch excursion; the lower extreme remains safely above y=442.
    const iy = fittedIntensity(i, (travel.y + 60) / 155.5);
    x += (p - .5) * 520 * ix; y = 330 - Math.abs(Math.sin(p * Math.PI * 3)) * 138 * iy - expression * 35 * iy; rotation = Math.sin(p * Math.PI * 3) * 8 * i;
  }
  if (s.preset === 'orbit') {
    const angle = -Math.PI / 2 + p * Math.PI * 2;
    const ix = fittedIntensity(i, travel.x / 180), iy = fittedIntensity(i, travel.y / 128);
    x += Math.cos(angle) * 180 * ix; y += Math.sin(angle) * 116 * iy - expression * 24 * iy; rotation = Math.sin(angle) * 13 * i;
  }
  let impact = 0;
  const settle = .16 + s.smoothing * .2;
  for (const event of analysis?.events || []) { const dt = t - finite(event.time); if (dt >= 0 && dt < settle) impact += clamp(event.strength) * Math.sin(Math.PI * dt / settle) * Math.exp(-dt / (settle * .6)); }
  impact = clamp(impact) * i;
  return { x, y, scaleX: 1 + impact * .24, scaleY: 1 - impact * .18, rotation, opacity: 1 };
}

export function sampleKeyframes(analysis, settings, fps = 60) {
  const duration = clamp(analysis?.duration ?? 5, .001, 15), count = Math.ceil(duration * clamp(fps, 1, 120));
  return Array.from({ length: count + 1 }, (_, index) => { const time = duration * index / count; return { time, ...motionAt(time, analysis, settings) }; });
}

export function validateSvgSource(text) {
  if (typeof text !== 'string' || text.length > 1_000_000) throw new Error('SVG는 1MB 이하의 텍스트 파일이어야 합니다.');
  if (/<!DOCTYPE|<!ENTITY|<\?xml-stylesheet|(?:javascript|vbscript)\s*:|\bexpression\s*\(/i.test(text)) throw new Error('실행 코드·외부 문서가 있는 SVG는 지원하지 않습니다.');
  if (/<\s*(?:\w+:)?(?:script|foreignObject|iframe|object|embed|image|style|animate|animateTransform|animateMotion|set|audio|video)\b|\son[a-z]+\s*=/i.test(text)) throw new Error('실행 요소나 내장 외부 콘텐츠는 SVG에 사용할 수 없습니다.');
  if (/\b(?:xlink:)?href\s*=\s*['"]\s*(?!#)/i.test(text)) throw new Error('외부 파일 참조는 SVG에 사용할 수 없습니다.');
  for (const match of text.matchAll(/url\s*\(([^)]*)\)/gi)) if (!/^\s*(['"]?)#[\w:.-]+\1\s*$/.test(match[1])) throw new Error('외부 SVG 리소스는 허용하지 않습니다.');
  return text;
}

const SVG_STYLE_PROPS = new Set('fill fill-rule fill-opacity stroke stroke-width stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset stroke-opacity opacity color clip-path mask filter font-family font-size font-style font-weight letter-spacing word-spacing text-anchor dominant-baseline paint-order'.split(' '));

/** Validate values AFTER XML entity decoding, including presentation attributes. */
export function validateSvgAttributeValue(name, value) {
  name = name.toLowerCase();
  if (typeof value !== 'string' || /[\\\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]|\/\*/.test(value)) throw new Error('SVG 속성의 CSS 이스케이프·제어 문자·주석은 지원하지 않습니다.');
  if (/(?:javascript|vbscript)\s*:|expression\s*\(/i.test(value)) throw new Error('실행 가능한 SVG 속성은 허용하지 않습니다.');
  if ((name === 'href' || name === 'xlink:href') && !/^#[\w:.-]+$/.test(value)) throw new Error('SVG 외부 파일 참조는 허용하지 않습니다.');
  if (/url\s*\(/i.test(value) && !/^url\(\s*(['"]?)#[\w:.-]+\1\s*\)$/i.test(value) && name !== 'style') throw new Error('외부 SVG 리소스는 허용하지 않습니다.');
  if (name === 'style') for (const declaration of value.split(';').filter(x => x.trim())) {
    const colon = declaration.indexOf(':'), property = declaration.slice(0, colon).trim().toLowerCase(), v = declaration.slice(colon + 1).trim();
    if (colon < 0 || !SVG_STYLE_PROPS.has(property) || /[@<>]|expression|javascript|vbscript/i.test(v) || /url\s*\(/i.test(v) && !/^url\(\s*(['"]?)#[\w:.-]+\1\s*\)$/i.test(v)) throw new Error('안전한 인라인 SVG 스타일만 지원합니다.');
  }
  return value;
}

/** Conservative XML allowlist. No embedded images/fonts/CSS sheets or external resources. */
export function sanitizeSvg(text) {
  validateSvgSource(text);
  if (typeof DOMParser === 'undefined') throw new Error('SVG 검증에는 브라우저 XML 파서가 필요합니다.');
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const root = doc.documentElement;
  if (doc.querySelector('parsererror') || root.localName !== 'svg' || root.namespaceURI !== 'http://www.w3.org/2000/svg') throw new Error('올바른 SVG 문서를 선택해 주세요.');
  const tags = new Set('svg g defs symbol use path rect circle ellipse line polyline polygon text tspan title desc linearGradient radialGradient stop clipPath mask pattern marker filter feBlend feColorMatrix feComponentTransfer feComposite feConvolveMatrix feDiffuseLighting feDisplacementMap feDistantLight feDropShadow feFlood feFuncA feFuncB feFuncG feFuncR feGaussianBlur feMerge feMergeNode feMorphology feOffset fePointLight feSpecularLighting feSpotLight feTile feTurbulence'.split(' '));
  const attrs = new Set('id xmlns xmlns:xlink version viewbox width height x y x1 x2 y1 y2 cx cy r rx ry d points transform opacity fill fill-rule fill-opacity stroke stroke-width stroke-linecap stroke-linejoin stroke-miterlimit stroke-dasharray stroke-dashoffset stroke-opacity vector-effect preserveaspectratio gradientunits gradienttransform spreadmethod offset stop-color stop-opacity clip-path clippathunits mask maskunits maskcontentunits patternunits patterncontentunits patterntransform marker-start marker-mid marker-end markerunits markerwidth markerheight refx refy orient filter filterunits primitiveunits color color-interpolation color-interpolation-filters in in2 result type values mode operator k1 k2 k3 k4 order kernelmatrix divisor bias targetx targety edgemode kernelunitlength preservealpha stddeviation dx dy scale xchannelselector ychannelselector surfaceScale diffuseconstant specularconstant specularexponent limitingconeangle azimuth elevation pointsatx pointsaty pointsatz z numoctaves seed stitchtiles basefrequency flood-color flood-opacity slope intercept amplitude exponent tablevalues font-family font-size font-style font-weight letter-spacing word-spacing text-anchor dominant-baseline alignment-baseline textlength lengthadjust rotate dx dy href xlink:href style'.toLowerCase().split(' '));
  let elements = 0;
  for (const el of [root, ...root.querySelectorAll('*')]) {
    if (++elements > 5000 || !tags.has(el.localName) || el.namespaceURI !== root.namespaceURI) throw new Error('실행 요소·외부 콘텐츠·과도하게 복잡한 SVG는 지원하지 않습니다.');
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase(), value = attr.value;
      if (!attrs.has(name) || name.startsWith('on')) throw new Error(`지원하지 않는 SVG 속성: ${attr.name}`);
      if (name === 'xmlns' && value !== root.namespaceURI || name === 'xmlns:xlink' && value !== 'http://www.w3.org/1999/xlink') throw new Error('외부 XML 네임스페이스는 지원하지 않습니다.');
      validateSvgAttributeValue(name, value);
    }
  }
  const width = parseFloat(root.getAttribute('width')), height = parseFloat(root.getAttribute('height'));
  if (!root.hasAttribute('viewBox')) root.setAttribute('viewBox', `0 0 ${width > 0 && width <= 10000 ? width : 400} ${height > 0 && height <= 10000 ? height : 400}`);
  const vb = root.getAttribute('viewBox').trim().split(/[\s,]+/).map(Number);
  if (vb.length !== 4 || vb.some(n => !Number.isFinite(n)) || vb[2] <= 0 || vb[3] <= 0 || vb[2] > 100000 || vb[3] > 100000) throw new Error('SVG 캔버스 크기가 올바르지 않습니다.');
  root.setAttribute('width', '100%'); root.setAttribute('height', '100%');
  return new XMLSerializer().serializeToString(root);
}

export function assetBox(svg) {
  const match = svg.match(/\bviewBox\s*=\s*["']([^"']+)["']/i), box = match?.[1].trim().split(/[\s,]+/).map(Number);
  const ratio = box?.length === 4 && box[2] > 0 && box[3] > 0 ? box[2] / box[3] : 1;
  return ratio >= 1 ? { width: STAGE.assetSize, height: STAGE.assetSize / ratio } : { width: STAGE.assetSize * ratio, height: STAGE.assetSize };
}

export function animatedSvg(svg, analysis, settings = DEFAULT_SETTINGS, name = 'Voice Motion') {
  if (typeof DOMParser !== 'undefined') svg = sanitizeSvg(svg); else validateSvgSource(svg);
  validateAnalysis(analysis);
  const s = normalizeSettings(settings), frames = sampleKeyframes(analysis, s), box = assetBox(svg), duration = analysis.duration;
  const n = v => finite(v).toFixed(4), times = frames.map(f => n(f.time / duration)).join(';');
  const anim = (type, values) => `<animateTransform attributeName="transform" type="${type}" dur="${n(duration)}s" values="${values.join(';')}" keyTimes="${times}" calcMode="linear" repeatCount="indefinite"/>`;
  const bg = s.transparent ? '' : `<rect width="960" height="540" fill="${s.background}"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540"><title>${esc(name)}</title><desc>Voice Motion Lab · ${esc(analysis.engine)} · sampled transforms at 60 fps · original audio is not embedded</desc>${bg}<g>${anim('translate', frames.map(f => `${n(f.x)} ${n(f.y)}`))}<g>${anim('rotate', frames.map(f => n(f.rotation)))}<g>${anim('scale', frames.map(f => `${n(f.scaleX)} ${n(f.scaleY)}`))}<svg x="${n(-box.width / 2)}" y="${n(-box.height / 2)}" width="${n(box.width)}" height="${n(box.height)}">${svg}</svg></g></g></g></svg>`;
}

export const createAnimatedSvg = animatedSvg;

export function downsampleMono(channels, sourceRate, targetRate = 22050, maxSeconds = 15) {
  if (!channels?.length || sourceRate < 8000 || sourceRate > 192000) throw new Error('지원하지 않는 오디오입니다.');
  const sourceLength = Math.min(channels[0].length, Math.floor(sourceRate * maxSeconds));
  const length = Math.floor(sourceLength * targetRate / sourceRate), output = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    const start = i * sourceRate / targetRate, end = Math.min(sourceLength, (i + 1) * sourceRate / targetRate);
    let total = 0, weight = 0;
    for (let j = Math.floor(start); j < Math.ceil(end); j++) { const w = Math.max(0, Math.min(end, j + 1) - Math.max(start, j)); for (const channel of channels) total += finite(channel[j]) * w / channels.length; weight += w; }
    output[i] = clamp(weight ? total / weight : 0, -1, 1);
  }
  return output;
}

export function validateProject(project) {
  if (project?.format !== 'voice-motion-project' || project.version !== 1 || typeof project.asset?.svg !== 'string') throw new Error('Voice Motion Lab 프로젝트 파일이 아닙니다.');
  const rate = Number(project.audio?.sample_rate), samples = project.audio?.samples;
  if (!Number.isInteger(rate) || rate < 8000 || rate > 96000 || !Array.isArray(samples) || samples.length < 1 || samples.length > rate * 15 || samples.some(v => !Number.isFinite(v) || Math.abs(v) > 1)) throw new Error('프로젝트 오디오가 올바르지 않습니다.');
  validateSvgSource(project.asset.svg); validateAnalysis(project.analysis);
  if (Math.abs(project.analysis.duration - samples.length / rate) > .1) throw new Error('프로젝트 오디오와 분석의 길이가 다릅니다.');
  return { ...project, settings: normalizeSettings(project.settings) };
}
