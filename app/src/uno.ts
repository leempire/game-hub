/**
 * UNO 牌码的**展示层**辅助。
 *
 * **这里没有任何规则**——能不能出、出哪张、罚几张，全部由服务端裁决并随
 * `playable` / `pending_draw` 等字段下发。象棋的合法着法、飞行棋的走子路径、
 * 俄罗斯方块的方块形状都是同一条教训：客户端多写一份规则必然与服务端漂移。
 * 本文件只做「把 `R+2` 这串码翻译成颜色 + 牌面」这类纯渲染工作。
 *
 * 牌码编码（与服务端 `server/app/uno.py` 一致）：
 * `R5` 红 5 ｜ `RS` 红跳过 ｜ `RR` 红反转 ｜ `R+2` 红 +2 ｜ `W` 变色 ｜ `W+4` 变色+4
 */

/** 颜色键：R 红 / G 绿 / B 蓝 / Y 黄 / W 黑（未定色）。 */
export type UnoColorKey = 'R' | 'G' | 'B' | 'Y' | 'W';

export const UNO_COLOR_KEYS: UnoColorKey[] = ['R', 'G', 'B', 'Y'];

/**
 * 颜色名字。选色弹窗与徽标旁都要带上它——**颜色不能单独承载信息**
 * （WCAG 1.4.1），只给四个色块对色觉障碍玩家等于没给。
 */
export const UNO_COLOR_NAMES: Record<string, string> = {
  R: '红', G: '绿', B: '蓝', Y: '黄', W: '黑',
};

export function cardColor(card: string): string {
  return card.charAt(0);
}

/**
 * 牌面：`"0"`–`"9"` / `"S"` / `"R"` / `"+2"` / `""`(变色) / `"+4"`，
 * 以及 No Mercy 牌堆的六种：`"SE"`(跳过所有人) / `"DA"`(弃同色) /
 * `"+6"` / `"+10"` / `"+RD4"`(变色反转+4) / `"+RO"`(颜色轮盘)。
 *
 * **这里没有任何规则** —— 什么牌能出、罚几张、谁赢，全部由服务端裁决。
 */
export function cardKind(card: string): string {
  return card.slice(1);
}

/** 会让下家摸牌的牌面（服务端 `uno.PENALTY_VALUE` 的镜像，**只用于文案**）。 */
export const UNO_DRAW_KINDS = ['+2', '+4', '+6', '+10', '+RD4'];

export function isWild(card: string): boolean {
  return card.charAt(0) === 'W';
}

/** 牌面文案（给无障碍标签与提示用）。 */
export function cardLabel(card: string): string {
  const color = UNO_COLOR_NAMES[cardColor(card)] || '';
  const kind = cardKind(card);
  if (kind === 'S') return `${color}色 跳过`;
  if (kind === 'R') return `${color}色 反转`;
  if (kind === 'SE') return `${color}色 跳过所有人`;
  if (kind === 'DA') return `${color}色 弃掉所有同色牌`;
  if (kind === '+2') return `${color}色 +2`;
  if (kind === '+4') return '变色 +4';
  if (kind === '+6') return '变色 +6';
  if (kind === '+10') return '变色 +10';
  if (kind === '+RD4') return '变色反转 +4';
  if (kind === '+RO') return '颜色轮盘';
  // 组队档专属的两张黑牌（官方 UNO TEAMS）
  if (kind === 'TP') return '变色 传给队友';
  if (kind === 'TD4') return '变色 全队罚四';
  if (kind === '') return '变色牌';
  return `${color}色 ${kind}`;
}

// ⚠️ 这里原本有一个 `unoRuleBlurb`（一条房规「当前取值」的说明）。v1.15 搬到了
// `roomSettings.tsx` 的 `settingBlurb` —— 它已经与 UNO 无关了：扫雷难度 / 军棋玩法 /
// 深空休眠地图 / 海战棋棋盘现在都走同一套「房间级设置」的渲染。