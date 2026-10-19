// 品牌与游戏入口矢量图标（react-native-svg，三端一致渲染）。
// 几何参数与 tmp/icon_drafts.html 评审稿一致：六芒星=两个等边三角形叠成，顶点共外接圆。
import React from 'react';
import Svg, { Circle, Defs, Ellipse, G, Line, LinearGradient, Path, Polygon, Rect, Stop, Text } from 'react-native-svg';
// UNO 的牌色直接取 theme（「颜色只有 theme.ts 一个来源」是硬规则——本文件其余
// 图标用的是各自的局部常量，UNO 不再复制一份 hex）
import { unoColors, unoWild, unoInk, unoOnWild } from './theme';
// 深空休眠的整套令牌也一律从 theme 取（同上：不在图标里写死 hex）
// **v5 起只剩这一组**：各单位的贴图搬到 `stasisArt.tsx` 了（那边的贴图是
// 「整格 44px 的俯视 sprite」，与这里 64px 的「正面图标」不是一套画法）。
import { stasisSpace, stasisPod, stasisAlert, stasisDoorAlloy, stasisDoorEdge, stasisTurret } from './theme';

const INDIGO = '#4F46E5';
const BOARD = '#DEB887';
const BOARD_LIGHT = '#F0DDBE';
const BOARD_LINE = '#8B6914';
const STONE_BLACK = '#1A1A1A';
const STONE_WHITE = '#F5F5F5';
const STONE_EDGE = '#D4D4D4';
const TILE = '#C7CDD6';
const TILE_LIGHT = '#EDF1F5';
const TILE_DARK = '#98A2B3';

// 应用 Logo（方案C）：圆角方形渐变底 + 白色六芒星与外接圆
export function AppLogo({ size = 56 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 96 96">
      <Defs>
        <LinearGradient id="gh-logo-grad" x1="0" y1="0" x2="1" y2="1">
          <Stop offset="0" stopColor="#6366F1" />
          <Stop offset="1" stopColor="#4338CA" />
        </LinearGradient>
      </Defs>
      <Rect x="0" y="0" width="96" height="96" rx="21" fill="url(#gh-logo-grad)" />
      <Circle cx="48" cy="48" r="30" fill="none" stroke="#FFFFFF" strokeWidth="2.6" opacity="0.92" />
      <Polygon points="48,18 22,63 74,63" fill="#FFFFFF" fillOpacity="0.14" stroke="#FFFFFF" strokeWidth="2.6" strokeLinejoin="round" />
      <Polygon points="48,78 74,33 22,33" fill="#FFFFFF" fillOpacity="0.14" stroke="#FFFFFF" strokeWidth="2.6" strokeLinejoin="round" />
    </Svg>
  );
}

// 五子棋入口图标（方案A）：木色棋盘 + 黑三连斜线 + 两端白子封堵
export function GomokuIcon({ size = 40 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={BOARD} />
      <G stroke={BOARD_LINE} strokeWidth="1.6" opacity="0.7">
        <Line x1="18" y1="14" x2="18" y2="50" />
        <Line x1="32" y1="14" x2="32" y2="50" />
        <Line x1="46" y1="14" x2="46" y2="50" />
        <Line x1="14" y1="18" x2="50" y2="18" />
        <Line x1="14" y1="32" x2="50" y2="32" />
        <Line x1="14" y1="46" x2="50" y2="46" />
      </G>
      <Circle cx="18" cy="18" r="7" fill={STONE_WHITE} stroke={STONE_EDGE} strokeWidth="1.2" />
      <Circle cx="46" cy="46" r="7" fill={STONE_WHITE} stroke={STONE_EDGE} strokeWidth="1.2" />
      <Circle cx="18" cy="46" r="7" fill={STONE_BLACK} />
      <Circle cx="32" cy="32" r="7" fill={STONE_BLACK} />
      <Circle cx="46" cy="18" r="7" fill={STONE_BLACK} />
    </Svg>
  );
}

// 扫雷入口图标（方案C）：立体斜面砖格 + 藏雷
export function MineIcon({ size = 40 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="6" y="6" width="52" height="52" rx="6" fill={TILE} />
      <Line x1="8" y1="56" x2="8" y2="8" stroke={TILE_LIGHT} strokeWidth="3.2" />
      <Line x1="8" y1="8" x2="56" y2="8" stroke={TILE_LIGHT} strokeWidth="3.2" />
      <Line x1="56" y1="8" x2="56" y2="56" stroke={TILE_DARK} strokeWidth="3.2" />
      <Line x1="56" y1="56" x2="8" y2="56" stroke={TILE_DARK} strokeWidth="3.2" />
      <G stroke={STONE_BLACK} strokeWidth="2.8" strokeLinecap="round">
        <Line x1="32" y1="17" x2="32" y2="47" />
        <Line x1="32" y1="17" x2="32" y2="47" transform="rotate(45 32 32)" />
        <Line x1="32" y1="17" x2="32" y2="47" transform="rotate(90 32 32)" />
        <Line x1="32" y1="17" x2="32" y2="47" transform="rotate(135 32 32)" />
      </G>
      <Circle cx="32" cy="32" r="8.5" fill={STONE_BLACK} />
      <Circle cx="29" cy="29" r="2.4" fill="#FFFFFF" opacity="0.9" />
    </Svg>
  );
}

// 黑白棋入口图标（方案A）：绿棋盘 + 半黑半白翻转中的棋子
export function ReversiIcon({ size = 44 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill="#157A46" />
      <G stroke="#FFFFFF" strokeWidth="1.4" opacity="0.22">
        <Line x1="18" y1="12" x2="18" y2="52" />
        <Line x1="32" y1="12" x2="32" y2="52" />
        <Line x1="46" y1="12" x2="46" y2="52" />
        <Line x1="12" y1="18" x2="52" y2="18" />
        <Line x1="12" y1="32" x2="52" y2="32" />
        <Line x1="12" y1="46" x2="52" y2="46" />
      </G>
      <Path d="M 32 19 A 13 13 0 0 0 32 45 Z" fill={STONE_BLACK} />
      <Path d="M 32 19 A 13 13 0 0 1 32 45 Z" fill={STONE_WHITE} />
      <Circle cx="32" cy="32" r="13" fill="none" stroke="#0B4A2A" strokeWidth="1.2" opacity="0.5" />
    </Svg>
  );
}

// 备用：五子棋方案C（黑白对峙）与极简棋盘底，供后续换肤/深色场景
export function BoardBackdrop({ light = false }: { light?: boolean }) {
  return <Rect x="4" y="4" width="56" height="56" rx="12" fill={light ? BOARD_LIGHT : BOARD} />;
}

// 海战棋入口图标：军舰侧影 + 雷达波纹，深蓝海域
const SEA_DARK = '#1E3A5F';
const SEA_MID = '#2563EB';
const SHIP_HULL = '#94A3B8';
const SHIP_DECK = '#CBD5E1';
const RADAR = '#38BDF8';

export function BattleshipIcon({ size = 40 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={SEA_DARK} />
      {/* Waves */}
      <Path d="M 6 42 Q 16 38 26 42 Q 36 46 46 42 Q 56 38 58 40" fill="none" stroke={SEA_MID} strokeWidth="2" opacity="0.5" />
      <Path d="M 6 48 Q 16 44 26 48 Q 36 52 46 48 Q 56 44 58 46" fill="none" stroke={SEA_MID} strokeWidth="1.5" opacity="0.35" />
      {/* Ship hull */}
      <Path d="M 10 36 L 14 40 L 50 40 L 54 36 L 52 34 L 12 34 Z" fill={SHIP_HULL} />
      {/* Deck / superstructure */}
      <Rect x="18" y="26" width="28" height="8" rx="2" fill={SHIP_DECK} />
      <Rect x="24" y="20" width="16" height="6" rx="1.5" fill={SHIP_DECK} />
      {/* Mast */}
      <Line x1="32" y1="12" x2="32" y2="20" stroke={SHIP_HULL} strokeWidth="2" strokeLinecap="round" />
      <Line x1="28" y1="15" x2="36" y2="15" stroke={SHIP_HULL} strokeWidth="1.5" strokeLinecap="round" />
      {/* Radar ripples */}
      <Circle cx="32" cy="12" r="4" fill="none" stroke={RADAR} strokeWidth="1.2" opacity="0.7" />
      <Circle cx="32" cy="12" r="7" fill="none" stroke={RADAR} strokeWidth="0.8" opacity="0.4" />
      <Circle cx="32" cy="12" r="10" fill="none" stroke={RADAR} strokeWidth="0.6" opacity="0.2" />
      {/* Gun turrets */}
      <Circle cx="22" cy="25" r="2" fill={SHIP_HULL} />
      <Circle cx="42" cy="25" r="2" fill={SHIP_HULL} />
    </Svg>
  );
}

// 点格棋入口图标：点阵 + 已画边 + 已封格子着色
const DOTS_BG = '#F9FAFB';
const DOTS_GRID = '#374151';
const DOTS_EDGE1 = '#4F46E5';
const DOTS_EDGE2 = '#EF4444';

export function DotsIcon({ size = 40 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={DOTS_BG} />
      {/* Grid dots (4×4 = 16 dots shown as 3×3 visible in icon) */}
      {[14, 28, 42, 56].map((cx) =>
        [14, 28, 42].map((cy) => (
          <Circle key={`d${cx}-${cy}`} cx={cx - 6} cy={cy - 6} r="2.8" fill={DOTS_GRID} />
        ))
      )}
      {/* Some edges drawn */}
      <Line x1="8" y1="8" x2="22" y2="8" stroke={DOTS_EDGE1} strokeWidth="3" strokeLinecap="round" />
      <Line x1="22" y1="8" x2="36" y2="8" stroke={DOTS_EDGE2} strokeWidth="3" strokeLinecap="round" />
      <Line x1="8" y1="8" x2="8" y2="22" stroke={DOTS_EDGE1} strokeWidth="3" strokeLinecap="round" />
      <Line x1="8" y1="22" x2="22" y2="22" stroke={DOTS_EDGE1} strokeWidth="3" strokeLinecap="round" />
      <Line x1="22" y1="8" x2="22" y2="22" stroke={DOTS_EDGE1} strokeWidth="3" strokeLinecap="round" />
      <Line x1="36" y1="22" x2="50" y2="22" stroke={DOTS_EDGE2} strokeWidth="3" strokeLinecap="round" />
      <Line x1="36" y1="8" x2="36" y2="22" stroke={DOTS_EDGE2} strokeWidth="3" strokeLinecap="round" />
      <Line x1="50" y1="8" x2="50" y2="22" stroke={DOTS_EDGE2} strokeWidth="3" strokeLinecap="round" />
      <Line x1="36" y1="8" x2="50" y2="8" stroke={DOTS_EDGE2} strokeWidth="3" strokeLinecap="round" />
      {/* Filled boxes */}
      <Rect x="9" y="9" width="12" height="12" rx="1" fill={DOTS_EDGE1} fillOpacity="0.2" />
      <Rect x="37" y="9" width="12" height="12" rx="1" fill={DOTS_EDGE2} fillOpacity="0.2" />
      {/* Bottom row edges */}
      <Line x1="8" y1="36" x2="22" y2="36" stroke={DOTS_EDGE1} strokeWidth="3" strokeLinecap="round" />
      <Line x1="8" y1="22" x2="8" y2="36" stroke={DOTS_EDGE1} strokeWidth="3" strokeLinecap="round" />
    </Svg>
  );
}

// 飞行棋图标：十字轨道 + 四色飞机 + 骰子
const LUDO_GREEN = '#2D6A4F';
const LUDO_TRACK = '#E8F5E9';
const LUDO_RED = '#E53935';
const LUDO_YELLOW = '#FDD835';
const LUDO_BLUE = '#1E88E5';
const LUDO_PLANE_GREEN = '#43A047';

export function LudoIcon({ size = 40 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={LUDO_GREEN} />
      <Rect x="22" y="7" width="20" height="50" rx="3" fill={LUDO_TRACK} opacity={0.9} />
      <Rect x="7" y="22" width="50" height="20" rx="3" fill={LUDO_TRACK} opacity={0.9} />
      <Circle cx="32" cy="32" r="7" fill="#FFF" stroke={LUDO_GREEN} strokeWidth={0.8} />
      {/* 四色飞机 */}
      <Path d="M32,13 L36,19 L32,17 L28,19 Z" fill={LUDO_RED} />
      <Path d="M51,32 L45,36 L47,32 L45,28 Z" fill={LUDO_YELLOW} />
      <Path d="M32,51 L28,45 L32,47 L36,45 Z" fill={LUDO_BLUE} />
      <Path d="M13,32 L19,28 L17,32 L19,36 Z" fill={LUDO_PLANE_GREEN} />
      {/* 骰子 */}
      <Rect x="28" y="28" width="8" height="8" rx="1.5" fill="#FFF" stroke="#333" strokeWidth={0.5} />
      <Circle cx="30.5" cy="30.5" r="0.8" fill="#333" />
      <Circle cx="33.5" cy="30.5" r="0.8" fill="#333" />
      <Circle cx="30.5" cy="33.5" r="0.8" fill="#333" />
      <Circle cx="33.5" cy="33.5" r="0.8" fill="#333" />
    </Svg>
  );
}

// 飞行棋棋子图案：俯视客机剪影（机身 + 后掠主翼 + 尾翼）。
// 白色填充 + 深墨描边：圆片本身是座位色，深浅底上都能成形——黄子尤其需要，
// 单靠颜色它和棋盘底色几乎分不开（WCAG 1.4.11）。颜色不是唯一信息通道，
// 图案 + 描边是第二通道。
const PLANE_D =
  'M12 1.6 C12.9 3.2 13.3 5.4 13.3 7.6 L22.2 13.6 L22.2 15.6 L13.3 13 ' +
  'L13.3 18 L16.4 20.8 L16.4 22.2 L12 20.6 L7.6 22.2 L7.6 20.8 L10.7 18 ' +
  'L10.7 13 L1.8 15.6 L1.8 13.6 L10.7 7.6 C10.7 5.4 11.1 3.2 12 1.6 Z';

export function PlaneGlyph({
  size = 20,
  fill = '#FFFFFF',
  stroke = 'rgba(0,0,0,0.55)',
}: {
  size?: number;
  fill?: string;
  stroke?: string;
}) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Path d={PLANE_D} fill={fill} stroke={stroke} strokeWidth={1.6} strokeLinejoin="round" />
    </Svg>
  );
}

// 骰子面：1-6 的点数布局（矢量画点，不用 emoji）。value 为 null 时画空白骰面。
const DIE_PIPS: Record<number, ReadonlyArray<readonly [number, number]>> = {
  1: [[12, 12]],
  2: [[7, 7], [17, 17]],
  3: [[7, 7], [12, 12], [17, 17]],
  4: [[7, 7], [17, 7], [7, 17], [17, 17]],
  5: [[7, 7], [17, 7], [12, 12], [7, 17], [17, 17]],
  6: [[7, 7], [7, 12], [7, 17], [17, 7], [17, 12], [17, 17]],
};

export function DiceFace({
  value,
  size = 30,
  fill = '#FFFFFF',
  pip = '#111827',
  border = '#111827',
}: {
  value: number | null;
  size?: number;
  fill?: string;
  pip?: string;
  border?: string;
}) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      <Rect x={1.4} y={1.4} width={21.2} height={21.2} rx={4.6}
        fill={fill} stroke={border} strokeWidth={1.8} />
      {(value ? DIE_PIPS[value] || [] : []).map(([cx, cy], i) => (
        <Circle key={i} cx={cx} cy={cy} r={2.3} fill={pip} />
      ))}
    </Svg>
  );
}

// 2048 竞速入口图标：数字方块 + 合并箭头
const TILE_BG = '#bbada0';
const TILE_2 = '#eee4da';
const TILE_4 = '#ede0c8';
const TILE_8 = '#f2b179';
const TILE_2048 = '#edc22e';

export function Game2048Icon({ size = 40 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={TILE_BG} />
      {/* Tile 2 */}
      <Rect x="10" y="10" width="20" height="20" rx="4" fill={TILE_2} />
      <Text x="20" y="24" textAnchor="middle" fontSize="14" fontWeight="800" fill="#776e65">2</Text>
      {/* Tile 4 */}
      <Rect x="34" y="10" width="20" height="20" rx="4" fill={TILE_4} />
      <Text x="44" y="24" textAnchor="middle" fontSize="14" fontWeight="800" fill="#776e65">4</Text>
      {/* Tile 8 */}
      <Rect x="10" y="34" width="20" height="20" rx="4" fill={TILE_8} />
      <Text x="20" y="48" textAnchor="middle" fontSize="14" fontWeight="800" fill="#f9f6f2">8</Text>
      {/* Tile 2048 */}
      <Rect x="34" y="34" width="20" height="20" rx="4" fill={TILE_2048} />
      <Text x="44" y="47" textAnchor="middle" fontSize="9" fontWeight="800" fill="#f9f6f2">2048</Text>
    </Svg>
  );
}

// 中国象棋入口图标：木色棋盘（中间留河界）+ 红「帅」黑「将」两枚棋子。
// 棋子用汉字而不是图形——象棋的辨识度就在字上，且 SVG 内的 <Text> 属矢量美术。
const XQ_BOARD = '#EBD3A0';
const XQ_LINE = '#6B4F0C';
const XQ_FACE = '#FDF6E3';
const XQ_RED = '#B3261E';
const XQ_BLACK = '#1F1F1F';

export function XiangqiIcon({ size = 44 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={XQ_BOARD} />
      <G stroke={XQ_LINE} strokeWidth="1.4" opacity="0.5">
        <Line x1="18" y1="10" x2="18" y2="54" />
        <Line x1="32" y1="10" x2="32" y2="54" />
        <Line x1="46" y1="10" x2="46" y2="54" />
        <Line x1="10" y1="18" x2="54" y2="18" />
        <Line x1="10" y1="26" x2="54" y2="26" />
        <Line x1="10" y1="38" x2="54" y2="38" />
        <Line x1="10" y1="46" x2="54" y2="46" />
      </G>
      <Circle cx="25" cy="22" r="10.5" fill={XQ_FACE} stroke={XQ_BLACK} strokeWidth="2" />
      <Text x="25" y="27" textAnchor="middle" fontSize="12" fontWeight="700" fill={XQ_BLACK}>将</Text>
      <Circle cx="39" cy="42" r="10.5" fill={XQ_FACE} stroke={XQ_RED} strokeWidth="2" />
      <Text x="39" y="47" textAnchor="middle" fontSize="12" fontWeight="700" fill={XQ_RED}>帅</Text>
    </Svg>
  );
}

// 重力四子棋：立起来的 7×6 棋盘，底部两行黑白交错 + 顶部落入中的一枚红盘。
// 棋子用圆环成形——亮色盘压在亮色底上只有 1.3:1，光靠填色认不出边界。
const C4_FRAME = '#1F3A93';
const C4_SLOT = '#0E1F52';

export function ConnectFourIcon({ size = 40 }: { size?: number }) {
  const slots = [];
  for (let r = 0; r < 6; r += 1) {
    for (let c = 0; c < 7; c += 1) {
      slots.push({ r, c });
    }
  }
  const cx = (c: number) => 9.6 + c * 7.4;
  const cy = (r: number) => 10.4 + r * 7.4;
  const colorOf = (r: number, c: number) => {
    if (r === 4) return c % 2 === 0 ? STONE_BLACK : STONE_WHITE;
    if (r === 5) return c % 2 === 0 ? STONE_WHITE : STONE_BLACK;
    return null;
  };
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={C4_FRAME} />
      <G>
        {slots.map(({ r, c }) => {
          const fill = colorOf(r, c);
          return (
            <Circle
              key={`${r}-${c}`}
              cx={cx(c)}
              cy={cy(r)}
              r="3.1"
              fill={fill ?? C4_SLOT}
              stroke={fill ? '#0B1533' : '#33539E'}
              strokeWidth="0.9"
            />
          );
        })}
      </G>
      {/* 正在落入的一枚：画在顶行上方，示意"从上面掉下来" */}
      <Circle cx={cx(3)} cy={cy(0)} r="3.4" fill="#D8332B" stroke="#0B1533" strokeWidth="1.1" />
    </Svg>
  );
}

// 俄罗斯方块入口图标：深色井字棋盘 + 一摞彩色方块（S/Z/L/I 四个形状拼成）。
// 每格描深墨边：同色相邻（I 的四连）不描边会糊成一条，看不出是「一格一格」——
// 与对局屏同一条理由（见 theme.ts 的 TETRIS_EDGE 注释）。
const TT_BOARD = '#111827';
const TT_EDGE = '#0B1220';

export function TetrisIcon({ size = 40 }: { size?: number }) {
  const cell = 7;
  const originX = 12;
  const originY = 10;
  // [列, 行, 颜色]，行 0 在最上面
  const blocks: [number, number, string][] = [
    [0, 1, '#3B82F6'], [1, 1, '#3B82F6'], [2, 1, '#3B82F6'], [3, 1, '#3B82F6'],
    [0, 0, '#22D3EE'],
    [1, 0, '#A855F7'], [2, 0, '#A855F7'], [3, 0, '#EF4444'],
    [2, 2, '#22C55E'], [3, 2, '#22C55E'], [1, 2, '#EF4444'],
    [0, 2, '#22C55E'], [0, 3, '#FACC15'], [1, 3, '#FACC15'],
    [2, 3, '#F97316'], [3, 3, '#F97316'],
  ];
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={TT_BOARD} />
      <Rect x="9" y="7" width="46" height="46" rx="3" fill="#1F2937" />
      <G>
        {blocks.map(([c, r, fill]) => (
          <Rect
            key={`${c}-${r}`}
            x={originX + c * cell}
            y={originY + r * cell}
            width={cell}
            height={cell}
            fill={fill}
            stroke={TT_EDGE}
            strokeWidth="1"
          />
        ))}
      </G>
    </Svg>
  );
}

// ── UNO ──────────────────────────────────────────────────────


/** 游戏入口图标：三张扇形摊开的牌压在深色底上。 */
export function UnoIcon({ size = 40 }: { size?: number }) {
  // [水平偏移, 牌面颜色]；中间的牌正对、两侧各偏一点并轻微旋转
  const cards: [number, string][] = [
    [-11, unoColors.G], [0, unoColors.B], [11, unoColors.R],
  ];
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={unoWild} />
      {cards.map(([dx, fill]) => (
        <G key={fill} transform={`rotate(${dx * 0.55} 32 36)`}>
          <Rect
            x={32 + dx - 8}
            y="15"
            width="16"
            height="42"
            rx="3.5"
            fill={fill}
            stroke={unoInk}
            strokeWidth="1.2"
          />
        </G>
      ))}
      {/* 中央的奶油色椭圆——UNO 牌的招牌 */}
      <Ellipse cx="32" cy="36" rx="8.5" ry="5.5" fill={unoOnWild} />
    </Svg>
  );
}

/**
 * 颜色徽标 —— 「颜色不能单独承载信息」（WCAG 1.4.1）的第二通道。
 *
 * UNO 里颜色**就是**游戏数据（红牌只能跟红牌），对色觉障碍玩家来说，纯色块
 * 分不出红与绿。所以每种颜色配一个专属形状，画在牌角，选色弹窗里也用
 * 「色块 + 徽标 + 颜色名」三条通道：
 * 红=圆 / 蓝=方 / 绿=三角 / 黄=星 / 黑（未定色）=菱形。
 */
export function UnoColorBadge({
  color, size = 13, fill,
}: { color: string; size?: number; fill?: string }) {
  const c = fill || unoInk;
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24">
      {color === 'R' && <Circle cx="12" cy="12" r="9" fill={c} />}
      {color === 'B' && <Rect x="3" y="3" width="18" height="18" rx="2.5" fill={c} />}
      {color === 'G' && <Polygon points="12,2 22,21.5 2,21.5" fill={c} />}
      {color === 'Y' && (
        <Polygon
          points="12,1 15.1,8.6 23,9 16.9,14.1 19,22 12,17.4 5,22 7.1,14.1 1,9 8.9,8.6"
          fill={c}
        />
      )}
      {color === 'W' && <Polygon points="12,2 22,12 12,22 2,12" fill={c} />}
    </Svg>
  );
}

/**
 * 牌面符号：跳过 ⊘ / 反转 ⇄ / +2 / +4。
 *
 * 形状本身就是第二通道（不靠颜色区分是哪种功能牌）。字形默认深墨——实测四色
 * 牌面上深墨字全部达标（红 3.74 / 绿 5.14 / 蓝 4.33 / 黄 11.68），而**白字在
 * 黄牌上只有 1.52**；黑牌上调用方要传 `fill={unoOnWild}`（深墨压黑牌 1.21）。
 */
export function UnoSymbolGlyph({
  kind, size = 40, fill = unoInk,
}: { kind: string; size?: number; fill?: string }) {
  const w = 3.2;
  if (kind === 'S') {
    // 跳过：一个圆加一道斜杠
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Circle cx="16" cy="16" r="11" stroke={fill} strokeWidth={w} fill="none" />
        <Line x1="8.5" y1="23.5" x2="23.5" y2="8.5"
              stroke={fill} strokeWidth={w} strokeLinecap="round" />
      </Svg>
    );
  }
  if (kind === 'R') {
    // 反转：两支反向箭头
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Path d="M9,9 L23,9 L23,5.5 L28,10.5 L23,15.5 L23,12 L9,12 Z" fill={fill} />
        <Path d="M23,20 L9,20 L9,16.5 L4,21.5 L9,26.5 L9,23 L23,23 Z" fill={fill} />
      </Svg>
    );
  }
  if (kind === 'SE') {
    // 跳过所有人：圆 + **两道交叉斜杠**。普通跳过牌只有一道 —— 在小到 5px 的
    // 角标上，「一道 / 两道」是唯一还分得出来的差别（细节越多越是噪点）。
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Circle cx="16" cy="16" r="11" stroke={fill} strokeWidth={w} fill="none" />
        <Line x1="8.5" y1="23.5" x2="23.5" y2="8.5"
              stroke={fill} strokeWidth={w} strokeLinecap="round" />
        <Line x1="8.5" y1="8.5" x2="23.5" y2="23.5"
              stroke={fill} strokeWidth={w} strokeLinecap="round" />
      </Svg>
    );
  }
  if (kind === 'DA') {
    // 弃同色：两张错开的牌 + 一枚向下的箭头（牌往下走 = 丢进弃牌堆）
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Rect x="10" y="4" width="14" height="15" rx="2"
              fill="none" stroke={fill} strokeWidth={2} opacity={0.55} />
        <Rect x="5" y="8" width="14" height="15" rx="2"
              fill="none" stroke={fill} strokeWidth={2.4} />
        <Path d="M12,25 L12,29 M8.5,26 L12,29.5 L15.5,26" stroke={fill}
              strokeWidth={2.6} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </Svg>
    );
  }
  if (kind === 'TP') {
    // 传给队友（Wild Team Play）：一个圆点 → 箭头 → 另一个圆点。
    // **两个点**是这张牌的辨识特征 —— 小到 5px 的角标上只剩「数得出几个东西」。
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Circle cx="6" cy="16" r="4" fill={fill} />
        <Circle cx="26" cy="16" r="4" fill={fill} />
        <Path d="M11,16 L20,16 M16.5,11.5 L21,16 L16.5,20.5" stroke={fill}
              strokeWidth={w} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </Svg>
    );
  }
  if (kind === 'TD4') {
    // 全队罚四（Wild Team Draw Four）：**两支向下的箭头**（对手一整队都要摸牌）。
    // 与「弃同色」的区分是「没有牌形、只有两支箭头」—— 同上，数量才是认得出的差别。
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Path d="M10,4 L10,21 M5.5,16.5 L10,22 L14.5,16.5" stroke={fill}
              strokeWidth={w} fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <Path d="M22,4 L22,21 M17.5,16.5 L22,22 L26.5,16.5" stroke={fill}
              strokeWidth={w} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </Svg>
    );
  }
  if (kind === '+RO') {
    // 颜色轮盘：一个**四格转盘**（交替实心/空心 + 中心轴孔）——「四个颜色转一转」
    // 正是这条规则。圆形轮廓与纯变色牌那个方形风车一眼分得开。
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Path d="M16,16 L16,3 A13,13 0 0,1 29,16 Z" fill={fill} />
        <Path d="M16,16 L29,16 A13,13 0 0,1 16,29 Z" fill={fill} opacity={0.5} />
        <Path d="M16,16 L16,29 A13,13 0 0,1 3,16 Z" fill={fill} />
        <Path d="M16,16 L3,16 A13,13 0 0,1 16,3 Z" fill={fill} opacity={0.5} />
        <Circle cx="16" cy="16" r="3.2" fill="none" stroke={fill} strokeWidth="2.4" />
      </Svg>
    );
  }
  if (kind === '+RD4') {
    // 变色反转 +4：上面两支反向箭头（沿用反转牌那对），下面一个 +4
    return (
      <Svg width={size} height={size} viewBox="0 0 32 32">
        <Path d="M7,7 L21,7 L21,4.5 L25,8.5 L21,12.5 L21,10 L7,10 Z" fill={fill} />
        <Path d="M25,15 L11,15 L11,12.5 L7,16.5 L11,20.5 L11,18 L25,18 Z" fill={fill} />
        <Text x="16" y="29" fontSize="11" fontWeight="bold" fill={fill} textAnchor="middle">
          +4
        </Text>
      </Svg>
    );
  }
  // +2 / +4 / +6 / +10：一张小牌 + 数字（真实 UNO 是叠起来的牌堆，这里抽象成单张更好认）
  const label = kind.startsWith('+') ? kind : '+2';
  const labelSize = label.length > 2 ? 11 : 15;   // "+10" 得压一档才塞得下
  return (
    <Svg width={size} height={size} viewBox="0 0 32 32">
      <Rect x="4" y="6" width="16" height="20" rx="2.5"
            fill="none" stroke={fill} strokeWidth={2} />
      <Text x="23" y="24" fontSize={labelSize} fontWeight="bold" fill={fill} textAnchor="middle">
        {label}
      </Text>
    </Svg>
  );
}

// ── 深空休眠（stasis） ────────────────────────────────────────
//
// 全部手写矢量，颜色一律取 theme.ts 的 stasis* 令牌（本组图标不写死 hex）。
// 三条约束贯穿这一组：
// ① **亮传信息、暗做装饰**（设计文档 §4.1）——承载信息的门/静默体/建筑/舱体一律提亮，
//    深空黑只当底。「暗色调下深色元素压深色底」永远是对比度更低，不是更高。
// ② **颜色不能单独承载信息**（WCAG 1.4.1）——门等级靠材质纹理（铆钉/加强筋/发光镶边）、
//    静默体状态靠体型与轮廓（团块/内收触须/放射尖刺），颜色只是氛围。
// ③ 图标会渲染到 28-44px：**轮廓要粗、细节要少**，密集装饰在这个尺寸下只是噪点。

/**
 * 游戏入口图标：圆形气密门。
 *
 * 构图照概念参考稿（`docs/design/深空休眠/2026-10-12/seedream_1791789509_1.jpg`）——
 * 圆形门体 + 八根辐射加强筋 + 中央观察窗 + 青蓝霓虹外环 + 一侧橙色警戒光。
 * 参考稿的门体是暗的，这里反过来用**亮门体**（`stasisDoorAlloy`）压深空黑：
 * 门体暗、底也暗，28px 下会整块糊掉；亮盘 + 深墨辐条才是这个尺寸读得出来的对比。
 */
export function StasisIcon({ size = 44 }: { size?: number }) {
  // 一组「十」字辐条（竖 + 横 = 四根臂）带端部卡扣；整组再转 45° 得到另外四根
  const arms = (
    <G>
      <Line x1="32" y1="17" x2="32" y2="47" stroke={stasisDoorEdge} strokeWidth="3" strokeLinecap="round" />
      <Line x1="17" y1="32" x2="47" y2="32" stroke={stasisDoorEdge} strokeWidth="3" strokeLinecap="round" />
      <Rect x="28.6" y="13" width="6.8" height="4.4" rx="1.2" fill={stasisDoorEdge} />
      <Rect x="28.6" y="46.6" width="6.8" height="4.4" rx="1.2" fill={stasisDoorEdge} />
      <Rect x="13" y="28.6" width="4.4" height="6.8" rx="1.2" fill={stasisDoorEdge} />
      <Rect x="46.6" y="28.6" width="4.4" height="6.8" rx="1.2" fill={stasisDoorEdge} />
    </G>
  );
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={stasisSpace} />
      <Circle cx="32" cy="32" r="24" fill={stasisDoorAlloy} stroke={stasisTurret} strokeWidth="2.6" />
      {/* 一侧的橙色警戒光：与霓虹环同半径，把青环截成一段 C（参考稿的构图） */}
      <Path d="M 52.2 19 A 24 24 0 0 1 52.2 45" fill="none" stroke={stasisAlert}
            strokeWidth="2.6" strokeLinecap="round" />
      <G transform="rotate(45 32 32)">{arms}</G>
      {arms}
      {/* 中央观察窗：深墨环 + 霓虹玻璃 + 高光核心 */}
      <Circle cx="32" cy="32" r="9" fill={stasisDoorEdge} />
      <Circle cx="32" cy="32" r="6.6" fill={stasisTurret} />
      <Circle cx="32" cy="32" r="3.2" fill={stasisPod} />
    </Svg>
  );
}

// 军棋入口图标：木色棋盘 + 铁路网（外框 + 两条横线 + 中路短竖）+ 两枚棋子。
// 棋子用「奶油面 + 本方色描边」成形（亮面压在木色底上只有 1.3:1，同象棋）。
const LZQ_BOARD = '#E3D3AE';
const LZQ_RAIL = '#6B5B3E';
const LZQ_FACE = '#FDF6E3';
const LZQ_RED = '#B3261E';
const LZQ_BLACK = '#1F1F1F';

export function LuzhanqiIcon({ size = 44 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      <Rect x="4" y="4" width="56" height="56" rx="12" fill={LZQ_BOARD} />
      <G stroke={LZQ_RAIL} strokeWidth="2.6" fill="none">
        <Rect x="16" y="9" width="32" height="46" rx="2.5" />
        <Line x1="16" y1="25" x2="48" y2="25" />
        <Line x1="16" y1="39" x2="48" y2="39" />
        <Line x1="32" y1="25" x2="32" y2="39" />
      </G>
      <G strokeWidth="1.5">
        <Rect x="19.5" y="13.5" width="12" height="9" rx="2" fill={LZQ_FACE} stroke={LZQ_BLACK} />
        <Rect x="33" y="41" width="12" height="9" rx="2" fill={LZQ_FACE} stroke={LZQ_RED} />
      </G>
      <Text x="25.5" y="20.4" textAnchor="middle" fontSize="7" fontWeight="700" fill={LZQ_BLACK}>司</Text>
      <Text x="39" y="47.9" textAnchor="middle" fontSize="7" fontWeight="700" fill={LZQ_RED}>旗</Text>
    </Svg>
  );
}

// 保持 INDIGO 常量导出，供未来图标配色引用
export const ICON_COLORS = {
  INDIGO, BOARD, BOARD_LIGHT, BOARD_LINE, STONE_BLACK, STONE_WHITE,
  XQ_BOARD, XQ_LINE, XQ_FACE, XQ_RED, XQ_BLACK,
  C4_FRAME, C4_SLOT,
  LZQ_BOARD, LZQ_RAIL, LZQ_FACE, LZQ_RED, LZQ_BLACK,
};
