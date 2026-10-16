import test from 'node:test';
import assert from 'node:assert/strict';
import { characterPose, CHARACTERS, STAGE } from '../../web/play/scene.mjs';

function analysis(pitch = 1, energy = 1) {
  return { frames: Array.from({ length: 301 }, (_, i) => ({ time: i / 60, progress: i / 300, energy, pitch, pitch_confidence: 1, onset: 1 })), events: Array.from({ length: 20 }, (_, i) => ({ time: i / 4, strength: 1 })) };
}
test('full rotated and squashed character stays inside the portrait action area at every pitch extreme', () => {
  for (const character of Object.keys(CHARACTERS)) for (const pitch of [0, .5, 1]) {
    const take = analysis(pitch);
    for (let i = 0; i <= 1200; i++) {
      const pose = characterPose(i / 240, take, character);
      assert.ok(pose.x - pose.halfX >= 32 - 1e-8, `${character} left`);
      assert.ok(pose.x + pose.halfX <= STAGE.width - 32 + 1e-8, `${character} right`);
      assert.ok(pose.y - pose.halfY >= 235 - 1e-8, `${character} top`);
      assert.ok(pose.y + pose.halfY <= 752 + 1e-8, `${character} bottom`);
    }
  }
});
test('silent input holds still instead of inventing motion', () => {
  const take = { frames: [{ time: 0, progress: 0, pitch: .5, pitch_confidence: 0, energy: 0, onset: 0 }, { time: 5, progress: 0, pitch: .5, pitch_confidence: 0, energy: 0, onset: 0 }], events: [] };
  for (const character of Object.keys(CHARACTERS)) assert.deepEqual(characterPose(0, take, character), characterPose(4.9, take, character));
});
