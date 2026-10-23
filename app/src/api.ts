import { API_BASE } from './config';
import type { SpiderSnapshot } from './spider';
import { parseError } from './utils';

// Callback invoked on 401 - set by AuthProvider to trigger logout
let onUnauthorized: (() => void) | null = null;

export function setOnUnauthorized(cb: () => void): void {
  onUnauthorized = cb;
}

// Core fetch wrapper: injects token, handles errors, parses JSON
export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
  token?: string | null,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
  if (res.status === 401) {
    onUnauthorized?.();
    throw new Error('登录已过期，请重新登录');
  }
  if (!res.ok) {
    const msg = await parseError(res);
    throw new Error(msg);
  }
  // Some endpoints return empty body on success (e.g. 204)
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// --- Auth API ---

export interface User {
  id: number;
  username: string;
  nickname: string | null;
  role: 'admin' | 'user';
  banned: boolean;
  created_at: string;
}

export interface AuthResult {
  token: string;
  user: User;
}

export function login(username: string, password: string): Promise<AuthResult> {
  return apiFetch<AuthResult>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export function register(username: string, password: string): Promise<AuthResult> {
  return apiFetch<AuthResult>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export function getMe(token: string): Promise<User> {
  return apiFetch<User>('/api/auth/me', {}, token);
}

export function updateNickname(token: string, nickname: string | null): Promise<User> {
  return apiFetch<User>('/api/auth/me', {
    method: 'PUT',
    body: JSON.stringify({ nickname }),
  }, token);
}

export function changePassword(token: string, oldPassword: string, newPassword: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  }, token);
}

// --- Admin API ---

export interface UserListResult {
  total: number;
  items: User[];
}

export function adminListUsers(token: string, page: number, size: number, keyword?: string): Promise<UserListResult> {
  let url = `/api/admin/users?page=${page}&size=${size}`;
  if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`;
  return apiFetch<UserListResult>(url, {}, token);
}

export function adminSetBanned(token: string, userId: number, banned: boolean): Promise<User> {
  return apiFetch<User>(`/api/admin/users/${userId}/ban`, {
    method: 'POST',
    body: JSON.stringify({ banned }),
  }, token);
}

export function adminSetRole(token: string, userId: number, role: 'admin' | 'user'): Promise<User> {
  return apiFetch<User>(`/api/admin/users/${userId}/role`, {
    method: 'POST',
    body: JSON.stringify({ role }),
  }, token);
}

// --- Room API ---

export interface RoomPlayer {
  id: number;
  username: string;
  nickname: string | null;
  ready: boolean;
  is_host: boolean;
  connected: boolean;
  is_bot?: boolean;
  /** 人机的 AI 难度档（仅海战棋：easy/normal/hard），其他游戏为 null。 */
  ai_difficulty?: string | null;
  /** 断线重连剩余秒数；在线或非游戏中为 null。客户端据此倒计时。 */
  reconnect_left?: number | null;
}

/**
 * 一条**房间级设置**（服务端下发，v1.15）。客户端不认识任何 `key` 的语义，
 * 只按 `kind` 画控件：`choice` 画一排芯片、`toggle` 画一个开关。
 *
 * 扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋棋盘 / UNO 房规**共用这一份结构**
 * —— 在这之前是五套字段、四种形状，而且扫雷与军棋的档位表压根不下发（客户端
 * 各抄了一份）。服务端那张表在 `server/app/room_settings.py`。
 */
export interface RoomSettingChoice {
  value: string | number;
  label: string;
  blurb: string;
}

export interface RoomSetting {
  /** 线上的唯一键（同一个房间里不重复）。 */
  key: string;
  /** 中文名（服务端翻译好，客户端不抄表）。 */
  label: string;
  kind: 'choice' | 'toggle';
  value: string | number | boolean;
  /** 当前值的**中文名**，房间页那行摘要直接用（`custom` 这种不在 choices 里的也有名字）。 */
  value_label: string;
  /** 兜底说明：当前值能在 `choices` 里找到时用那条的 blurb，否则用这一句。 */
  blurb: string;
  choices?: RoomSettingChoice[];
  /**
   * `kind === 'toggle'` 时才有：**两个状态的中文名**（`[开, 关]`）。
   * 开关也按「一排芯片」渲染，所以不能写死「开 / 关」—— 军棋那条叫「公开 / 不公开」。
   */
  toggle_labels?: [string, string];
  /** 只在另一项取某个值时才显示（军棋的「阵亡公开身份」只在暗棋下有意义）。 */
  visible_when?: { key: string; value: string | number | boolean };
  /** 是否进房间页那行摘要（UNO 有 8 项，全拼出来太长）。 */
  in_summary?: boolean;
}

export interface Room {
  code: string;
  name: string;
  game: string;
  host_id: number;
  // 房间只有两种状态：等待中 / 游戏中。对局结束即回到 waiting（可续开新局）。
  status: 'waiting' | 'playing';
  has_password: boolean;
  max_players: number;
  players: RoomPlayer[];
  /**
   * **本房间可调的全部设置**（v1.15）。没有可调项的游戏（五子棋等）不下发这个键。
   * 房主在等待大厅里点「玩法设置」打开抽屉逐条改；非房主只能看。
   */
  settings?: RoomSetting[];
  /** 已打完的局数；>0 说明这房打过至少一局，可提示「可续开新一局」 */
  round?: number;
  /** 请求者是否仍是这局的在局玩家（仅单房接口返回；房间页据此自愈跳转）。 */
  in_game?: boolean;
}

export interface RoomListResult {
  items: Room[];
}

/**
 * 建房。**游戏级设置一律不在这里传** —— 扫雷难度、军棋玩法、深空休眠地图、海战棋
 * 棋盘、UNO 房规都是「房主进房之后再选」（统一走 `room_setting`），与「房间是给人
 * 进来一起选的」这件事对齐；创建接口只负责挑游戏类型。
 */
export function createRoom(
  token: string,
  game: string,
  name?: string | null,
  password?: string | null,
): Promise<Room> {
  const body: Record<string, unknown> = { game };
  if (name !== undefined && name !== null) body.name = name;
  if (password !== undefined && password !== null) body.password = password;
  return apiFetch<Room>('/api/rooms', {
    method: 'POST',
    body: JSON.stringify(body),
  }, token);
}

export function listRooms(token: string, game?: string): Promise<RoomListResult> {
  let url = '/api/rooms';
  if (game) url += `?game=${encodeURIComponent(game)}`;
  return apiFetch<RoomListResult>(url, {}, token);
}

export function getRoom(token: string, code: string): Promise<Room> {
  return apiFetch<Room>(`/api/rooms/${code}`, {}, token);
}

export function joinRoom(token: string, code: string, password?: string | null): Promise<Room> {
  const body: Record<string, unknown> = {};
  if (password !== undefined && password !== null) body.password = password;
  return apiFetch<Room>(`/api/rooms/${code}/join`, {
    method: 'POST',
    body: JSON.stringify(body),
  }, token);
}

export function leaveRoom(token: string, code: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/api/rooms/${code}/leave`, {
    method: 'POST',
  }, token);
}

// --- History API ---

export interface HistoryPlayer {
  username: string;
  nickname: string | null;
  seat: number;
  color?: string;
}

export interface HistoryResult {
  username: string;
  result: 'win' | 'lose' | 'draw' | 'end';
}

export interface GomokuSnapshot {
  game: 'gomoku';
  board_size: number;
  board: number[][];
  winner: string | null;
  reason: string | null;
}

export interface ReversiSnapshot {
  game: 'reversi';
  board_size: number;
  board: number[][];
  winner: string | null;
  reason: string | null;
}

export interface MinesweeperSnapshot {
  game: 'minesweeper';
  width: number;
  height: number;
  mines: number;
  lives: number;
  revealed: number[][];
  flagged: number[][];
  winner: string | null;
  reason: string | null;
}

export interface BattleshipSnapshot {
  game: 'battleship';
  phase: string;
  winner: string | null;
  reason: string | null;
  next: string | null;
  player_colors: Record<string, string>;
  placements: Record<string, { ships: Array<Record<string, unknown>>; board: number[][] }>;
  shots: Record<string, Array<Record<string, unknown>>>;
}

export interface XiangqiSnapshot {
  game: 'xiangqi';
  rows: number;
  cols: number;
  /** 10×9，元素是棋子单字符（红大写/黑小写）或 null。 */
  board: (string | null)[][];
  moves: Array<{ from: number[]; to: number[]; piece: string; captured: string | null }>;
  winner: string | null;
  reason: string | null;
}

export interface ConnectFourSnapshot {
  game: 'connect4';
  cols: number;
  rows: number;
  /** rows × cols，元素是 0（空）/ 1（黑）/ 2（白）。 */
  board: number[][];
  winner: string | null;
  reason: string | null;
}

/** 俄罗斯方块战绩快照：每人一张终局盘面 + 统计（uid 是字符串键）。
 *
 * `ranking` 里带 `user_id`/`username`/名次，`players` 是 uid → 身份（与 2048 同理：
 * 颜色既不是身份也不是名次，光有 uid→盘面 的映射没法渲染「谁是谁」）。
 */
export interface TetrisSnapshot {
  game: 'tetris';
  /** uid → 20×10 盘面；0 空、1..7 方块、8 攻击行填充块 */
  boards: Record<string, number[][]>;
  scores: Record<string, number>;
  lines: Record<string, number>;
  levels: Record<string, number>;
  garbage_received: Record<string, number>;
  winner: string | null;
  reason: string | null;
  ranking: Array<{
    seat: number; user_id: number; username: string; nickname: string | null;
    is_bot?: boolean; color: string; score: number; lines: number; level: number;
    garbage_received: number; finish_reason: string | null; rank: number;
  }>;
  players: Record<string, { username: string; nickname: string | null; is_bot?: boolean; color: string }>;
}

/** UNO 战绩快照：终局每人剩余手牌 + 名次。
 *
 * `hands`/`points` 以**座位号**为键，`seat_uids` 给出座位 → uid 的对应。
 * `ranking` 里已经带了用户名与剩余点数（单局定胜负：出完手牌的人第一，
 * 其余按剩余点数升序），战绩页直接照着渲染即可。
 */
export interface UnoSnapshot {
  game: 'uno';
  num_players: number;
  /** 座位号 → uid（座位号即 room.players 下标，也是回合顺序） */
  seat_uids: number[];
  /** 座位号 → 剩余手牌（牌码，如 `R5` / `RS` / `W+4`） */
  hands: Record<string, string[]>;
  /** 座位号 → 剩余手牌点数（数字牌面值 / 功能牌 20 / 黑牌 50） */
  points: Record<string, number>;
  discard_top: string | null;
  /** 胜者的**座位号**（不是颜色，也不是 uid） */
  winner: number | null;
  reason: string | null;
  ranking: Array<{
    seat: number; username: string; nickname: string | null;
    cards: number; points: number; surrendered: boolean; rank: number;
  }>;
}

export interface DotsSnapshot {
  game: 'dots';
  dots: number;
  rows: number;
  cols: number;
  h: number[][];
  v: number[][];
  boxes: number[][];
  scores: Record<string, number>;
  winner: string | null;
  reason: string | null;
}

export interface Game2048Snapshot {
  game: '2048';
  boards: Record<string, number[][]>;
  scores: Record<string, number>;
  max_tiles: Record<string, number>;
  winner: string | null;
  reason: string | null;
}

export interface LudoSnapshot {
  game: 'ludo';
  num_players: number;
  players: Record<string, { pieces: number[]; color: string; finished_count: number; finished: boolean }>;
  ranking?: Array<{ seat: number; username: string; nickname: string | null; color: string; finished_count: number; rank: number }>;
  reason: string | null;
}

/** 军棋战绩快照。`board` 是**全量真相**（含暗棋下当时扣着的棋子）——
 * 对局已经结束，全量更有复盘价值。棋子是 `{c: 颜色, p: 棋子码}` 或 null。 */
export interface LuzhanqiSnapshot {
  game: 'luzhanqi';
  rows: number;
  cols: number;
  mode: 'open' | 'hidden';
  /** 棋盘地形（与服务端 `TOPOLOGY` 同一份），战绩页复用对局屏的画法。 */
  topology: import('./luzhanqi').LzqTopology;
  board: ({ c: string; p: string } | null)[][];
  moves: Array<{ from: number[]; to: number[]; piece: string; captured: string | null }>;
  winner: string | null;
  reason: string | null;
}

export type GameSnapshot = GomokuSnapshot | ReversiSnapshot | MinesweeperSnapshot | BattleshipSnapshot | DotsSnapshot | LudoSnapshot | Game2048Snapshot | XiangqiSnapshot | ConnectFourSnapshot | TetrisSnapshot | UnoSnapshot | LuzhanqiSnapshot | SpiderSnapshot;

export interface GameRecord {
  id: number;
  game: 'gomoku' | 'reversi' | 'minesweeper' | 'battleship' | 'dots' | 'ludo' | '2048' | 'xiangqi' | 'connect4' | 'tetris' | 'uno' | 'luzhanqi';
  room_code: string;
  players: HistoryPlayer[];
  results: HistoryResult[];
  snapshot: GameSnapshot;
  started_at: string;
  ended_at: string;
}

export interface HistoryListResult {
  items: GameRecord[];
  total: number;
  page: number;
  size: number;
}

export function fetchMyHistory(
  token: string,
  page: number = 1,
  size: number = 20,
): Promise<HistoryListResult> {
  return apiFetch<HistoryListResult>(
    `/api/history/mine?page=${page}&size=${size}`,
    {},
    token,
  );
}
