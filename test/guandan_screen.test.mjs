import assert from 'node:assert/strict';
import test from 'node:test';
import { mountGame } from './game_screen_harness.mjs';

const hand = Array.from({ length: 27 }, (_, index) => ({
  id: index + 1, rank: ['10', '10', '5', '6'][index % 4], suit: ['S', 'S', 'H', 'D'][index % 4],
}));
const game = (overrides = {}) => ({
  game: 'guandan', match_id: 'match-one', revision: 6, phase: 'playing', deal: 1, players: [1, 2, 3, 4],
  levels: ['2', '2'], level: '5', level_team: null, current_id: 1, hand, remaining: [27, null, 10, null],
  passed: [], finish_order: [], last_play: null, exchange: null, return_choices: [], lead_reason: 'first',
  deal_result: null, ...overrides,
});
const mount = (width = 390, height = 844, initial = game(), options = {}) =>
  mountGame('Guandan', initial, { width, height, ...options });
const pair = { kind: 'pair', rank: '10', cards: hand.slice(0, 2), wild: [] };
const card = id => `guandan-card-${id}`;
const summary = (matchId = 'match-one') => ({
  game: 'guandan', match_id: matchId, summary: {
    match_id: matchId, players: [1, 2, 3, 4], levels: ['A', '5'], deal: 8, winner: 0, reason: 'clear_ace',
    deal_result: null,
  },
});

for (const [width, height, rows] of [
  [320, 568, [9, 9, 9]], [390, 844, [14, 13]], [667, 375, [14, 13]], [1440, 900, [27]],
]) {
  test(`${width}×${height} fits ${rows.join('/')} hand slots with visible actions`, () => {
    const ui = mount(width, height);
    assert.equal(ui.count('guandan-hand-row-'), rows.length);
    assert.deepEqual(rows.map((_, index) => ui.get(`guandan-hand-row-${index + 1}`).children.length), rows);
    assert.ok(ui.get('guandan-play'));
    assert.equal(ui.count('guandan-seat-'), 4);
  });
}

test('identical faces have independent hit targets and the picker shares the same selected IDs', () => {
  const ui = mount(320, 568);
  const original = ui.get(card(1));
  assert.equal(original.accessibilityLabel, ui.get(card(2)).accessibilityLabel);
  ui.press(card(1));
  assert.deepEqual(ui.get(card(1)).style, original.style);
  assert.equal(ui.get(card(1)).accessibilityState.selected, true);
  assert.equal(ui.get(card(2)).accessibilityState.selected, false);
  ui.press('guandan-expand');
  assert.equal(ui.get('guandan-large-card-1').accessibilityState.selected, true);
  ui.press('guandan-large-card-2');
  assert.equal(ui.get(card(2)).accessibilityState.selected, true);
  assert.equal(ui.calls.length, 0);
  ui.press('guandan-picker-play');
  assert.deepEqual([...ui.calls[0].cards], [1, 2]);
  assert.equal(ui.calls[0].deal, 1);
  assert.equal(ui.calls[0].revision, 6);
  assert.ok(ui.get(card(1)));
  assert.match(ui.receive('error', { game: 'guandan', match_id: 'match-one', revision: 6, deal: 1, message: '这手牌不能出' }), /这手牌不能出/);
  assert.equal(ui.get(card(1)).accessibilityState.selected, true);
});

test('hand and public cards use vector pips with readable card labels', () => {
  const ui = mount(390, 844, game({ last_play: { uid: 2, ...pair } }));
  assert.match(ui.render(), /<svg/);
  assert.doesNotMatch(ui.render(), /[♠♥♣♦]/);
  assert.equal(ui.get(card(1)).accessibilityLabel, '黑桃10');
});

test('the recap at Ace shows the same level for the next deal', () => {
  const recap = { deal: 8, winner: 0, finish_order: [1, 2, 4, 3], levels: ['A', '5'], advance: 1, level: 'A' };
  const ui = mount(390, 844, game({ phase: 'between', deal: 8, level: 'A', level_team: 0,
    levels: recap.levels, deal_result: recap, current_id: null }));
  assert.match(ui.render(), /下一副 · 我方打 A/);
  assert.doesNotMatch(ui.render(), /我方升 1 级/);
});

test('an active bot is labelled as thinking while the human buttons wait', () => {
  const ui = mount(390, 844, game({ current_id: -7, players: [1, -7, 3, 4] }), {
    room: { game: 'guandan', status: 'playing', host_id: 1, players: [
      { id: 1, username: 'human', connected: true },
      { id: -7, username: 'bot', nickname: '人机·棋手', connected: true, is_bot: true },
    ] },
  });
  assert.match(ui.render(), /人机·棋手 思考中/);
  assert.equal(ui.get('guandan-play').disabled, true);
});

test('a preview is tied to both selection and turn; only an authoritative broadcast removes cards', () => {
  const ui = mount();
  ui.press(card(3));
  const first = ui.calls.at(-1);
  assert.equal(first.action, 'preview');
  ui.press(card(1));
  const second = ui.calls.at(-1);
  assert.notEqual(first.request_id, second.request_id);
  const wildPair = { ...pair, cards: [hand[0], hand[2]], wild: [{ id: 3, rank: '10', suit: 'S' }] };
  assert.doesNotMatch(ui.receive('guandan_preview', { ...first, combo: wildPair }), /红桃5 当黑桃10/);
  assert.match(ui.receive('guandan_preview', { ...second, combo: wildPair }), /红桃5 当黑桃10/);
  ui.press('guandan-play');
  assert.equal(ui.calls.at(-1).action, 'play');
  assert.ok(ui.get(card(3)));
  ui.receive('guandan_state', { game: game({ revision: 7, current_id: 2, hand: hand.filter(entry => entry.id !== 1 && entry.id !== 3) }) });
  assert.equal(ui.get(card(3)), undefined);
  assert.doesNotMatch(ui.receive('guandan_preview', { ...second, combo: wildPair }), /红桃5 当黑桃10/);
});

test('the matching hint only selects cards and a manual selection supersedes an unanswered hint', () => {
  const ui = mount();
  ui.press('guandan-hint');
  const first = ui.calls.at(-1);
  ui.press(card(4));
  ui.receive('guandan_hint', { ...first, combo: pair });
  assert.equal(ui.get(card(4)).accessibilityState.selected, true);
  assert.equal(ui.get(card(1)).accessibilityState.selected, false);
  ui.press('guandan-hint');
  const second = ui.calls.at(-1);
  ui.receive('guandan_hint', { ...second, combo: pair });
  assert.equal(ui.get(card(1)).accessibilityState.selected, true);
  assert.equal(ui.get(card(2)).accessibilityState.selected, true);
  assert.equal(ui.get(card(4)).accessibilityState.selected, false);
  assert.deepEqual(ui.calls.map(call => call.action), ['hint', 'hint']);
});

test('reconnect retains owned selection and ignores queued updates and results from an older match', () => {
  const ui = mount();
  ui.press(card(1));
  ui.press(card(2));
  ui.disconnect();
  ui.reconnect();
  ui.receive('guandan_state', { game: game({ revision: 7, hand: [] }) });
  assert.ok(ui.get(card(1)));
  ui.receive('sync', { game: game({ revision: 7, hand: hand.slice(1) }) });
  assert.equal(ui.get(card(1)), undefined);
  assert.equal(ui.get(card(2)).accessibilityState.selected, true);
  ui.receive('guandan_state', { game: game() });
  assert.equal(ui.get(card(1)), undefined);
  ui.receive('game_start', { game: game({ match_id: 'match-two', revision: 0 }) });
  ui.receive('game_over', summary());
  ui.receive('sync', { room: { status: 'waiting', host_id: 1, players: [] }, guandan_result: summary() });
  assert.ok(ui.get(card(1)));
  assert.equal(ui.get('guandan-result'), undefined);
});

test('only legal return cards submit and a between-deal result displays before the host continues', () => {
  const deal = { deal: 1, winner: 0, finish_order: [1, 3], levels: ['5', '2'], advance: 3, level: '2' };
  const exchange = { antitribute: false, lead_id: 2, pairs: [
    { from_id: 2, to_id: 1, tribute: hand[0], returned: null, submitted: false },
    { from_id: 4, to_id: 3, tribute: hand[1], returned: null, submitted: true },
  ] };
  const ui = mount(390, 844, game({ phase: 'returning', deal: 2, current_id: null, return_choices: [4], exchange }));
  ui.press(card(1));
  assert.equal(ui.get(card(1)).accessibilityState.selected, false);
  ui.press(card(4));
  ui.press('guandan-return');
  assert.equal(ui.calls.at(-1).card, 4);
  assert.equal(ui.calls.at(-1).action, 'return');
  assert.match(ui.receive('guandan_state', { game: game({ phase: 'between', deal: 2, revision: 7, current_id: null, deal_result: deal }) }), /双下/);
  assert.ok(ui.get('guandan-deal-result'));
  ui.press('guandan-next');
  assert.equal(ui.calls.at(-1).action, 'next');
});

test('terminal sync restores the result even when opening the game route without a live snapshot', () => {
  const ui = mount(390, 844, null);
  ui.receive('sync', { room: { status: 'waiting', host_id: 1, players: [] }, guandan_result: summary() });
  assert.ok(ui.get('guandan-result'));
  assert.match(ui.render(), /过 A/);
  ui.press('guandan-back-to-room');
  assert.deepEqual(ui.navigationCalls.map(([name, options]) => [name, options?.code]), [['Room', 'TEST01']]);
});
