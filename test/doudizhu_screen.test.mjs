import assert from 'node:assert/strict';
import test from 'node:test';
import { mountGame } from './game_screen_harness.mjs';
const hand = ['BJ', 'SJ', '2S', '2H', 'AS', 'AH', 'KS', 'KH', 'QS', 'QH', 'JS', 'JH', '10S', '10H', '9S', '9H', '8S', '8H', '7S', '7H'];
const game = (overrides = {}) => ({
  game: 'doudizhu', match_id: 'match-one', revision: 6, phase: 'playing', seat_uids: [1, 2, 3],
  current_id: 1, landlord_id: 1, hand, remaining: [20, 17, 17], bottom: ['SJ','QH','JS'],
  bid: 3, multiplier: 1, available_bids: [], can_pass: false, actions: [null,null,null],
  last_play: null, rules: { all_pass: 'redeal', airplane_pair_as_singles: true, four_pair_as_singles: true },
  ...overrides,
});
const mount = width => mountGame('Doudizhu', game(), { width });

test('a 320-wide hand splits into two fixed-slot rows and cross-row cards only send on play', () => {
  const ui = mount(320);
  assert.ok(ui.get('doudizhu-hand-row-2'));
  const selector = card => 'doudizhu-card-' + card;
  const original = ui.get(selector('QH'));
  ui.press(selector('QH'));
  ui.press(selector('JS'));
  assert.deepEqual(ui.get(selector('QH')).style, original.style);
  assert.equal(ui.get(selector('QH')).accessibilityState.selected, true);
  assert.equal(ui.get(selector('JS')).accessibilityState.selected, true);
  assert.equal(ui.calls.length, 0);
  ui.press('doudizhu-play');
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].action, 'play');
  assert.equal(ui.calls[0].revision, 6);
  assert.deepEqual([...ui.calls[0].cards], ['QH', 'JS']);
});
test('desktop uses one row and its picker shares selected IDs without sending', () => {
  const ui = mount(1440);
  assert.ok(ui.get('doudizhu-hand-row-1'));
  assert.equal(ui.get('doudizhu-hand-row-2'), undefined);
  ui.press('doudizhu-card-BJ');
  ui.press('doudizhu-expand');
  assert.equal(ui.get('doudizhu-large-card-BJ').accessibilityState.selected, true);
  ui.press('doudizhu-large-card-SJ');
  assert.equal(ui.get('doudizhu-card-SJ').accessibilityState.selected, true);
  assert.equal(ui.calls.length, 0);
});
test('suit pips are drawn as card art and each card keeps a readable button label', () => {
  const ui = mount(390);
  const html = ui.render();
  assert.match(html, /<svg/);
  assert.doesNotMatch(html, />[♠♥♣♦]</);
  assert.equal(ui.get('doudizhu-card-2H').accessibilityLabel, '红桃2');
});
test('the table tells the player when a bot is thinking', () => {
  const ui = mountGame('Doudizhu', game({ current_id: -2, seat_uids: [1, -2, 3] }), { width: 390 });
  const html = ui.render();
  assert.match(html, /人机 思考中…/);
  assert.equal(ui.get('doudizhu-play').disabled, true);
});

test('the table shows the player’s own hand count and keeps past plays after a new lead', () => {
  const ui = mountGame('Doudizhu', game({ play_history: [
    { player_id: 3, play: { player_id: 3, cards: ['3C'], kind: 'single', label: '单张' } },
    { player_id: 2, play: null },
  ] }), { width: 390 });
  assert.match(ui.render(), /剩 20 张/);
  assert.ok(ui.get('doudizhu-history-feed'));
  ui.press('doudizhu-history-open');
  assert.ok(ui.get('doudizhu-history-modal'));
  assert.match(ui.render(), /梅花3/);
});
test('only the matching pending hint selects cards and rejection retains the selected pair', () => {
  const ui = mount(390);
  ui.press('doudizhu-hint');
  ui.receive('doudizhu_hint', {match_id: 'match-one', revision: 5, cards: ['2S']});
  assert.equal(ui.get('doudizhu-card-2S').accessibilityState.selected, false);
  ui.receive('doudizhu_hint', {match_id: 'match-one', revision: 6, cards: ['2S','2H']});
  assert.equal(ui.get('doudizhu-card-2S').accessibilityState.selected, true);
  assert.equal(ui.calls.length, 1);
  ui.press('doudizhu-play');
  const html = ui.receive('error', {message: '这手牌不能压过上一手'});
  assert.match(html, /这手牌不能压过上一手/);
  assert.equal(ui.get('doudizhu-card-2S').accessibilityState.selected, true);
  assert.equal(ui.get('doudizhu-play').disabled, false);
});
test('the reconnect snapshot preserves owned selection and drops cards already accepted by the server', () => {
  const ui = mount(390);
  ui.press('doudizhu-card-BJ');
  ui.press('doudizhu-card-SJ');
  ui.disconnect();
  assert.ok(ui.get('doudizhu-play').disabled);
  ui.reconnect();
  ui.receive('doudizhu_state', {game: game({revision: 7})});
  assert.ok(ui.get('doudizhu-play').disabled);
  ui.receive('sync', {game: game({revision: 7, hand: hand.slice(1)})});
  assert.equal(ui.get('doudizhu-card-BJ'), undefined);
  assert.equal(ui.get('doudizhu-card-SJ').accessibilityState.selected, true);
  ui.receive('doudizhu_state', {game: game({revision: 6})});
  assert.equal(ui.get('doudizhu-card-BJ'), undefined);
});
