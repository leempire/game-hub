/**
 * 深空休眠的**整格矢量贴图**（一格一张，`viewBox="0 0 44 44"`）。
 *
 * 为什么单独一个文件：`StasisScreen.tsx` 已经两千多行，而这一屏的美术量还在涨
 * （单位、机器、四座矿、弹道特效）。贴图是**纯展示**、无状态、无回调，抽出来
 * 既给主组件瘦身，也让「贴图长什么样」有一个能整体替换的地方。
 *
 * ── 三条画法约定（来自俯视 2D 可读性调研，`增强调研.md` §10.2）──────────
 *
 * 1. **剪影优先**。俯视视角下玩家看到的是「顶面 + 一点点侧面」，44px 的格子里
 *    细节全糊掉，能区分的只有轮廓。所以每种单位给一个**独一无二的外轮廓**，
 *    而不是同一个方框里换一个小图标。
 * 2. **明度分组优先于色相**。墙 / 地板 / 机器 / 单位四组在灰度下也要分得开 ——
 *    所以每个贴图都有「深色底座 + 亮色主体」两层，底座把主体从地板上托起来。
 * 3. **轮廓靠线，不靠填充**。这条本项目在俄罗斯方块上踩过（把空位填充调亮，
 *    结果整块盘面变成一片浅灰、格子之间依旧没有分界）。所有贴图一律带深墨描边
 *    `stasisInk`，宽 1.4~2。
 *
 * 光源统一在**左上**（俯视 2D 的通用约定），所以高光永远画在左上角、
 * 阴影永远落在右下角。所有贴图共用这一点，整张地图才像同一个光源照出来的。
 *
 * ── 有意不做的两件事 ────────────────────────────────────────
 *
 * - **炮塔的炮管不跟着目标转**。看着更帅，但朝向每 tick 都在变，就得把整层贴图
 *   （可能几十座塔）从 `memo` 里踢出去、每 tick 重建 —— 代价远大于收益。
 *   方向改由**枪口焰与弹丸**表达（它们本来就指向目标），而炮塔本身画成
 *   **对称的环形发射器**：没有炮管就没有「指错方向」这回事。
 * - **贴图里不写等级数字**。等级由屏幕层的小徽标画（`LevelBadge`），
 *   这样数字能随主题走、能被视觉审计看见，也不必给 13 个等级各画一张图。
 */

import React from 'react';
import { Svg, Circle, Ellipse, G, Path, Rect } from 'react-native-svg';
import {
  stasisAlert, stasisAlloy, stasisBio, stasisCopper, stasisDiamond,
  stasisDoorAlloy, stasisDoorCivil, stasisDoorEdge, stasisDoorMil,
  stasisAccel, stasisBinder, stasisCryo, stasisEntity, stasisEntityOverload,
  stasisEntityRetreat, stasisGold, stasisInk, stasisMuzzle, stasisPod,
  stasisRefinery, stasisRepair, stasisShield, stasisSilver, stasisTracer,
  stasisTurret, stasisWallBevel,
} from './theme';

/** 一格。所有贴图共用这个坐标系，屏幕尺寸由调用方给。 */
const TILE = 44;

/** 贴图的公共底座：把主体从地板上「托起来」的一层深色垫。 */
function BasePlate({ inset = 3, r = 7 }: { inset?: number; r?: number }) {
  return (
    <>
      <Rect x={inset} y={inset + 1.5} width={TILE - inset * 2} height={TILE - inset * 2}
            rx={r} fill={stasisInk} opacity="0.5" />
      <Rect x={inset} y={inset} width={TILE - inset * 2} height={TILE - inset * 2}
            rx={r} fill={stasisWallBevel} opacity="0.22"
            stroke={stasisInk} strokeWidth="1.6" />
    </>
  );
}

// ── 哨戒炮 ──────────────────────────────────────────────────

/**
 * 哨戒炮：八边形装甲基座 + 环形发射器 + 四枚发射极 + 中央等离子核心。
 *
 * **没有炮管是有意的**（见文件头）：对称的环形发射器不需要朝向，而
 * 「谁在开火、打哪儿」由枪口焰与弹丸表达 —— 那两样本来就指向目标。
 * 等级体现在**发射极的点亮数量**（1-3 级亮 1 枚、4-6 级亮 2 枚……最多 4 枚）
 * 与核心的大小上，是一个不需要读数字就能扫一眼的通道。
 */
export function TurretSprite({ level = 1, size = TILE }: { level?: number; size?: number }) {
  const lit = Math.min(4, Math.ceil(level / 3.5));
  const core = 3.4 + Math.min(2.2, level * 0.24);
  const prongs: [number, number][] = [[22, 6.5], [37.5, 22], [22, 37.5], [6.5, 22]];
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={2.5} r={6} />
      {/* 八边形装甲基座（切角 = 与「方箱」机器的轮廓分开） */}
      <Path
        d="M13 6 H31 L38 13 V31 L31 38 H13 L6 31 V13 Z"
        fill={stasisInk} opacity="0.55"
      />
      <Path
        d="M13.6 8 H30.4 L36 13.6 V30.4 L30.4 36 H13.6 L8 30.4 V13.6 Z"
        fill={stasisTurret} fillOpacity="0.22" stroke={stasisInk} strokeWidth="1.6"
      />
      {/* 发射极：未点亮的是深墨短柱，点亮的加白热端头 */}
      {prongs.map(([cx, cy], i) => (
        <G key={i}>
          <Circle cx={cx} cy={cy} r="4.6" fill={stasisInk} opacity="0.6" />
          <Circle cx={cx} cy={cy} r={i < lit ? 3.2 : 2.2}
                  fill={i < lit ? stasisTurret : stasisWallBevel}
                  opacity={i < lit ? 1 : 0.5} />
          {i < lit ? <Circle cx={cx - 0.8} cy={cy - 0.8} r="1.1" fill={stasisMuzzle} /> : null}
        </G>
      ))}
      {/* 环形发射器 + 等离子核心（左上高光） */}
      <Circle cx="22" cy="22" r="10.5" fill={stasisInk} opacity="0.65" />
      <Circle cx="22" cy="22" r="9" fill={stasisTurret} fillOpacity="0.35"
              stroke={stasisInk} strokeWidth="1.4" />
      <Circle cx="22" cy="22" r={core} fill={stasisTurret} />
      <Circle cx="22" cy="22" r={core * 0.45} fill={stasisMuzzle} />
      <Path d="M15.5 17.5 Q17 14.6 20 13.6" fill="none" stroke={stasisMuzzle}
            strokeWidth="1.4" strokeLinecap="round" opacity="0.75" />
    </Svg>
  );
}

// ── 精炼机 ──────────────────────────────────────────────────

/** 精炼机：卧式圆罐 + 右侧控制箱 + 顶部排气口。等级体现在**排气口的档位数**。 */
export function RefinerySprite({ level = 1, size = TILE }: { level?: number; size?: number }) {
  const vents = Math.min(4, level);
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      {/* 卧式罐体：圆头矩形，与炮塔的八边形、矿的晶簇都不同 */}
      <Rect x="6.5" y="13" width="24" height="18" rx="9"
            fill={stasisRefinery} fillOpacity="0.9" stroke={stasisInk} strokeWidth="1.7" />
      {/* 罐体高光（左上）+ 箍带 */}
      <Path d="M11 17.5 Q13.5 15.2 17 15" fill="none" stroke={stasisMuzzle}
            strokeWidth="1.6" strokeLinecap="round" opacity="0.8" />
      <Rect x="14" y="13" width="2.4" height="18" fill={stasisInk} opacity="0.28" />
      <Rect x="21" y="13" width="2.4" height="18" fill={stasisInk} opacity="0.28" />
      {/* 控制箱 */}
      <Rect x="28.5" y="17" width="9" height="15" rx="2.2"
            fill={stasisRefinery} stroke={stasisInk} strokeWidth="1.6" />
      <Circle cx="33" cy="21.5" r="1.9" fill={stasisBio} />
      <Rect x="30.6" y="26.5" width="4.8" height="2.4" rx="1.2" fill={stasisInk} opacity="0.45" />
      {/* 排气口：亮起的档位数 = 等级（第二通道，不读数字也看得出） */}
      {[0, 1, 2, 3].map((i) => (
        <Rect key={i} x={9 + i * 4.6} y="6.5" width="3" height="4.4" rx="1.4"
              fill={i < vents ? stasisBio : stasisInk}
              opacity={i < vents ? 0.95 : 0.35} />
      ))}
    </Svg>
  );
}

// ── 纳米修复器 ──────────────────────────────────────────────

/** 纳米修复器：圆形修复台 + 中央十字 + 外圈刻度。无等级（原型也没有升级档）。 */
export function RepairSprite({ size = TILE }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      <Circle cx="22" cy="22" r="13.5" fill={stasisInk} opacity="0.55" />
      <Circle cx="22" cy="22" r="12" fill={stasisRepair} fillOpacity="0.28"
              stroke={stasisInk} strokeWidth="1.6" />
      {/* 外圈刻度：让它读作「仪器」而不是一个色圆 */}
      {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
        <Rect key={a} x="21.2" y="8.2" width="1.6" height="3.4" rx="0.8"
              fill={stasisRepair} opacity="0.75"
              transform={`rotate(${a} 22 22)`} />
      ))}
      {/* 十字：带深墨描边，压在任何底色上都成形 */}
      <Path d="M19.4 13.5 H24.6 V19.4 H30.5 V24.6 H24.6 V30.5 H19.4 V24.6 H13.5 V19.4 H19.4 Z"
            fill={stasisRepair} stroke={stasisInk} strokeWidth="1.5" strokeLinejoin="round" />
      <Circle cx="22" cy="22" r="2.4" fill={stasisMuzzle} opacity="0.85" />
    </Svg>
  );
}

// ── 控场四件（v13）─────────────────────────────────────────
//
// 四件都不打伤害，所以**轮廓必须和炮台/机器差得远**：它们是「仪器」，不是「武器」。
// 四张图各自给一个独一无二的外形，颜色只是第二通道。

/**
 * 应急力场：一个**六边形力场罩**（罩在门上那一层）。
 *
 * 六边而不是圆形 —— 圆在 44px 下和纳米修复器的圆盘读起来一样，而六边是
 * 「力场/护盾」在科幻视觉里的通行形状，和炮台的圆环、修复器的圆盘都分得开。
 * 中间那圈亮环是它的「已经充能」暗示，罩子本身在门那一层另画（见 `StasisBoard`）。
 */
export function ShieldSprite({ size = TILE }: { size?: number }) {
  const hex = 'M22 6 L35 13.5 V30.5 L22 38 L9 30.5 V13.5 Z';
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      <Path d={hex} fill={stasisShield} fillOpacity="0.22"
            stroke={stasisInk} strokeWidth="1.6" strokeLinejoin="round" />
      <Path d="M22 12.5 L30.5 17.4 V27.6 L22 32.5 L13.5 27.6 V17.4 Z"
            fill={stasisShield} fillOpacity="0.55"
            stroke={stasisInk} strokeWidth="1.4" strokeLinejoin="round" />
      {/* 中心的三棱芯：力场发生器的读法 */}
      <Path d="M22 20 L25.6 26.2 H18.4 Z" fill={stasisMuzzle}
            stroke={stasisInk} strokeWidth="1.2" strokeLinejoin="round" />
    </Svg>
  );
}

/**
 * 束缚弹射器：两只**张开的叉臂**（一左一右）+ 中间的发射口。
 *
 * 形状语言是「夹住」—— 与炮台的环形发射器完全不同的剪影。叉臂尖端各一颗
 * 亮点，读作「待发的束缚束」。
 */
export function BinderSprite({ size = TILE }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      {/* 底座横梁 */}
      <Rect x="10" y="28" width="24" height="8" rx="3"
            fill={stasisBinder} fillOpacity="0.5"
            stroke={stasisInk} strokeWidth="1.5" />
      {/* 两只叉臂 */}
      {[-1, 1].map((sgn) => (
        <G key={sgn}>
          <Path d={`M${22 + sgn * 4} 29 L${22 + sgn * 12} 12 L${22 + sgn * 15.5} 13.5 L${22 + sgn * 8} 29.5 Z`}
                fill={stasisBinder} stroke={stasisInk} strokeWidth="1.5"
                strokeLinejoin="round" />
          <Circle cx={22 + sgn * 13.6} cy={12.6} r="2.6" fill={stasisMuzzle}
                  stroke={stasisInk} strokeWidth="1.1" />
        </G>
      ))}
      {/* 发射口 */}
      <Rect x="19.4" y="19" width="5.2" height="10" rx="2.4"
            fill={stasisInk} opacity="0.75" />
      <Rect x="20.4" y="20" width="3.2" height="8" rx="1.6" fill={stasisBinder} />
    </Svg>
  );
}

/**
 * 低温阱：一枚**六角冰晶**架在两根支柱上。
 *
 * 冰晶（六向对称的枝杈）在俯视下非常好认 —— 全图只有它长这样。
 */
export function CryoSprite({ size = TILE }: { size?: number }) {
  const arms = [0, 60, 120, 180, 240, 300];
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      <G stroke={stasisCryo} strokeWidth="2.4" strokeLinecap="round">
        {arms.map((a) => (
          <Path key={a} d="M22 22 V9.5" transform={`rotate(${a} 22 22)`} />
        ))}
      </G>
      {/* 枝杈：每根主枝两侧各一小段，读作「霜」而不是「星」 */}
      <G stroke={stasisCryo} strokeWidth="1.6" strokeLinecap="round" opacity="0.85">
        {arms.map((a) => (
          <Path key={a} d="M22 13 L18.6 15.8 M22 13 L25.4 15.8"
                transform={`rotate(${a} 22 22)`} />
        ))}
      </G>
      <Circle cx="22" cy="22" r="4.4" fill={stasisCryo}
              stroke={stasisInk} strokeWidth="1.5" />
      <Circle cx="20.6" cy="20.6" r="1.5" fill={stasisMuzzle} opacity="0.8" />
    </Svg>
  );
}

/**
 * 粒子加速器：一圈**闭合的环形轨道** + 环上三个加速腔。
 *
 * 与炮台的区别：炮台是「环 + 中心发射口」，它是「环 + 环上节点」，
 * 而且环明显更粗、带一圈方向箭头 —— 读作「在转」。
 */
export function AcceleratorSprite({ size = TILE }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      <Circle cx="22" cy="22" r="13" fill="none"
              stroke={stasisAccel} strokeWidth="4.6" opacity="0.55" />
      <Circle cx="22" cy="22" r="13" fill="none"
              stroke={stasisInk} strokeWidth="1.3" opacity="0.7" />
      {/* 三个加速腔（环上的节点） */}
      {[90, 210, 330].map((a) => (
        <Rect key={a} x="19.8" y="6.4" width="4.4" height="6" rx="1.6"
              fill={stasisAccel} stroke={stasisInk} strokeWidth="1.2"
              transform={`rotate(${a} 22 22)`} />
      ))}
      {/* 中心：被加速的粒子（亮点 + 两道尾迹） */}
      <Circle cx="22" cy="22" r="3.4" fill={stasisMuzzle}
              stroke={stasisInk} strokeWidth="1.2" />
      <G stroke={stasisMuzzle} strokeWidth="1.3" strokeLinecap="round" opacity="0.75">
        <Path d="M14 22 H18.4 M25.6 22 H30" />
      </G>
    </Svg>
  );
}

// ── 四座矿 ──────────────────────────────────────────────────

const MINE_TINT = [stasisCopper, stasisSilver, stasisGold, stasisDiamond] as const;

/**
 * 矿：一簇水晶。**档位同时改颜色与形状**（WCAG 1.4.1）：
 * 晶体的**根数**（3 → 3+主晶 → 4 → 5）与**高度**逐档增长，
 * 高两档还多一圈辉光。所以灰度下也分得清铜/银/金/钻石。
 */
export function MineSprite({ tier = 0, size = TILE, premium = false }:
  { tier?: number; size?: number; premium?: boolean }) {
  const t = Math.max(0, Math.min(3, tier));
  // **氪金矿**（钻石矿的 2 级）多一圈金火花：不新开一个配色，靠**形状通道**
  // 与钻石矿分开（同「颜色不单独承载信息」那条）。名字与产率仍由弹窗与徽标给。
  const tint = MINE_TINT[t];
  // [中心 x, 底部 y, 宽, 高]；根数与高度随档位增长
  const shards: [number, number, number, number][] = [
    [22, 34, 9, 15],
    [13.5, 34, 7, 11 + t],
    [30.5, 34, 7, 11 + t],
  ];
  if (t >= 1) shards.push([22, 34, 7, 19 + t * 2]);        // 银：多一根主晶
  if (t >= 2) shards.push([17.5, 34, 5.5, 9]);             // 金：再加一根小晶
  if (t >= 3) shards.push([27, 34, 5.5, 12]);              // 钻石：再加一根
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={3} r={6} />
      {t >= 2 ? (
        <Ellipse cx="22" cy="26" rx={13 + t} ry={11 + t} fill={tint} opacity="0.16" />
      ) : null}
      {/* 先画两侧的小晶、再画中间的主晶 —— 主晶压在上面，读作「一簇」 */}
      {[...shards].reverse().map(([cx, by, w, h], i) => (
        <G key={i}>
          <Path d={`M${cx} ${by - h} L${cx + w / 2} ${by - h * 0.55} L${cx + w / 2} ${by} L${cx - w / 2} ${by} L${cx - w / 2} ${by - h * 0.55} Z`}
                fill={tint} stroke={stasisInk} strokeWidth="1.4" strokeLinejoin="round" />
          <Path d={`M${cx} ${by - h} L${cx - w / 2} ${by - h * 0.55} L${cx - w / 2 + 1.2} ${by}`}
                fill="none" stroke={stasisMuzzle} strokeWidth="1.1"
                strokeLinecap="round" opacity="0.65" />
        </G>
      ))}
      {/* 底座：一小块岩体，把晶簇压在地上 */}
      <Path d="M8 34 H36 Q37.5 34 36.5 36.5 L35 39 H9 L7.5 36.5 Q6.5 34 8 34 Z"
            fill={stasisInk} opacity="0.55" />
      {/* 钻石档多两颗火花（形状通道的最后一档） */}
      {t >= 3 ? (
        <G fill={stasisMuzzle}>
          <Path d="M34 11 L35 14 L38 15 L35 16 L34 19 L33 16 L30 15 L33 14 Z" />
          <Path d="M10 17 L10.7 19 L12.7 19.7 L10.7 20.4 L10 22.4 L9.3 20.4 L7.3 19.7 L9.3 19 Z" />
        </G>
      ) : null}
      {premium ? (
        <G fill={stasisGold}>
          <Path d="M38 22 L39 25 L42 26 L39 27 L38 30 L37 27 L34 26 L37 25 Z" />
          <Path d="M6 27 L6.7 29 L8.7 29.7 L6.7 30.4 L6 32.4 L5.3 30.4 L3.3 29.7 L5.3 29 Z" />
        </G>
      ) : null}
    </Svg>
  );
}

// ── 休眠舱 ──────────────────────────────────────────────────

/**
 * 休眠舱：胶囊舱体 + 舱盖 + 内部暖光（产出的就是生物能，所以暖光取 `stasisBio`）。
 *
 * `lit` = 这间舱室有主（灯亮、暖光可见）；`occupied` = 主人正躺在里面
 * （舱盖内侧出现人形剪影 + 舱盖发光）。**这两个状态必须一眼分得出** ——
 * 「队友到底进屋了没有」是本作最要紧的一条状态（v4 起没躺下的人会被抓死）。
 */
export function PodSprite({ lit = false, occupied = false, size = TILE }:
  { lit?: boolean; occupied?: boolean; size?: number }) {
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <BasePlate inset={4} r={6} />
      {/* 胶囊（圆角矩形 = 与八边形炮塔、卧式罐体都不同） */}
      <Rect x="12" y="7" width="20" height="30" rx="10"
            fill={lit ? stasisPod : stasisWallBevel}
            fillOpacity={lit ? 1 : 0.35} stroke={stasisInk} strokeWidth="1.7" />
      {/* 舱盖玻璃 */}
      <Rect x="15" y="10.5" width="14" height="23" rx="7"
            fill={stasisInk} opacity={lit ? 0.72 : 0.55} />
      {lit ? (
        <>
          <Ellipse cx="22" cy="21" rx="4.6" ry="7.2" fill={stasisBio}
                   opacity={occupied ? 0.75 : 0.34} />
          <Circle cx="22" cy="21" r={occupied ? 3.4 : 2.4} fill={stasisBio} />
        </>
      ) : null}
      {/* 躺着的人形剪影：头 + 肩，只在有人时出现 */}
      {occupied ? (
        <G fill={stasisInk} opacity="0.75">
          <Circle cx="22" cy="17.5" r="2.6" />
          <Path d="M17.5 27 Q22 22.5 26.5 27 L26.5 30 H17.5 Z" />
        </G>
      ) : null}
      {/* 舱盖反光（左上） */}
      <Path d="M17.5 15 Q18.5 12.5 20.5 11.8" fill="none" stroke={stasisMuzzle}
            strokeWidth="1.4" strokeLinecap="round" opacity={lit ? 0.85 : 0.4} />
      {/* 状态灯带 */}
      <Rect x="17" y="35.5" width="10" height="2.4" rx="1.2"
            fill={lit ? stasisBio : stasisInk} opacity={lit ? 0.9 : 0.35} />
    </Svg>
  );
}

// ── 气密门 ──────────────────────────────────────────────────

/**
 * 气密门：`26 × 36` 的门体（不是正方形 —— 门嵌在舱壁的缺口里，是个窄条）。
 *
 * **材质纹理随等级推进是必需的、不是装饰**：三级门的相邻色差只有 1.16 / 1.76，
 * 光靠颜色分不出等级。纹理：1-2 级铆钉（4→6 颗）、3-4 级横向加强筋（2→3 道）、
 * 5 级起发光镶边。**门的等级数字与血条由屏幕层画**，贴图里不塞文字。
 */
export function DoorSprite({ level = 1, size = 36 }: { level?: number; size?: number }) {
  const lv = Math.max(1, Math.min(13, level));
  // 五档材质（每 3 级一档），与 `doorMaterial` 的三档底色是同一套分组
  const body = lv >= 11 ? stasisDoorAlloy : lv >= 6 ? stasisDoorMil : stasisDoorCivil;
  const w = 26;
  const h = 36;
  const rivets: [number, number][] = lv >= 2
    ? [[3.6, 4], [22.4, 4], [3.6, 32], [22.4, 32], [3.6, 18], [22.4, 18]]
    : [[3.6, 4], [22.4, 4], [3.6, 32], [22.4, 32]];
  const ribs = [24, 29].slice(0, Math.max(0, lv - 4));
  return (
    <Svg width={(size * w) / h} height={size} viewBox={`0 0 ${w} ${h}`}>
      <Rect x="0" y="0" width={w} height={h} rx="3.4"
            fill={body} stroke={lv >= 5 ? stasisTurret : stasisDoorEdge}
            strokeWidth={lv >= 5 ? 2.6 : 2.2} />
      {/* 观察窗 */}
      <Circle cx="13" cy="14" r="5.6" fill={stasisInk} />
      <Circle cx="13" cy="14" r="3.9" fill={stasisTurret} />
      <Circle cx="13" cy="14" r="1.7" fill={stasisPod} />
      {lv <= 5 ? (
        <G fill={stasisInk} opacity="0.6">
          {rivets.map(([cx, cy]) => <Circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.4" />)}
        </G>
      ) : null}
      {lv >= 6 && lv <= 10 ? (
        <G fill={stasisInk} opacity="0.6">
          {ribs.map((y) => <Rect key={y} x="3" y={y} width="20" height="2.4" rx="1.2" />)}
        </G>
      ) : null}
      {lv >= 11 ? (
        <Rect x="2.2" y="2.2" width={w - 4.4} height={h - 4.4} rx="2.2" fill="none"
              stroke={stasisTurret} strokeWidth="1.6" opacity="0.6" />
      ) : null}
      {/* 左上高光（与其它贴图同一光源） */}
      <Path d="M3 8 Q3 3 8 3" fill="none" stroke={stasisMuzzle}
            strokeWidth="1.3" strokeLinecap="round" opacity="0.45" />
    </Svg>
  );
}

// ── 角色与气闸 ──────────────────────────────────────────────

/**
 * 玩家角色（俯视小人）：**肩（横椭圆）+ 亮头盔**。
 *
 * 原来是一块圆角矩形加一个小圆（正面视角），俯视地图上读作「一根柱子」。
 * 第一版改成「色环 + 深墨圆心」之后又踩了一个坑：**深色圆心 + 彩色外环会被
 * 读成一个甜甜圈**，而不是一个人。俯视视角下人的辨识特征是「**亮头盔压在
 * 肩甲上**」——所以头盔用 `stasisPod`（亮色）而不是深墨，肩用身份色横椭圆。
 *
 * 身份色仍然是最主要的信息通道（肩甲面积最大），头盔只是让它读成「人」。
 * `lying` 转 72°：椭圆肩甲一转就明显不是站姿了（圆的甜甜圈转多少度都一样）。
 */
export function HeroSprite({ color, lying = false, size = 30 }:
  { color: string; lying?: boolean; size?: number }) {
  const s = 44;
  return (
    <Svg width={size} height={size * 1.05} viewBox={`0 0 ${s} ${s}`}
         style={{ transform: [{ rotate: lying ? '72deg' : '0deg' }] } as any}>
      {/* 落地阴影（右下）：俯视 2D 的通用约定，光源在左上 */}
      <Ellipse cx="24" cy="25" rx="11" ry="8.5" fill={stasisInk} opacity="0.5" />
      {/* 肩甲：**横椭圆**（不是圆）—— 俯视看人是「宽肩」，而且一转就看得出来 */}
      <Ellipse cx="22" cy="22" rx="11.5" ry="9.5" fill={color}
               stroke={stasisInk} strokeWidth="1.9" />
      {/* 肩甲上的两道分隔：把「左右肩」读出来，而不是一整块色块 */}
      <Path d="M22 13.2 L22 30.8" stroke={stasisInk} strokeWidth="1.2" opacity="0.35" />
      {/* 头盔：亮玻璃 + 深墨圈口（**亮头盔压肩甲**才是俯视下「人」的形状） */}
      <Circle cx="22" cy="21" r="6.2" fill={stasisInk} />
      <Circle cx="22" cy="21" r="4.8" fill={stasisPod} />
      {/* 面罩反光（左上） */}
      <Path d="M19.4 19.6 Q20 17.4 22 16.8" fill="none" stroke={stasisAlloy}
            strokeWidth="1.4" strokeLinecap="round" opacity="0.9" />
    </Svg>
  );
}

/** 气闸（静默体的入场口）：黄黑警示条 + 中央闸门。 */
export function AirlockSprite({ size = TILE }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox={`0 0 ${TILE} ${TILE}`}>
      <Rect x="1" y="1" width="42" height="42" rx="4" fill={stasisInk} opacity="0.7" />
      <Rect x="3" y="3" width="38" height="38" rx="3"
            fill={stasisDoorCivil} fillOpacity="0.3" stroke={stasisDoorCivil} strokeWidth="1.8" />
      {/* 斜向警示条 */}
      <G opacity="0.75">
        {[0, 1, 2, 3].map((i) => (
          <Rect key={i} x={-6 + i * 12} y="-10" width="5" height="64"
                fill={stasisDoorCivil} transform="rotate(45 22 22)" />
        ))}
      </G>
      <Circle cx="22" cy="22" r="7.5" fill={stasisInk} opacity="0.75" />
      <Circle cx="22" cy="22" r="5.4" fill="none" stroke={stasisDoorCivil} strokeWidth="2" />
    </Svg>
  );
}

// ── 弹道特效 ────────────────────────────────────────────────

/** 枪口焰：90ms 的一次性星芒（惯例 60-100ms，「长到能看见，短到来不及看清」）。 */
export function MuzzleFlash({ size = 26 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 44 44">
      <Path d="M22 2 L25 17 L40 22 L25 27 L22 42 L19 27 L4 22 L19 17 Z"
            fill={stasisMuzzle} opacity="0.9" />
      <Path d="M22 9 L23.6 19.4 L34 22 L23.6 24.6 L22 35 L20.4 24.6 L10 22 L20.4 19.4 Z"
            fill={stasisTracer} />
      <Circle cx="22" cy="22" r="3.6" fill={stasisPod} />
    </Svg>
  );
}

/**
 * 弹丸：一颗带拖尾的能量弹。**拖尾朝后**（`-x` 方向），由父层旋转到飞行方向 ——
 * 拖尾是「往哪儿飞」这条信息的通道，比弹丸本身更重要。
 */
export function BulletSprite({ size = 14 }: { size?: number }) {
  return (
    <Svg width={size} height={size * 0.6} viewBox="0 0 24 14">
      <Path d="M0 7 L10 3.4 L10 10.6 Z" fill={stasisTracer} opacity="0.55" />
      <Ellipse cx="16" cy="7" rx="6" ry="4.2" fill={stasisMuzzle} />
      <Ellipse cx="16" cy="7" rx="6" ry="4.2" fill="none" stroke={stasisTracer} strokeWidth="1.4" />
      <Circle cx="17.5" cy="7" r="2" fill={stasisPod} />
    </Svg>
  );
}

/** 命中冲击环：扩散的一圈（它同时是「这一发打中了」的确认）。 */
export function ImpactRing({ size = 30 }: { size?: number }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 44 44">
      <Circle cx="22" cy="22" r="17" fill="none" stroke={stasisMuzzle}
              strokeWidth="3" opacity="0.9" />
      <Circle cx="22" cy="22" r="9" fill="none" stroke={stasisTracer}
              strokeWidth="2.2" opacity="0.75" />
      {[0, 90, 180, 270].map((a) => (
        <Path key={a} d="M22 1 L24 7 L20 7 Z" fill={stasisMuzzle} opacity="0.9"
              transform={`rotate(${a} 22 22)`} />
      ))}
    </Svg>
  );
}

// ── 静默体 ──────────────────────────────────────────────────

// 本体轮廓（原点在图形中心，便于按状态整体缩放）。
// 圆润、略不对称的团块 —— 设定是「异星寄生体」，压迫感来自体型与逼近，
// 不是尖牙与血。
const BODY_D =
  'M 0 -13 C 5.5 -13.6 11.2 -10.6 13.2 -4.6 C 15 -0.6 13.6 5.2 8.6 8.8 ' +
  'C 4.4 11.8 -4.4 12.2 -8.8 8.6 C -13.4 4.8 -14.8 -1.4 -12.6 -6.2 ' +
  'C -10.4 -11 -5.5 -13.6 0 -13 Z';

// 发光核心：四角星（火花）而不是圆。两个理由：① 设定稿里的核心是一团向外发散
// 的辉光，不是一颗圆珠；② **圆形深底 + 亮心会被读成一只眼睛**，而设定明确
// 「没有眼睛」。星形 + 深墨描边既不像眼睛，又保证核心在三种体色上都有边界。
const CORE_D =
  'M 0 -7.2 L 1.9 -1.9 L 7.2 0 L 1.9 1.9 L 0 7.2 L -1.9 1.9 L -7.2 0 L -1.9 -1.9 Z';

/**
 * 静默体本体（画在通道上）。
 *
 * **三种状态的颜色差不够，必须同时改形状**（WCAG 1.4.1）：
 * - `normal`　圆润团块 + 青色发光核心 + 底部两枚橙色小灯
 * - `retreat`　**整体缩小**，外加一圈**向内收**的触须（退回气闸再生中）
 * - `overload`　**整体放大**，外加**放射状尖刺 + 外光环**（门血见底时的过载态）
 * 三者的外接尺寸也依次为 小 < 中 < 大，远看轮廓就能分辨。
 */
export function EntitySprite({ size = 40, mood = 'normal' }:
  { size?: number; mood?: 'normal' | 'retreat' | 'overload' }) {
  const body = mood === 'retreat' ? stasisEntityRetreat
    : mood === 'overload' ? stasisEntityOverload
      : stasisEntity;
  const scale = mood === 'retreat' ? 0.95 : mood === 'overload' ? 1.5 : 1.3;
  return (
    <Svg width={size} height={size} viewBox="0 0 64 64">
      {mood === 'overload' ? (
        <G>
          <Circle cx="32" cy="32" r="26" fill="none" stroke={body} strokeWidth="2" opacity="0.55" />
          {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
            <Path key={a} d="M 0 -25.5 L 3.8 -16.5 L -3.8 -16.5 Z" fill={body}
                  stroke={stasisInk} strokeWidth="1" transform={`translate(32 32) rotate(${a})`} />
          ))}
        </G>
      ) : null}
      <G transform={`translate(32 32) scale(${scale})`}>
        {mood === 'retreat' ? [0, 60, 120, 180, 240, 300].map((a) => (
          <Path key={a} d="M 15 -4.5 Q 20.5 0 13.5 4.5" fill="none" stroke={body}
                strokeWidth="2.2" strokeLinecap="round" transform={`rotate(${a})`} />
        )) : null}
        <Path d={BODY_D} fill={body} fillOpacity="0.92" stroke={stasisInk} strokeWidth="1.3" />
        <Circle cx="0" cy="0" r="8.6" fill={stasisAlloy} opacity="0.25" />
        <Path d={CORE_D} fill={stasisAlloy} stroke={stasisInk}
              strokeWidth="1.1" strokeLinejoin="round" />
        <Circle cx="0" cy="0" r="1.7" fill={stasisPod} />
        {mood === 'normal' ? (
          <G>
            <Circle cx="-4.6" cy="8.4" r="2.2" fill={stasisInk} opacity="0.6" />
            <Circle cx="4.6" cy="8.4" r="2.2" fill={stasisInk} opacity="0.6" />
            <Circle cx="-4.6" cy="8.4" r="1.4" fill={stasisAlert} />
            <Circle cx="4.6" cy="8.4" r="1.4" fill={stasisAlert} />
          </G>
        ) : null}
      </G>
    </Svg>
  );
}
