import assert from 'node:assert/strict';
import test from 'node:test';
import { mountGame } from './game_screen_harness.mjs';

const state = (extra = {}) => ({
  game: 'turtle_soup', match_id: 'first', revision: 0, phase: 'playing', title: '公开题', surface: '测试汤面',
  player_ids: [1, 2], active: [1, 2], requests: [], busy: false, ai_enabled: true, can_submit: true,
  can_reveal: true, request_limit: 80, question_max_length: 200, guess_max_length: 500, ...extra,
});
const mount = (os = 'web') => mountGame('TurtleSoup', state(), { game: 'turtle_soup', os });
const type = (view, value) => { view.get('soup-input').onChangeText(value); view.render(); };

for (const os of ['web', 'android']) {
  test(`${os}: choosing a mode does not submit; repeated clicks send one paid request`, () => {
    const view = mount(os);
    view.press('soup-mode-guess');
    assert.equal(view.calls.length, 0);
    type(view, '完整推理');
    view.press('soup-submit');
    view.press('soup-submit');
    assert.equal(view.calls.length, 1);
    assert.equal(view.get('soup-input').value, '');
    assert.equal(view.calls[0].text, '完整推理');
    assert.equal(view.calls[0].type, 'soup_submit');
    assert.equal(view.calls[0].kind, 'guess');
    assert.equal(view.calls[0].match_id, 'first');
    assert.match(view.calls[0].request_id, /^[A-Za-z0-9_-]{8,64}$/);
  });
}

test('rejected actions restore the draft; incorrect guesses remain cleared', () => {
  const view = mount();
  type(view, '草稿');
  view.press('soup-submit');
  view.receive('error', { message: '主持忙碌' });
  assert.equal(view.get('soup-input').value, '草稿');
  assert.match(view.render(), /主持忙碌/);
  view.press('soup-mode-guess');
  view.press('soup-submit');
  const id = view.calls.at(-1).request_id;
  view.receive('soup_state', { game: state({ revision: 2, requests: [
    { id, user_id: 1, kind: 'guess', text: '草稿', status: 'answered', answer: '否', correct: false },
  ] }) });
  assert.equal(view.get('soup-input').value, '');
  assert.match(view.render(), /尚未猜中/);
});

test('a draft edited while waiting is not cleared by the previous answer', () => {
  const view = mount();
  type(view, '问题一');
  view.press('soup-submit');
  const id = view.calls[0].request_id;
  type(view, '问题二');
  view.receive('soup_state', { game: state({ revision: 2, requests: [
    { id, user_id: 1, kind: 'ask', text: '问题一', status: 'answered', answer: '是', correct: null },
  ] }) });
  assert.equal(view.get('soup-input').value, '问题二');
});

test('reconnect does not retry; an unconfirmed request can be sent manually after sync', () => {
  const view = mount();
  type(view, '网络中断前的草稿');
  view.press('soup-submit');
  view.disconnect();
  view.reconnect();
  view.press('soup-submit');
  assert.equal(view.calls.length, 1);
  view.receive('sync', { game: state() });
  view.press('soup-submit');
  assert.equal(view.calls.length, 2);
  assert.notEqual(view.calls[0].request_id, view.calls[1].request_id);
});

test('stale revisions and an old waiting-room result cannot replace a new match', () => {
  const view = mount();
  view.receive('game_start', { game: state({ match_id: 'second', revision: 4, title: '新汤面' }) });
  view.receive('soup_state', { game: state({ match_id: 'second', revision: 3, title: '旧版本' }) });
  assert.match(view.render(), /新汤面/);
  view.receive('sync', { game: null, room: { status: 'waiting', players: [] }, soup_result: {
    game: 'turtle_soup', match_id: 'first', summary: { title: '上局答案' },
  } });
  assert.match(view.render(), /新汤面/);
  assert.doesNotMatch(view.render(), /上局答案/);
});

test('reveal requires confirmation; result returns to the room before opening history', () => {
  const view = mount();
  view.press('soup-reveal');
  assert.equal(view.calls.length, 0);
  view.pressText('取消');
  assert.equal(view.calls.length, 0);
  view.press('soup-reveal');
  view.press('soup-confirm');
  assert.equal(view.calls[0].type, 'soup_reveal');
  view.receive('game_over', { game: 'turtle_soup', match_id: 'first', reason: 'solved', winner: 'players',
    summary: { game: 'turtle_soup', title: '题目', surface: '汤面', solution: '汤底答案', request_count: 1, reason: 'solved' } });
  assert.match(view.render(), /汤底答案/);
  view.press('game-result-history');
  assert.deepEqual(view.navigationCalls.map(call => call[0]), ['Room', 'History']);
});


test('failed AI requests restore an untouched editor, without overwriting subsequent edits', () => {
  for (const edited of [false, true]) {
    const view = mount();
    type(view, '提交给主持的问题');
    view.press('soup-submit');
    assert.equal(view.get('soup-input').value, '');
    const id = view.calls[0].request_id;
    if (edited) type(view, '新问题');
    view.receive('soup_state', { game: state({ revision: 2, requests: [
      { id, user_id: 1, kind: 'ask', text: '提交给主持的问题', status: 'failed', error: '主持请求中断' },
    ] }) });
    assert.equal(view.get('soup-input').value, edited ? '新问题' : '提交给主持的问题');
    assert.match(view.render(), /主持请求中断/);
  }
});

test('an intentionally cleared new draft is not replaced by a late rejection', () => {
  const view = mount();
  type(view, '旧问题');
  view.press('soup-submit');
  type(view, '新问题');
  type(view, '');
  view.receive('error', { request_id: view.calls[0].request_id, message: '提交被拒绝' });
  assert.equal(view.get('soup-input').value, '');
});
