import assert from 'node:assert/strict';
import test from 'node:test';
import { mountGame } from './game_screen_harness.mjs';

const state = (overrides = {}) => ({
  match_id: 'match-one', revision: 4, phase_id: 5, phase: 'clue', round: 1, remaining_ms: 45000,
  speaker_id: 1, player_ids: [1, 2, 3, 4], alive: [1, 2, 3, 4], turn_order: [1, 2, 3, 4],
  clues: [], eliminations: [], tallies: [], candidate_ids: [], votes_cast: 0, your_vote: null,
  your_word: '雨伞', can_clue: true, ...overrides,
});
const mount = (os = 'web') => mountGame('Undercover', state(), { os });
const vote = (overrides = {}) => state({
  phase: 'vote', phase_id: 9, revision: 8, speaker_id: null, can_clue: false,
  remaining_ms: 30000, candidate_ids: [2, 3, 4], ...overrides,
});

for (const os of ['web', 'android']) {
  test(`${os}: private word starts hidden and closes in the background`, () => {
    const view = mount(os);
    assert.doesNotMatch(view.render(), /雨伞/);
    assert.match(view.press('undercover-word-toggle'), /雨伞/);
    view.background();
    assert.doesNotMatch(view.render(), /雨伞/);
  });
}

test('clue rejection preserves the draft and late revisions cannot restore an old turn', () => {
  const view = mount();
  view.get('undercover-clue-input').onChangeText('下雨能用');
  view.render();
  const submit = view.get('undercover-clue-submit').onPress;
  submit();
  submit();
  assert.deepEqual(view.calls.map(value => ({ ...value })), [
    { type: 'undercover_clue', match_id: 'match-one', phase_id: 5, text: '下雨能用' },
  ]);
  view.receive('error', { message: '线索有误' });
  assert.equal(view.get('undercover-clue-input').value, '下雨能用');
  assert.equal(view.get('undercover-clue-submit').disabled, false);
  view.receive('undercover_state', { game: state({ revision: 5, phase_id: 6, speaker_id: 2, can_clue: false }) });
  view.receive('undercover_state', { game: state() });
  assert.equal(view.get('undercover-clue-input'), undefined);
});

test('reconnect requires a fresh sync and a departed target can unlock a confirmed vote', () => {
  const view = mount();
  view.receive('undercover_state', { game: vote() });
  view.press('undercover-vote-2');
  const stale = view.get('undercover-vote-confirm').onPress;
  view.disconnect();
  stale();
  view.reconnect();
  view.receive('undercover_state', { game: vote({ revision: 9 }) });
  stale();
  assert.equal(view.calls.length, 0);
  view.receive('sync', { game: vote({ revision: 9, your_vote: 2, votes_cast: 2 }) });
  assert.match(view.render(), /已锁票/);
  assert.equal(view.get('undercover-vote-confirm'), undefined);
  view.receive('undercover_state', { game: vote({ revision: 10, alive: [1, 3, 4], candidate_ids: [3, 4] }) });
  view.press('undercover-vote-3');
  view.press('undercover-vote-confirm');
  assert.deepEqual(view.calls.map(value => ({ ...value })), [
    { type: 'undercover_vote', match_id: 'match-one', phase_id: 9, target_id: 3 },
  ]);
});

test('a stale match cannot replace the current view or result', () => {
  const view = mount();
  view.receive('sync', { game: state({ match_id: 'match-two', revision: 0, phase_id: 1, your_word: '雨衣' }) });
  view.receive('undercover_state', { game: state({ revision: 99 }) });
  view.receive('game_over', {
    game: 'undercover', match_id: 'match-one', winner: 'civilian', forfeited: [],
    summary: { game: 'undercover', round: 2, winner: 'civilian', reason: 'all_undercover_eliminated',
      word_pair: { civilian: '铅笔', undercover: '钢笔' }, roles: [], eliminations: [] },
  });
  const html = view.press('undercover-word-toggle');
  assert.match(html, /雨衣/);
  assert.doesNotMatch(html, /雨伞|铅笔/);
});

test('an eliminated player can review clues and return without forfeiting', () => {
  const view = mount();
  const html = view.receive('undercover_state', { game: vote({
    alive: [2, 3, 4], candidate_ids: [], clues: [{ round: 1, user_id: 1, text: '圆形挡雨' }],
  }) });
  assert.match(html, /你已出局/);
  assert.match(html, /圆形挡雨/);
  assert.equal(view.get('undercover-vote-confirm'), undefined);
  view.press('undercover-back-to-room');
  assert.deepEqual(view.navigationCalls.map(([route, params]) => [route, { ...params }]), [['Room', { code: 'TEST01' }]]);
});

test('a fresh waiting-room sync restores the participant result', () => {
  const view = mountGame('Undercover', null, {
    room: { game: 'undercover', status: 'waiting', players: [{ id: 1, username: 'human' }] },
  });
  const html = view.receive('sync', { undercover_result: {
    game: 'undercover', match_id: 'match-one', winner: 'civilian', reason: 'no_undercover', forfeited: [1],
    summary: { game: 'undercover', round: 2, winner: 'civilian', reason: 'no_undercover',
      word_pair: { civilian: '雨伞', undercover: '雨衣' }, roles: [{ user_id: 1, role: 'undercover' }], eliminations: [],
      tallies: [{ round: 2, runoff: true, votes: [{ user_id: 1, count: 3 }] }] },
  } });
  assert.match(html, /平民胜利/);
  assert.match(html, /雨伞/);
  assert.match(html, /雨衣/);
  assert.match(html, /本局记负/);
  assert.match(html, /第 2 轮重投/);
  assert.match(html, /human（我） 3 票/);
});

for (const membership of ['removed', 'present', 'unavailable']) {
  test(`departing after reconnect failure: ${membership} membership`, async () => {
    const room = { game: 'undercover', status: 'playing', players: [
      { id: 1, username: 'human', connected: true }, { id: 2, username: 'opponent', connected: true },
    ] };
    const view = mountGame('Undercover', state(), {
      room, api: {
        async leaveRoom() { throw new Error(membership === 'removed' ? '你不在房间中' : '连接失败'); },
        async getRoom() {
          if (membership === 'unavailable') throw new Error('连接失败');
          return { ...room, players: membership === 'removed' ? room.players.slice(1) : room.players };
        },
      },
    });
    view.disconnect();
    view.pressText('离开房间');
    view.pressText('确认离开');
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(view.navigationCalls, membership === 'removed' ? [['MainTabs']] : []);
    if (membership !== 'removed') assert.deepEqual(view.notices, [['退出失败', '连接失败']]);
  });
}
