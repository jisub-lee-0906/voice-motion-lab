export const STAGE = Object.freeze({ width: 720, height: 960, fps: 24, duration: 5 });
export const CHARACTERS = Object.freeze({
  bounce: Object.freeze({ id: 'bounce', name: '말랑 바운스', line: '내 텐션, 이 정도.', sound: '통! 통! 통!', bg: '#192d23', accent: '#d2f864', soft: '#466638', caption: 'BOUNCE WITH ME', asset: new URL('../production/assets/bop-bounce.svg', import.meta.url).href }),
  fly: Object.freeze({ id: 'fly', name: '삐슝 비행', line: '오늘도 정신은 비행 중.', sound: '위이이이잉↗', bg: '#30274b', accent: '#d2bcff', soft: '#665489', caption: 'CATCH ME IF YOU CAN', asset: new URL('../production/assets/bop-fly.svg', import.meta.url).href }),
  orbit: Object.freeze({ id: 'orbit', name: '우주 둠칫', line: '아무튼 내 리듬대로.', sound: '둠칫 두둠칫', bg: '#16333b', accent: '#ffbb95', soft: '#477381', caption: 'IN MY OWN ORBIT', asset: new URL('../production/assets/bop-orbit.svg', import.meta.url).href }),
});
const clamp = (v, min = 0, max = 1) => Math.max(min, Math.min(max, Number.isFinite(v) ? v : min));

export function frameAt(time, analysis) {
  const frames = analysis?.frames;
  if (!frames?.length) return { energy: 0, pitch: .5, pitch_confidence: 0, onset: 0, progress: 0 };
  let lo = 0, hi = frames.length - 1;
  while (lo < hi) { const mid = Math.ceil((lo + hi) / 2); if (frames[mid].time <= time) lo = mid; else hi = mid - 1; }
  const a = frames[lo], b = frames[Math.min(lo + 1, frames.length - 1)];
  const mix = b.time > a.time ? clamp((time - a.time) / (b.time - a.time)) : 0;
  const frame = {};
  for (const key of ['energy', 'pitch', 'pitch_confidence', 'onset', 'progress']) frame[key] = clamp(a[key] + (b[key] - a[key]) * mix);
  return frame;
}

/** Bounds reserve the complete rotated and squashed 390 × 260 source artwork. */
export function characterPose(time, analysis, character = 'bounce') {
  const f = frameAt(time, analysis), p = f.progress, e = f.energy;
  const pitch = (f.pitch - .5) * f.pitch_confidence;
  let impact = 0;
  for (const event of analysis?.events || []) {
    const age = time - event.time;
    if (age >= 0 && age < .42) impact += event.strength * Math.sin(age / .42 * Math.PI) * Math.exp(-age * 4);
  }
  impact = clamp(impact);
  let x = 360, y = 462, rotation = 0;
  if (analysis) {
    if (character === 'bounce') { x += Math.sin(p * Math.PI * 4) * 60; y -= Math.abs(Math.sin(p * Math.PI * 5)) * 120 + pitch * 64; rotation = Math.sin(p * Math.PI * 5) * .16; }
    if (character === 'fly') { x += (p - .5) * 132; y -= Math.sin(p * Math.PI) * 100 + pitch * 94; rotation = -.11 - pitch * .22 + Math.sin(p * Math.PI * 4) * .07; }
    if (character === 'orbit') { const angle = p * Math.PI * 2; x += Math.sin(angle) * 65; y += Math.cos(angle) * 68 - pitch * 70; rotation = Math.sin(angle) * .22; }
  } else { y += Math.sin(time * 1.8) * 6; rotation = Math.sin(time * 1.4) * .025; }
  // A stronger hit spreads the body sideways; sustained volume makes it swell.
  const scaleX = 1 + impact * .2 + e * .055, scaleY = 1 - impact * .18 + e * .055;
  const halfX = (390 * scaleX * Math.abs(Math.cos(rotation)) + 260 * scaleY * Math.abs(Math.sin(rotation))) / 2;
  const halfY = (390 * scaleX * Math.abs(Math.sin(rotation)) + 260 * scaleY * Math.abs(Math.cos(rotation))) / 2;
  x = clamp(x, 32 + halfX, STAGE.width - 32 - halfX);
  y = clamp(y, 235 + halfY, 752 - halfY);
  return { x, y, rotation, scaleX, scaleY, impact, energy: e, halfX, halfY };
}

function roundRect(ctx, x, y, w, h, r = 10) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }
function text(ctx, value, x, y, size, color, weight = '700', align = 'center') { ctx.font = `${weight} ${size}px Arial, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif`; ctx.fillStyle = color; ctx.textAlign = align; ctx.fillText(value, x, y); }

/** This one function draws both the interactive preview and every exported frame. */
export function createSceneRenderer({ character = 'bounce', image, analysis = null, synthetic = false }) {
  const chosen = CHARACTERS[character] || CHARACTERS.bounce;
  return (ctx, time = 0) => {
    const pose = characterPose(time, analysis, chosen.id), frame = frameAt(time, analysis);
    ctx.save(); ctx.clearRect(0, 0, STAGE.width, STAGE.height); ctx.fillStyle = chosen.bg; ctx.fillRect(0, 0, STAGE.width, STAGE.height);
    // A quiet grid and offset rings make the motion read like a tiny music poster.
    ctx.strokeStyle = '#ffffff0b'; ctx.lineWidth = 1;
    for (let x = 0; x <= STAGE.width; x += 60) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, STAGE.height); ctx.stroke(); }
    for (let y = 0; y <= STAGE.height; y += 60) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(STAGE.width, y); ctx.stroke(); }
    ctx.save(); ctx.translate(360, 482); ctx.rotate(-.18); ctx.strokeStyle = chosen.soft; ctx.globalAlpha = .48; ctx.lineWidth = 2;
    for (const radius of [210, 264, 318]) { ctx.beginPath(); ctx.ellipse(0, 0, radius, radius * .77, 0, 0, Math.PI * 2); ctx.stroke(); } ctx.restore();
    text(ctx, 'BOP!', 49, 66, 40, chosen.accent, '900', 'left');
    text(ctx, synthetic ? 'SYNTHETIC DEMO' : 'MADE WITH MY VOICE', 670, 55, 13, '#d4dece', '700', 'right');
    text(ctx, chosen.line, 360, 147, 36, '#f5f6de', '800');
    text(ctx, chosen.caption, 360, 181, 12, chosen.accent, '700');
    // Impact rings are driven only by measured attacks, never inferred meaning.
    if (pose.impact > .015) {
      ctx.save(); ctx.strokeStyle = chosen.accent; ctx.globalAlpha = pose.impact * .65; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.ellipse(pose.x, pose.y + 129, 135 + (1 - pose.impact) * 100, 29 + (1 - pose.impact) * 20, 0, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
    }
    ctx.save(); ctx.translate(pose.x, 714); ctx.scale(1, .13); ctx.beginPath(); ctx.arc(0, 0, 120 - (462 - pose.y) * .12, 0, Math.PI * 2); ctx.fillStyle = '#00000035'; ctx.fill(); ctx.restore();
    if (image) { ctx.save(); ctx.translate(pose.x, pose.y); ctx.rotate(pose.rotation); ctx.scale(pose.scaleX, pose.scaleY); ctx.drawImage(image, -195, -130, 390, 260); ctx.restore(); }
    ctx.save(); ctx.translate(360, 791); ctx.rotate(-.035); roundRect(ctx, -178, -28, 356, 57, 10); ctx.fillStyle = chosen.accent; ctx.fill(); text(ctx, analysis ? synthetic ? '합성음도 이 정도.' : '이 소리, 내가 냈음.' : '네 목소리면 충분해.', 0, 10, 24, '#1b2a20', '800'); ctx.restore();
    // Volume bars and the timing rail are included in the actual video.
    const count = 23, startX = 179, gap = 16;
    for (let i = 0; i < count; i++) { const ripple = .4 + .6 * Math.abs(Math.sin(i * .7 + frame.progress * 15)); const h = 4 + frame.energy * ripple * 35; roundRect(ctx, startX + i * gap, 865 - h / 2, 6, h, 3); ctx.fillStyle = chosen.accent; ctx.globalAlpha = .45 + frame.energy * .55; ctx.fill(); }
    ctx.globalAlpha = 1; ctx.fillStyle = '#ffffff20'; roundRect(ctx, 48, 913, 624, 3, 1.5); ctx.fill();
    if (analysis) { ctx.fillStyle = chosen.accent; roundRect(ctx, 48, 913, 624 * clamp(time / STAGE.duration), 3, 1.5); ctx.fill(); }
    text(ctx, '5 SECONDS. ZERO EXPLANATION.', 360, 942, 11, '#c3d2bc', '700'); ctx.restore();
  };
}
