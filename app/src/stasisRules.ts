// 深空休眠的**规则常量与纯函数**（客户端这一份，服务端 `server/app/stasis.py` 是权威）。
//
// 为什么单独一个文件：这一屏（`StasisScreen.tsx`）原来 2700 行，其中三百行是
// 「这张表是多少 / 这一级叫什么 / 这个等级画多大的圈」—— 它们与 React 无关，
// 却夹在组件之间，改一个数值要在一堆 JSX 里翻。拆出来之后：
//
// - **数值只有一个地方可改**（费用表 / 产率表 / 门槛表 / 射程公式）；
// - 屏幕层只剩「状态 + 回调 + 布局」，回归时不必读三百行常量；
// - 这些值与服务端的对应关系集中写在文件头，改一边能立刻看到另一边在哪。
//
// ⚠️ **这里的每个数都是服务端那份的副本**，用于「按钮上写多少资源」「买不起时差
// 多少」「面板上标多少产率」这类**展示**。真伪一律由服务端裁决（非法动作回 `error`，
// 客户端 toast）。同步对象：
//
// | 这里 | 服务端 |
// |---|---|
// | `BED_COST` / `DOOR_COST` / `TURRET_COST` / `REFINERY_COST` | `stasis.UPGRADE_COST` |
// | `BUILDINGS[*].cost` | `stasis.BUILD_COST` |
// | `BED_RATE` / `TURRET_DPS` / `REFINERY_RATE` / `MINE_RATE` | `stasis.BED_RATE` 等 |
// | `BED_DOOR_REQ` | `stasis.BED_DOOR_REQ` |
// | `turretRange` | `stasis.turret_range` |
// | `PLAYER_SPEED` | `stasis.PLAYER_SPEED` |
//
// **「躺下」的判据不在这里** —— v6 起它整个由服务端下发（`players[uid].lie`），
// 客户端不再复刻（理由见 `StasisScreen` 里 `canLie` 的注释）。
import { Platform } from 'react-native';
import {
  colors,
  stasisTurret, stasisRefinery, stasisRepair,
  stasisShield, stasisBinder, stasisCryo, stasisAccel,
  stasisCopper, stasisSilver, stasisGold, stasisDiamond,
  stasisDoorOk, stasisDoorWarn, stasisDoorLow,
} from './theme';
import { TILE, type MapGeom } from './stasisMap';

// ── 建筑 ────────────────────────────────────────────────

export const BUILDINGS: Record<string, { name: string; res: 'bio' | 'alloy'; cost: number; effect: string; max?: number }> = {
  // **名字一律是本作自己的**（v7）：深空休眠是换皮作品，界面上不该出现原版的
  // 名称（用户明确要求）。`effect` 里也不要写原版的资源名。
  turret: { name: '哨戒炮', res: 'bio', cost: 8, effect: '自动打射程内的静默体（Lv1 射程 4 格）' },
  refinery: { name: '精炼机', res: 'bio', cost: 200, effect: '产合金（Lv1 每 2 秒 1 个，满级 30/秒）' },
  repair: { name: '纳米修复器', res: 'alloy', cost: 64, max: 4, effect: '每秒回本舱气密门 2% 血' },
  copper: { name: '铜矿', res: 'alloy', cost: 128, effect: '产生物能（每秒 8）' },
  silver: { name: '银矿', res: 'alloy', cost: 512, effect: '产生物能（每秒 32）' },
  gold: { name: '金矿', res: 'alloy', cost: 1024, effect: '产生物能（每秒 64）' },
  diamond: { name: '钻石矿', res: 'alloy', cost: 4096, effect: '产生物能（每秒 256），可升成氪金矿' },
  // **控场四件**（v13）。它们不打伤害，改的是静默体的行为或炮台的效率 ——
  // 定位是「炮台还没成规模之前，先把鬼按住」。四条效果的数值与服务端一致
  // （`stasis.SHIELD_TRIGGER` / `CRYO_SLOW` / `BINDER_CHANCE` / `ACCEL_BOOST`）。
  shield: { name: '应急力场', res: 'alloy', cost: 64, max: 1, effect: '门血低于 30% 时开 3 秒无敌护盾（冷却 60 秒）' },
  binder: { name: '束缚弹射器', res: 'alloy', cost: 64, max: 1, effect: '它放 EMP 或刚进入过载时，30% 概率定住它 3 秒' },
  cryo: { name: '低温阱', res: 'alloy', cost: 256, max: 3, effect: '降低它 20% 攻速（每台叠加一档）' },
  accelerator: { name: '粒子加速器', res: 'alloy', cost: 2048, max: 1, effect: '本舱哨戒炮的攻速 +50%（不跨舱室）' },
};

/**
 * 建造菜单的顺序：机器在前、矿按档位递增在后。
 *
 * **v5 把原来的「培养槽 + 3 级升级链」拆成了四个独立条目**（用户要求，
 * `增强调研.md` §10.1）：原作的四种矿在道具列表里本来就是四个并列条目
 * （各自写「放置需要 X 闪电」）。升级链删除是有意的 —— 两条路并存时升级链
 * 恒为劣选（128+1024 > 1024），留着就是一份永远不该点的 UI。
 */
export const BUILD_KINDS = ['turret', 'refinery', 'repair',
  'shield', 'binder', 'cryo', 'accelerator',
  'copper', 'silver', 'gold', 'diamond'];

/**
 * 人机**性格**的展示名（v9）。服务端下发的是 key（`balanced` / `turtle` / …），
 * 界面上要给人看的是中文。
 *
 * **为什么值得显示**：同一局里的几个人机刻意用不同打法（用户要求「每个人机造的
 * 建筑都不一样」），但玩家看不见这份差异时，只会觉得「这人机怎么乱造、一会儿铺塔
 * 一会儿升门」。标一个「龟缩」「莽夫」，那些取舍立刻读得懂。
 */
export const AI_PROFILE_NAMES: Record<string, string> = {
  balanced: '均衡',
  turtle: '龟缩',
  rush: '莽夫',
  miner: '矿工',
  medic: '修补匠',
  casual: '随性',
};

/** 四座矿的档位（0-3）。贴图与配色按档位取值。 */
export const MINE_TIERS = ['copper', 'silver', 'gold', 'diamond'];
export const MINE_TINT = [stasisCopper, stasisSilver, stasisGold, stasisDiamond];

/** 门的档位名（原型：铁门 1-5 / 钢门 1-5 / 振金门 1-3，共 13 级）。 */
export const DOOR_TIER = ['铁门', '钢门', '振金门'];

/**
 * **升到床 Lv N 需要的最低门等级** —— 原型的门槛是**间断的**，不是每级 +1
 * （与服务端 `stasis.BED_DOOR_REQ` 同值，锚点见 `增强调研.md` §9.2）。
 */
export const BED_DOOR_REQ: Record<number, number> = {
  2: 1, 3: 2, 4: 2, 5: 5, 6: 5, 7: 8, 8: 9, 9: 11, 10: 13,
};

export function doorName(level: number): string {
  const tier = Math.min(Math.floor((level - 1) / 5), DOOR_TIER.length - 1);
  return `${DOOR_TIER[tier]} ${((level - 1) % 5) + 1}`;
}

export function bedDoorReq(level: number): number {
  return BED_DOOR_REQ[level] ?? 1;
}

// ── 数值表 ──────────────────────────────────────────────

// **费用**表带表首占位 0，**下标 = 当前等级**：`BED_COST[1]` 是「1 级升 2 级」的
// 价（与服务端 `stasis.UPGRADE_COST[BED][lv - 1]` 同值，只是读法不同）。
// 满级 = `表长 - 1`（`maxLevelOf`），所以**加一级只需要往表尾补一个数**。
//
// 与服务端 `stasis.UPGRADE_COST` 同值。床 25/50/…（满级 10 级 512/秒）、
// **门 16/32/64/128/256…（纯 ×2 链，v7 修）**、炮台 16/40/88/128…（满级 13 级）、
// 精炼机 400/800/…、四座矿 128/512/1024/2048。见 `增强调研.md` §9.1 / §11。
export const BED_COST = [0, 25, 50, 100, 200, 400, 800, 1600, 3200, 6400];
export const DOOR_COST = [0, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768];
export const TURRET_COST = [0, 16, 40, 88, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768];
// 精炼机 **v10 起满级 10 级**，价钱每次翻倍（用户定的规则；原作只有 1→2 级那
// 个 400 查得到来源，见 `增强调研.md` §13.2）。
export const REFINERY_COST = [0, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200, 102400];
// **四座矿里只有钻石矿有升级表**（v10）：花 4096 合金升成「氪金矿」，秒产翻倍。
// 铜/银/金仍是「一个等级、想换更快的就拆了重造」（拆除返还一半建造费）。
//
// ⚠️ 这张表**下标 = 当前等级**（同其余费用表，表首是占位 0）；矿升级掏的是
// **合金**，见 `upgradeRes`。
export const MINE_COST: Record<string, number[]> = { diamond: [0, 4096] };

/**
 * **第 6 级起，升级还要搭合金**（v7，与服务端 `stasis.upgrade_alloy` 同一条规则）。
 *
 * 原型的规则是「铁门 1-5 只吃金币，进钢门之后要金币加闪电」，本作三样统一取
 * 第 6 级这条线，比例 1/32。**客户端的「够不够钱」判断必须两个资源一起看** ——
 * 只看生物能会画出一颗点不动的按钮（服务端会回「合金不够」）。
 */
export const ALLOY_FROM_LEVEL = 6;
export const ALLOY_RATIO = 32;

/** 从 `level` 升到 `level+1` 要搭的合金（0 = 这一级只要生物能）。 */
export function upgradeAlloy(cost: number[], level: number): number {
  if (level + 1 < ALLOY_FROM_LEVEL) return 0;
  return cost[level] / ALLOY_RATIO;
}

/**
 * 升级掏的是哪个资源池（与服务端 `stasis.upgrade_res` 同一条规则）。
 *
 * **矿那一族升级花的是合金** —— 与它们的建造费同一个池子，这样「再造一座矿」
 * 与「把手上这座升上去」才是一道真选择题（两边的钱是同一份）。
 */
export function upgradeRes(kind: string): 'bio' | 'alloy' {
  return kind in MINE_RATE ? 'alloy' : 'bio';
}

/** 某种建筑的升级费用表（表首占位 0，下标 = 当前等级）。没有升级档的返回 null。 */
export function upgradeTable(kind: string): number[] | null {
  if (kind === 'bed') return BED_COST;
  if (kind === 'door') return DOOR_COST;
  if (kind === 'turret') return TURRET_COST;
  if (kind === 'refinery') return REFINERY_COST;
  return MINE_COST[kind] ?? null;
}

// **产率**表**没有占位**，下标 = 当前等级 - 1。
//
// ⚠️ **这些表千万别加占位 0**：它们一律用 `[level - 1]` 取值，若表首再塞一个 0，
// 「1 级」会读到 0、「2 级」会读到本该是 1 级的值 —— 界面上写着 1、实际产 2，
// 用户报的「生物能生成速度看起来是标注的两倍」就是这个（Lv2 标 1 实产 2）。
// 口径与服务端 `stasis.BED_RATE` / `TURRET_DPS` / `REFINERY_RATE` / `MINE_RATE`
// 一致（矿那张按 kind 取值，见 v5 的拆分）。
export const BED_RATE = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512];
export const TURRET_DPS = [4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384];
// 精炼机：**v10 起 1 级 0.5、2 级 2、3~6 级每级 +2、7~10 级每级 +5**（加法曲线，
// 与原作口径一致），所以 6 级 10/秒、10 级 30/秒。
export const REFINERY_RATE = [0.5, 2, 4, 6, 8, 10, 15, 20, 25, 30];
/** 矿的产率：**按 kind + 等级取**（V10 起钻石矿 2 级＝氪金矿，翻倍）。 */
export const MINE_RATE: Record<string, number[]> = {
  copper: [8], silver: [32], gold: [64], diamond: [256, 512],
};

/** 矿在 `level` 级的产率（与服务端 `stasis.mine_rate` 同一个口径）。 */
export function mineRate(kind: string, level: number): number | null {
  const table = MINE_RATE[kind];
  if (!table) return null;
  return table[Math.min(level, table.length) - 1];
}
export const PATCH_SELF = 20;
export const PATCH_REMOTE = 30;

/** 满级 = 费用表长度 - 1（表首那个占位 0 不算一级）。 */
export function maxLevelOf(cost: number[]): number {
  return cost.length - 1;
}

/**
 * 哨戒炮的射程（格）。**与服务端 `stasis.turret_range` 同一个公式**：
 * 4 格起、每级 +0.5、上限 6 —— 服务端逐塔按这个值判距离，客户端画范围圈
 * 必须用同一个数，否则「圈里的打不到」。
 */
export function turretRange(level: number): number {
  return Math.min(4 + 0.5 * (level - 1), 6);
}

// ── 时间与相机 ──────────────────────────────────────────

/** 与服务端 `PLAYER_SPEED` 一致 —— 本地预演要用同一个速度，否则校正时会一直拽。 */
export const PLAYER_SPEED = 3.6;
/** 预演与权威位置的容差（格）：超过就直接吸附，否则按比例插值。 */
export const RECONCILE_SNAP = 0.9;
export const RECONCILE_LERP = 0.22;
/** 服务端 tick 间隔（ms），与 `stasis.TICK_MS` 一致。远端单位的插值按它定段长的上下限。 */
export const TICK_MS = 200;
/**
 * 远端单位（队友/静默体）一个 tick 内位移超过多少**格**就直接吸附、不插值。
 * 正常移动一个 tick 最多 0.72 格（3.6 格/秒 × 0.2 秒）；超过 2 格只可能是
 * 开局传送进舱室或重连，插值反而会画出一段穿墙的滑行。
 */
export const REMOTE_SNAP = 2;

/** 移动输入的上行最小间隔。服务端 tick 是 200ms，80ms 保证它每次积分拿到的
 *  方向不早于半个 tick；再密只是白费上行带宽。起始/停止两个边沿不受它限制。 */
export const SEND_MIN_MS = 80;

/** 相机可以推出地图之外的余量（px）。边界的格子否则永远只能露半格、点不到。 */
export const PAN_MARGIN = TILE * 2;

/**
 * 相机的**阻尼系数**（每帧向目标靠拢的比例，33ms 一帧 → 约 0.2 秒到位）。
 *
 * 硬跟随会把服务端 tick 的位置校正原样传进画面（走起来一冲一顿）；
 * 加一层 lerp 之后校正被抹平。取值 0.14 是「跟得上但不抖」——再小会有拖影感，
 * 再大就等于硬跟随了。
 */
export const CAM_LERP = 0.14;
/** 相机的**前视**偏移（格）：沿移动方向多留一点视野。 */
export const CAM_LEAD = 0.6;

/** 门血低于这个比例就报红（配文字与条长，颜色不单独承载信息）。 */
export const ALERT_DOOR_RATIO = 0.3;
/** 静默体在门血低于这个比例时攻速 ×2（服务端同值）。 */
export const OVERLOAD_DOOR_RATIO = 0.2;
/** 脉冲类动效的循环次数（WCAG 2.2.2：禁止无限循环）。 */
export const PULSE_CYCLES = 3;

/**
 * 终局文案。
 *
 * **v8 删掉了 `rescue`（「救援舰抵达」）** —— 用户要求唯一获胜条件是击杀静默体，
 * 而且**一局没有时长上限**（「不要求 6 分钟内打死，时间不限」）。所以 `timeout`
 * 不再是「没在时限内打死」，它只剩一种来源：**防卡死的兜底上限**（服务端
 * `STALL_CAP_MS`，30 分钟）—— 正常对局碰不到，文案也照这个口径写。
 * 与服务端 `stasis.REASON_*` 一一对应。
 */
export const REASON_LABELS: Record<string, string> = {
  killed: '静默体被击杀',
  timeout: '僵持超时 · 静默体仍在',
  wiped: '全员舱室失压',
};

export const ENTITY_STATE_LABELS: Record<string, string> = {
  idle: '游荡中',
  approach: '逼近中',
  attack: '撞门中',
  chase: '追击中',
  retreat: '负伤撤退',
  regenerate: '气闸再生',
};

/** 深色页面上的文字两档。
 *  theme 的 `text` / `textSecondary` 是给浅色底写的，压在 stasisSpace 上等于隐身
 *  （#111827 vs #0B1220 只有 1.1:1），所以这一屏只用这两个（实测 vs 页面底）：
 *  白 18.72 ✓、textFaint 7.37 ✓。 */
export const ON_SPACE = colors.white;
export const ON_SPACE_SOFT = colors.textFaint;

/** Web 端参与自定义手势的元素必须声明 `touch-action: none` —— 否则浏览器的
 *  「横拖返回上一页」会把摇杆/地图的拖动抢走（抢走后 JS 收不到 release，
 *  摇杆会永远停在最后的方向上）。RN 的 `ViewStyle` 没有这个字段，RNW 有。 */
export const WEB_TOUCH_NONE = Platform.OS === 'web' ? ({ touchAction: 'none' } as any) : null;

/** Web 端给「每帧都在动」的容器**提升合成图层**（v8）。
 *
 *  整张地图是 700+ 个 DOM 节点，而相机每帧都在改它的 `transform`。声明
 *  `will-change: transform` 之后 transform 只剩合成、不再触发重绘 —— 这是通行做法，
 *  不过**本项目没有单独量出它的收益**：CPU ×6 降速档下整机都慢，隔离不出来
 *  （改前改后都是 39.4 / 39.6 fps、p50 16.7ms）。留着是因为它按机制就该有用，
 *  而且**没有任何代价**（见下）。
 *
 *  **只给「每帧都动」的那几个容器加**（地图 / 角色），不要见一个加一个：每个
 *  `will-change` 都会真的占一层显存，十几发弹丸各占一层反而更慢。
 *  RN 原生端没有这个属性（`ViewStyle` 里也没有），所以只在 Web 上给。 */
export const WEB_GPU = Platform.OS === 'web' ? ({ willChange: 'transform' } as any) : null;

// ── 展示用纯函数 ────────────────────────────────────────

export function fmtClock(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function clampRatio(hp: number, max: number): number {
  if (!max || max <= 0) return 0;
  return Math.max(0, Math.min(1, hp / max));
}

/** 门血的色调（三档）。**永远配条长与数字**，颜色不单独承载信息（WCAG 1.4.1）。 */
export function doorTone(ratio: number): string {
  if (ratio <= 0.3) return stasisDoorLow;
  if (ratio <= 0.6) return stasisDoorWarn;
  return stasisDoorOk;
}

export function buildingColor(kind: string): string {
  if (kind === 'turret') return stasisTurret;
  if (kind === 'refinery') return stasisRefinery;
  if (kind === 'repair') return stasisRepair;
  // 控场四件（v13）：等级徽标与「可升级」描边都取这个色，所以四件都要认。
  if (kind === 'shield') return stasisShield;
  if (kind === 'binder') return stasisBinder;
  if (kind === 'cryo') return stasisCryo;
  if (kind === 'accelerator') return stasisAccel;
  const t = MINE_TIERS.indexOf(kind);
  return t >= 0 ? MINE_TINT[t] : stasisGold;
}

/** 该建筑每秒产多少（写在头顶，让「这格在产多少」不用点开就知道）。
 *  **精炼机与矿都逐级取值**（v10 起钻石矿有两个等级：氪金矿翻倍）。 */
export function buildingRate(kind: string, level: number): { text: string; alloy: boolean } | null {
  if (kind === 'turret') return { text: `${TURRET_DPS[level - 1]}`, alloy: false };
  if (kind === 'refinery') return { text: `${REFINERY_RATE[level - 1]}/s`, alloy: true };
  if (kind === 'repair') return { text: '2%', alloy: true };
  // 控场四件**不产任何东西**（头顶不挂产出数字）—— 它们的价值是「改变鬼的行为
  // 或塔的效率」，写成一个每秒数字只会误导。效果写在建造菜单与升级弹窗里。
  if (kind === 'shield' || kind === 'binder' || kind === 'cryo'
      || kind === 'accelerator') return null;
  const r = mineRate(kind, level);
  return r == null ? null : { text: `${r}/s`, alloy: false };
}

/**
 * 某一类建筑的展示名。**只有本作的名字**（v6）。
 *
 * 之前这里会在后面挂一个括号写原版的叫法（「精炼机（游戏机）」），理由是
 * 「用户看的是原版攻略，对不上名字就找不到」。**这条已经删掉** —— 深空休眠是
 * 换皮作品，界面上出现原版名称是错的（用户明确要求）。攻略对不上名字这件事，
 * 靠 `help.tsx` 里写清楚「本作的精炼机就是产合金的那台」来解决，而不是把
 * 原版的词搬进游戏。
 */
export function buildingName(kind: string, level = 1): string {
  // 逐级的名字（v10）：钻石矿升一级就是「氪金矿」。**只在这一个地方写**，
  // 其他地方一律调这个函数 —— 名字散成两份必然漂移（同「颜色只有一个来源」）。
  if (kind === 'diamond' && level >= 2) return '氪金矿';
  return BUILDINGS[kind]?.name ?? kind;
}

/** 升到下一级的价；满级返回 null。 */
export function costOf(table: number[], level: number): number | null {
  if (level >= maxLevelOf(table)) return null;
  return table[level];
}

/**
 * 客户端预演用的落点判定 —— **与服务端 `stasis_map.can_step` 同一套规则**：
 * 只能站在通道格或舱室地板格上，跨通道↔舱室只能经过门那一对格。
 *
 * 这是这一屏里**唯一保留的第二份实现**：位置预演必须在本地每帧算，等不了往返
 * （同俄罗斯方块「客户端只为手感做本地预演」）。判据本身（谁能躺下、谁能建造）
 * 一律不在这里 —— 那些走服务端下发。
 */
export function canStand(map: MapGeom, from: [number, number], to: [number, number]): boolean {
  const c0: [number, number] = [Math.floor(from[0]), Math.floor(from[1])];
  const c1: [number, number] = [Math.floor(to[0]), Math.floor(to[1])];
  if (c0[0] === c1[0] && c0[1] === c1[1]) return true;
  return map.canStep(c0, c1);
}
