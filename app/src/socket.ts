import { AppState } from 'react-native';
import { WS_BASE } from './config';
import { onWebOnline } from './backGuard';

export type SocketMessage = Record<string, unknown> & { type: string };

type MessageHandler = (msg: SocketMessage) => void;

const PING_MS = 25000;
// 多久一条消息都没收到就认定连接已经「半死」：TCP 没报错、对端却早没了（切网络、
// 息屏后系统回收了 socket）。服务端每个 ping 都回 pong，活着的连接最多 PING_MS
// 就有一条消息进来；留两个周期的余量。
const STALE_MS = PING_MS * 2 + 10000;
// 服务端的拒绝（未授权 / 不在房间里 / 房间不存在）：重连多少次结果都一样
const FATAL_CLOSE = new Set([4001, 4003, 4004]);

// 回到前台、网络恢复时要叫醒的连接（connect 时登记，close 时注销）
const live = new Set<RoomSocket>();
let wakeInstalled = false;

function installWake(): void {
  if (wakeInstalled) return;
  wakeInstalled = true;
  AppState.addEventListener('change', (state) => {
    if (state === 'active') live.forEach((s) => s.wake());
  });
  onWebOnline(() => live.forEach((s) => s.wake()));
}

// RoomSocket: manages a single WS connection for a room.
// Features: auto token, heartbeat ping every 25s (+ stale watchdog), exponential
// backoff reconnect (capped at 30s, never gives up unless the server refuses),
// immediate reconnect when the app returns to foreground / the network comes back.
export class RoomSocket {
  private ws: WebSocket | null = null;
  private code: string;
  private token: string;
  private handlers: Map<string, Set<MessageHandler>> = new Map();
  private anyHandlers: Set<MessageHandler> = new Set();
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectAttempts = 0;
  private lastMessageAt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private paused = false;
  private onOpenCallback: (() => void) | null = null;
  private onCloseCallback: (() => void) | null = null;
  // 大厅类消息（ready/start/add_bot…）在连接建立前入队，onopen 后按序补发；
  // 对局内动作（move/fire/reveal）不排队，避免重连后发出过期操作。
  private pendingLobby: Record<string, unknown>[] = [];

  constructor(code: string, token: string) {
    this.code = code;
    this.token = token;
  }

  connect(onOpen?: () => void, onClose?: () => void): void {
    this.onOpenCallback = onOpen ?? null;
    this.onCloseCallback = onClose ?? null;
    this.closed = false;
    live.add(this);
    installWake();
    this.doConnect();
  }

  private doConnect(): void {
    if (this.closed) return;
    const url = `${WS_BASE}/ws/room/${this.code}?token=${encodeURIComponent(this.token)}`;
    try {
      this.ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.lastMessageAt = Date.now();
      this.startPing();
      this.flushPending();
      this.onOpenCallback?.();
    };

    this.ws.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      // **`try` 只包 `JSON.parse`，不包 `dispatch`。**
      //
      // 原来是一个 `try { parse; dispatch } catch {}`，本意是「服务端偶尔发了
      // 非 JSON 就丢掉这条」—— 但它把**处理器自己的异常**也一起吞了，而且一声
      // 不吭：`suite` 图那次，`buildMap` 抛 `TypeError: Cannot read properties
      // of undefined`，界面上唯一的表现是「地图全是黑的」，控制台里连一行日志
      // 都没有（用户报的正是这个）。**静默 try 会把「崩溃」伪装成「功能没做」**，
      // 排查成本差着量级（见 ~/.claude/CLAUDE.md「错误直接抛」）。
      let msg: SocketMessage;
      try {
        msg = JSON.parse(event.data as string) as SocketMessage;
      } catch {
        return;                     // 真·解析失败：不是 JSON，丢掉这条即可
      }
      this.dispatch(msg);
    };

    this.ws.onclose = (event) => {
      this.stopPing();
      this.onCloseCallback?.();
      if (FATAL_CLOSE.has(event.code)) {
        this.closed = true;
        live.delete(this);
        return;
      }
      if (!this.closed) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      // onclose will fire after onerror, reconnect handled there
    };
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      // 暂停的连接不查：它可能正被同一用户的新连接顶替着，收不到 pong 是正常的，
      // 这时重连会把槽位抢回来（见 pause()）
      if (!this.paused && Date.now() - this.lastMessageAt > STALE_MS) {
        this.reconnect();
        return;
      }
      this.send({ type: 'ping' });
    }, PING_MS);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.closed || this.paused) return;
    // 不设上限：原来 10 次（约 3.5 分钟）后就永远不再连，地铁里断一会儿回来
    // 只能退出重进；封顶 30 秒一次的代价可以忽略
    const delay = Math.min(1000 * Math.pow(2, Math.min(this.reconnectAttempts, 5)), 30000);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => {
      this.doConnect();
    }, delay);
  }

  private dispatch(msg: SocketMessage): void {
    const specific = this.handlers.get(msg.type);
    if (specific) {
      specific.forEach((h) => h(msg));
    }
    this.anyHandlers.forEach((h) => h(msg));
  }

  // Listen for a specific message type
  on(type: string, handler: MessageHandler): void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);
  }

  // Listen for all messages
  onAny(handler: MessageHandler): void {
    this.anyHandlers.add(handler);
  }

  off(type: string, handler: MessageHandler): void {
    this.handlers.get(type)?.delete(handler);
  }

  offAny(handler: MessageHandler): void {
    this.anyHandlers.delete(handler);
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  // 大厅消息：未连接时入队（同类型只保留最后一条），连上后按序补发
  private sendOrQueue(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return;
    }
    this.pendingLobby = this.pendingLobby.filter((m) => m.type !== msg.type);
    if (this.pendingLobby.length < 20) this.pendingLobby.push(msg);
  }

  private flushPending(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const queued = this.pendingLobby;
    this.pendingLobby = [];
    queued.forEach((m) => this.send(m));
  }

  // Send game actions
  ready(): void { this.sendOrQueue({ type: 'ready' }); }
  unready(): void { this.sendOrQueue({ type: 'unready' }); }
  start(): void { this.sendOrQueue({ type: 'start' }); }
  /** difficulty 仅海战棋有意义（easy/normal/hard），其余游戏服务端忽略。 */
  addBot(difficulty?: string): void {
    this.sendOrQueue({ type: 'add_bot', difficulty });
  }
  removeBot(botId?: number): void { this.sendOrQueue({ type: 'remove_bot', bot_id: botId }); }
  /**
   * 等待大厅里请求与某人互换座位（`targetId` 是对方的 user id）。
   *
   * 座位 = 玩家列表里的下标，开局时由服务端读它决定先手 / 出牌顺序 —— 所以这条
   * **只在大厅里有效**，开局后服务端会回一句「对局中不能换位置」。
   * 人机由服务端直接同意（回 `swap_result{auto:true}` + 广播 `lobby`）；
   * 真人则由服务端把 `swap_request` 转给对方，等他在弹窗里点同意。
   *
   * 走 `sendOrQueue`：这是大厅动作（同 ready / start / add_bot），断线期间排队补发。
   */
  swapRequest(targetId: number): void {
    this.sendOrQueue({ type: 'swap_request', target_id: targetId });
  }
  /** 答复别人的换位请求（`fromId` 是发起人的 user id）。 */
  swapRespond(fromId: number, accept: boolean): void {
    this.sendOrQueue({ type: 'swap_respond', from_id: fromId, accept });
  }
  move(x: number, y: number): void { this.send({ type: 'move', x, y }); }
  /** 中国象棋走子：起点 → 终点（x = 列，y = 行）。 */
  xiangqiMove(fromX: number, fromY: number, toX: number, toY: number): void {
    this.send({ type: 'xiangqi_move', from_x: fromX, from_y: fromY, to_x: toX, to_y: toY });
  }
  /** 重力四子棋落子：只给列号，落点行由服务端按重力算并通过 move 广播回来。 */
  drop(col: number): void { this.send({ type: 'move', x: col }); }
  /** 军棋布阵：交换自己半场两枚棋子（合法性由服务端判定，回一条 `luzhanqi_setup`）。 */
  luzhanqiSwap(a: [number, number], b: [number, number]): void {
    this.send({ type: 'luzhanqi_setup', action: 'swap', a, b });
  }
  /** 军棋布阵：重新随机一套合法阵型。 */
  luzhanqiRandom(): void { this.send({ type: 'luzhanqi_setup', action: 'random' }); }

  /** 军棋布阵：套用一套经典阵型（阵型表由服务端下发，见 `LzqView.presets`）。 */
  luzhanqiPreset(name: string): void {
    this.send({ type: 'luzhanqi_setup', action: 'preset', name });
  }
  /** 军棋布阵：确认（双方都确认才开打）。 */
  luzhanqiConfirm(): void { this.send({ type: 'luzhanqi_setup', action: 'confirm' }); }
  /** 军棋走子：起点 → 终点（x = 列，y = 行，同象棋）。 */
  luzhanqiMove(fromX: number, fromY: number, toX: number, toY: number): void {
    this.send({ type: 'luzhanqi_move', from_x: fromX, from_y: fromY, to_x: toX, to_y: toY });
  }
  move2048(direction: string): void { this.send({ type: 'move2048', direction }); }
  /**
   * 俄罗斯方块：把本地攒下的一段动作序列发上去，服务端重放（每块方块一次往返）。
   *
   * **不能排队**（同 move2048/ludo 的动作类消息）：序列过期后重放会作用到新方块上，
   * 盘面立刻与服务端分叉——未连接时直接丢弃，靠 sync 兜底。
   */
  /** 2048 单人局撤销上一步（服务端裁决；多人局会回 error）。 */
  undo2048(): void {
    this.send({ type: 'undo2048' });
  }
  tetrisActions(actions: string[]): void {
    if (actions.length === 0) return;
    this.send({ type: 'tetris_actions', actions });
  }
  /**
   * UNO 出牌。
   *
   * `card` 是牌码（`R5` / `RS` / `R+2` / `W` / `W+4`），**出哪张由服务端裁决**——
   * 客户端只按服务端下发的 `playable` 做高亮，不自己判断能不能出（象棋的合法着法、
   * 飞行棋的走子路径都是同一条教训）。只出黑牌时才传 `color`。
   *
   * 不排队：动作类消息过期后重放会打到新的回合上（同 move2048 / tetris_actions）。
   *
   * `jump` 是**抢出**（`jump_in` 房规）：不轮到我时，用手上与牌堆顶完全相同的牌
   * 打出去。`target` 是 **7 换手**要换给谁（座位号）。两者都由服务端裁决，
   * 客户端只按服务端下发的 `jumpable` / `swap_targets` 决定界面上亮不亮。
   */
  unoPlay(card: string, color?: string, jump?: boolean, target?: number): void {
    this.send({ type: 'uno_play', card, color, jump, target });
  }
  /** UNO 摸牌（有待罚牌时会一次摸完累计张数）。 */
  unoDraw(): void { this.send({ type: 'uno_draw' }); }
  /** UNO 摸到可选牌之后选择不出，移交回合。 */
  unoPass(): void { this.send({ type: 'uno_pass' }); }
  /** UNO 喊 UNO（手牌剩 1 张时）。本版不抓罚，纯广播提示。 */
  /** 喊 UNO。组队档里可以带 `target` **替队友喊**（不传由服务端决定替谁喊）。 */
  unoCall(target?: number): void {
    this.send(target === undefined ? { type: 'uno_call' } : { type: 'uno_call', target });
  }
  /**
   * UNO 颜色轮盘点名（`W+RO`，无怜悯牌堆）：翻抽牌堆直到出现该色，
   * 翻出来的牌全归点名者，然后停一轮。
   *
   * **只有被点名的那个人能发**（服务端只认 `roulette_yours` 那个座位），
   * 客户端靠服务端下发的 `roulette_yours` 决定弹不弹选色框。
   */
  unoRoulette(color: string): void { this.send({ type: 'uno_roulette', color }); }
  surrender(): void { this.send({ type: 'surrender' }); }
  leave(): void { this.send({ type: 'leave' }); }
  reveal(x: number, y: number): void { this.send({ type: 'reveal', x, y }); }
  flag(x: number, y: number): void { this.send({ type: 'flag', x, y }); }
  /**
   * 深空休眠（stasis）：本项目第一个有服务端 tick 的游戏。
   *
   * 五个动作**一律走 send 不排队**——对局内动作过期后执行会作用到新的局面
   * （重连前点的那次建造会在重连后才发出去），同 move2048 / tetris_actions。
   * 服务端对失败动作回 `error` 消息（资源不够 / 这一格不能建造 / …），客户端 toast。
   */
  /**
   * 移动输入（摇杆方向，-1..1，每 tick 一条）。
   *
   * **服务端是位置的权威**，客户端只做本地预演（同俄罗斯方块的教训）：摇杆一动
   * 就本地积分位置保证手感，`stasis_tick` 回来后按服务端位置校正。
   * 松手必须补一条 `(0, 0)`，否则服务端会一直按最后的方向推。
   */
  stasisMove(dx: number, dy: number): void { this.send({ type: 'stasis_move', dx, dy }); }
  /** 在空格建造：kind = turret / refinery / repair / copper / silver / gold / diamond，
   *  cell 是 `"x,y"` 键（**四座矿是四个独立的 kind**，v5 起没有「培养槽升级链」）。 */
  stasisBuild(kind: string, cell: string): void { this.send({ type: 'stasis_build', kind, cell }); }
  /** 升级：`"bed"` 休眠舱 / `"door"` 气密门 / `"x,y"` 该格的哨戒炮。 */
  stasisUpgrade(target: string): void { this.send({ type: 'stasis_upgrade', target }); }
  /** 拆除建筑，返还建造费的 50%。 */
  stasisDemolish(cell: string): void { this.send({ type: 'stasis_demolish', cell }); }
  /** 修补气密门：null 或自己＝修自己（20 生物能），给队友修 ×1.5（30）。 */
  stasisPatch(targetUid: number | null): void { this.send({ type: 'stasis_patch', target_uid: targetUid }); }

  /** 修补**共用闸门**（v9）：`target_uid` 换成闸门 id。 */
  stasisPatchGate(gate: string): void { this.send({ type: 'stasis_patch', gate }); }

  /**
   * **房主在等待大厅里改一条房间级设置**（v1.15 统一入口）。
   *
   * `key` / `value` 就是服务端下发的 `room.settings` 里那一项的键与取值 ——
   * 客户端不认识任何 key 的语义，只把用户点中的那一个原样发回去。服务端校验
   * （房主 / 等待中 / 这个房间有没有这项设置 / 取值合不合法）后广播一条 `lobby`
   * 给全房，界面靠那条广播刷新。
   *
   * **用 `send` 而不是 `sendOrQueue`**：后者对同 type 的消息只保留最后一条，
   * 而这里连续改两项是两次独立的动作（离线时先改难度再改地图，前一条会被挤掉）。
   */
  roomSetting(key: string, value: string | number | boolean): void {
    this.send({ type: 'room_setting', key, value });
  }

  /** 躺进休眠舱（要走到床边）／起身。**躺着不能移动**，服务端会丢掉移动输入。 */
  stasisLie(): void { this.send({ type: 'stasis_lie' }); }
  
  // 强制重建底层连接（保留已注册 handler）：
  // 页面驻留在导航栈底时，其连接可能已被新页面连接替换并被服务端弹出，
  // 重新聚焦时调用此方法重新注册，避免"僵尸连接"收不到广播。
  reconnect(): void {
    if (this.closed) return;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      const old = this.ws;
      this.ws = null;
      old.onopen = null;
      old.onmessage = null;
      old.onclose = null;
      old.onerror = null;
      try { old.close(); } catch { /* ignore */ }
    }
    this.reconnectAttempts = 0;
    this.doConnect();
  }

  // 暂停自动重连：页面失焦（被对局页盖住）时调用。
  // 服务端每个 (房间, 玩家) 只保留一条连接——若房间页与对局页在服务端重启后
  // 同时自动重连，两者会争抢槽位，抢输的那条将永远收不到广播
  // （表现为「对局页还停在旧棋盘，对手走子无反应」）。失焦页让位即可。
  pause(): void {
    this.paused = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  // 回到前台 / 网络恢复：没连上（或连接早已半死）就立刻重连，不等退避计时器
  wake(): void {
    if (this.closed || this.paused) return;
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) return;
    if (!this.isConnected || Date.now() - this.lastMessageAt > STALE_MS) {
      this.reconnect();
    }
  }

  // 恢复自动重连：重新聚焦时调用；若当前没连上则立刻重连一次
  resume(): void {
    if (!this.paused) return;
    this.paused = false;
    if (!this.closed && !this.isConnected) {
      this.reconnect();
    }
  }

  // Close permanently - no reconnect
  close(): void {
    this.closed = true;
    live.delete(this);
    this.pendingLobby = [];
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.handlers.clear();
    this.anyHandlers.clear();
  }

  get isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}
