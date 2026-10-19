// 深空休眠的**地图渲染层**：整层楼的俯视平面图 —— 静态层（通道 / 舱壁 / 地板 /
// 气闸）、动态层（门 / 建筑 / 休眠舱 / 空格 / 范围圈 / 角色 / 静默体）与虚拟摇杆。
//
// 从 `StasisScreen.tsx` 拆出来的（那一屏原来 2700 行，其中约 600 行是纯渲染）。
// 这里**只有 props 进、JSX 出**：没有一个组件持有对局状态，也没有一个读 socket。
// 屏幕层负责状态与回调，这一层负责「长什么样」。
//
// ── 为什么要按图层拆、而且要 `memo` ────────────────────────────
//
// tick 是 5 Hz，而每一层的内容**只在 `stasis_state`（离散动作）时才变**。不抽出来
// + `memo`，它们就跟着每 tick 重建（有人的舱室叠色 ~100 个 View、休眠舱 12 个、
// 建筑若干个）—— 帧率就是这么塌下来的。所以：
//
// - **props 必须是稳定引用**，否则 `memo` 等于没写：`onPress` 一律走 ref 包过的
//   稳定回调（屏幕层负责），这里不自己 `useCallback` 造新函数。
// - 静态层（`StaticMap`）只在 `shapes` 变时重渲染 —— 一局就一次。750 个格子全画
//   的话每 tick 都会重建整棵树，5 Hz 下必卡。
//
// ── 渲染取巧（改几何前必读）────────────────────────────────────
//
// **先铺整块 7×6 舱壁、再把地板格盖上去**：房型缺角自然露出舱壁色，不必逐格判
// 类型（750 格的逐格判断既慢又容易和 `stasisMap` 漂移）。门格也在这块舱壁里，
// 由 `DoorView` 画在它上面 —— 门是墙上的缺口，不是地板上的家具。
import React, { memo, useMemo, useRef, useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Animated, PanResponder } from 'react-native';
import { withAlpha, stasisGrid, stasisWall, stasisWallBevel, stasisFloor, stasisCorridor,
  stasisSpace, stasisInk, stasisPod, stasisBio, stasisAlloy, stasisGold,
  stasisTurret, stasisEntity, stasisEntityRetreat, stasisEntityOverload,
  stasisDoorLow, stasisDoorOk, stasisRepair, stasisShield } from './theme';
import {
  TILE, cellKey, type MapGeom, type Side,
} from './stasisMap';
import {
  AcceleratorSprite, AirlockSprite, BinderSprite, CryoSprite, DoorSprite,
  EntitySprite, HeroSprite, MineSprite, PodSprite, RefinerySprite,
  RepairSprite, ShieldSprite, TurretSprite,
} from './stasisArt';
import type { DoorHpMap } from './StasisPanels';
import type { StasisRoomState } from './stasisTypes';
import {
  MINE_TIERS, ON_SPACE, ON_SPACE_SOFT, WEB_TOUCH_NONE,
  buildingColor, buildingRate, clampRatio, doorTone, turretRange,
} from './stasisRules';

/** 建筑的整格贴图（`stasisArt.tsx`，俯视 sprite，一格一张）。
 *
 *  放在这一层而不是屏幕层：**建造菜单里那张预览图与地图上那一格是同一张**，
 *  两边共用一个分发函数才不会出现「菜单里长这样、建出来长那样」。 */
export function buildingSprite(kind: string, level: number, size: number) {
  if (kind === 'turret') return <TurretSprite level={level} size={size} />;
  if (kind === 'refinery') return <RefinerySprite level={level} size={size} />;
  if (kind === 'repair') return <RepairSprite size={size} />;
  // 控场四件（v13）：都不打伤害，轮廓各自独立（见 `stasisArt` 那一段）。
  if (kind === 'shield') return <ShieldSprite size={size} />;
  if (kind === 'binder') return <BinderSprite size={size} />;
  if (kind === 'cryo') return <CryoSprite size={size} />;
  if (kind === 'accelerator') return <AcceleratorSprite size={size} />;
  return <MineSprite tier={Math.max(0, MINE_TIERS.indexOf(kind))} size={size}
                     premium={kind === 'diamond' && level >= 2} />;
}

// ── 静态层 ──────────────────────────────────────────────

export const StaticMap = memo(function StaticMap({ map }: { map: MapGeom }) {
  const cells: React.ReactNode[] = [];

  // ① 通道：**逐行游程**（连续同类型的格子合成一个 View）。
  //
  //    v9 起地图由服务端下发（多张图），没法再像老代码那样「写死纵向两条、横向三条」
  //    —— 但也不能逐格建节点（一张图最多 1085 格）。游程是两者的折中：一趟扫描把
  //    每一行里连续的通道格合成一个矩形，一张图几十个节点就画完了。
  //
  //    护栏按「这一段外面的邻居是不是通道」画：通道比舱室暗，但明度只差 1.41:1，
  //    真正把它读成「一条沟」而不是「一块深色地板」的是这两条亮边。
  for (let y = 0; y < map.h; y++) {
    const row = map.rows[y] ?? '';
    let x = 0;
    while (x < map.w) {
      const ch = row[x];
      let x2 = x + 1;
      while (x2 < map.w && row[x2] === ch) x2++;
      if (ch === 'c') {
        const w = (x2 - x) * TILE;
        cells.push(
          <View key={`c${x}-${y}`} style={[styles.corridor, { left: x * TILE, top: y * TILE, width: w, height: TILE }]} />,
        );
        if (map.kindAt(x, y - 1) !== 'corridor') {
          cells.push(<View key={`rt${x}-${y}`} style={[styles.corridorRailH, { left: x * TILE, top: y * TILE, width: w }]} />);
        }
        if (map.kindAt(x, y + 1) !== 'corridor') {
          cells.push(<View key={`rb${x}-${y}`} style={[styles.corridorRailH, { left: x * TILE, top: (y + 1) * TILE - 2, width: w }]} />);
        }
        if (map.kindAt(x - 1, y) !== 'corridor') {
          cells.push(<View key={`rl${x}-${y}`} style={[styles.corridorRailV, { left: x * TILE, top: y * TILE, height: TILE }]} />);
        }
        if (map.kindAt(x2, y) !== 'corridor') {
          cells.push(<View key={`rr${x}-${y}`} style={[styles.corridorRailV, { left: x2 * TILE - 2, top: y * TILE, height: TILE }]} />);
        }
      }
      x = x2;
    }
  }

  // ② 舱室：**先铺整块舱壁**（包围盒），再把内部地板格盖上去。
  //
  //    「先铺一块再盖地板」是有意的：房型造成的缺角（凹角 / L / 立柱）本来就该
  //    露出舱壁色，这样**一格厚的墙**和房型的不规则一起自然长出来，不必逐格判断
  //    类型。门格也在这块舱壁里 —— 门由 `DoorView` 画在它上面（门是墙上的缺口）。
  //
  //    地板仍然**逐格**画（不并成游程）：一格一条 `borderColor` 就是那张瓷砖网格，
  //    合并成矩形会把网格弄丢（一张图只有二百来格，而且是 memo 过的静态层）。
  for (const rid of map.roomIds) {
    const r = map.rooms[rid];
    const [ox, oy, bw, bh] = r.box;
    cells.push(
      <View
        key={`w${rid}`}
        style={[styles.wallBox, { left: ox * TILE, top: oy * TILE, width: bw * TILE, height: bh * TILE }]}
      />,
    );
    // 内壁高光：舱壁的**内侧面**。一格厚的观感全靠这条亮线 —— 墙色与地板色
    // 都是深蓝灰，只差 1.5:1，没有它读不出「墙有多厚」。
    cells.push(
      <View
        key={`wi${rid}`}
        style={[styles.wallInner, {
          left: (ox + 1) * TILE, top: (oy + 1) * TILE,
          width: (bw - 2) * TILE, height: (bh - 2) * TILE,
        }]}
      />,
    );
    // 舱壁板缝：横向接缝线，把「一整块色块」读成**拼装的舱壁**。
    // 同一条教训 —— 引结构靠线不靠填充（俄罗斯方块的 `tetrisGrid`）。
    for (let k = 1; k * 2 < bh - 1; k++) {
      cells.push(
        <View
          key={`wr${rid}-${k}`}
          style={[styles.wallRib, { left: ox * TILE + 3, top: (oy + k * 2) * TILE, width: bw * TILE - 6 }]}
        />,
      );
    }
    for (const [x, y] of r.cells) {
      cells.push(
        <View
          key={`f${x}-${y}`}
          style={[styles.floorTile, { left: x * TILE, top: y * TILE, width: TILE, height: TILE }]}
        />,
      );
    }
  }

  // ③ 气闸（静默体入场口）：黄黑警示条 + 闸门（贴图），底下垫一行字。
  cells.push(
    <View key="air" style={[styles.airlock, { left: map.airlock[0] * TILE, top: map.airlock[1] * TILE, width: TILE, height: TILE }]}>
      <AirlockSprite size={TILE} />
      <Text style={styles.airlockText}>气闸</Text>
    </View>,
  );

  return <>{cells}</>;
});

// ── 动态层 ──────────────────────────────────────────────

export function DoorView({ id, at, side, kind = 'room', level, hp, max, showBar, targeted, shieldMs = 0, onPress, shake }: {
  /** 房间号，或共用闸门的 id。决定 testID 前缀。 */
  id: string;
  /** 门格（舱壁上的那个缺口）。 */
  at: [number, number];
  /** 门朝哪边开 —— 决定门体是竖的还是横的。 */
  side: Side;
  /** `room` 房间门 / `gate` **共用闸门**（v9）。后者加一圈金色门框与「外闸」标签：
   *  它是两个人的共用前线，画得和自家房门一模一样的话，玩家分不清哪扇是公共的。 */
  kind?: 'room' | 'gate';
  level: number; hp: number; max: number;
  /** 只有**有主的**舱室才画血条 —— 空房的门恒满血，条是纯装饰，
   *  少画 6 条就是少 12 个每 tick 重建的元素。闸门恒画（它是公共目标）。 */
  showBar: boolean;
  targeted: boolean;
  /** **应急力场的护盾还剩多久**（毫秒，0 = 没开，v13）。那 3 秒里门不掉血 ——
   *  不画出来的话，玩家看到的是「门卡住了」，而不是「力场在挡」。 */
  shieldMs?: number;
  onPress: () => void;
  /** 被砸时的横向抖动（`Animated.ValueXY`，由主循环写）。
   *  **血条是结果、抖动是事件** —— 结果每帧都在变，事件才抓得住眼睛。 */
  shake?: Animated.ValueXY;
}) {
  // **门体方向跟着门开在哪条边走**：左右墙上的门是竖的，上下墙上的门是横的。
  // 老地图（dorm）只有左右门，所以这里原本写死了竖版；`atrium` 的上下排与
  // `suite` 的内门都是横的，写死会让门横在墙上穿帮。
  const vertical = side === 'left' || side === 'right';
  const w = vertical ? 26 : TILE - 8;
  const h = vertical ? TILE - 8 : 26;
  // **门画在门格上**（舱壁那个缺口），不是舱内格上 —— 门是墙的一部分，
  // 画在舱内会变成「地板上立着一扇门」。
  const cx = at[0] * TILE + TILE / 2;
  const cy = at[1] * TILE + TILE / 2;
  const ratio = clampRatio(hp, max);
  const barW = 50;
  return (
    <>
      {/* 门框：嵌在舱壁里，门体才不会像浮在空中的色块 */}
      <View style={[kind === 'gate' ? styles.gateFrame : styles.doorFrame,
        { left: cx - w / 2 - 4, top: cy - h / 2 - 4, width: w + 8, height: h + 8 }]} />
      <Animated.View
        pointerEvents="box-none"
        style={[styles.doorWrap, {
          left: cx - w / 2, top: cy - h / 2, width: w, height: h,
          transform: shake ? shake.getTranslateTransform() : undefined,
        }]}
      >
        <TouchableOpacity
          testID={`${kind === 'gate' ? 'stasis-gate' : 'stasis-door'}-${id}`}
          onPress={onPress}
          activeOpacity={0.75}
          style={[styles.door, {
            width: w, height: h,
            // 贴图自带材质与描边（等级纹理见 `stasisArt.DoorSprite`）；
            // 这一层只负责「被锁定时套一圈红边」这条状态。
            borderColor: targeted ? stasisDoorLow : 'transparent',
            borderWidth: targeted ? 3 : 0,
          }]}
        >
          <DoorSprite level={level} size={Math.min(w, h) + 6} />
          <Text style={styles.doorLv}>{level}</Text>
        </TouchableOpacity>
      </Animated.View>
      {/* 血条抬到门体**上方固定高度** —— 原型里「门朝上的房间有优势，因为血条
          不会被鬼挡住」是设计缺陷，正解是把血条画在不会被遮挡的层，而不是让
          玩家靠选房间朝向规避。 */}
      {showBar ? (
        <View style={[styles.doorBar, { left: cx - barW / 2, top: cy - h / 2 - 13, width: barW }]}>
          <View style={[styles.doorBarFill, { width: `${ratio * 100}%`, backgroundColor: doorTone(ratio) }]} />
        </View>
      ) : null}
      {shieldMs > 0 ? (
        // 六边形力场罩（与 `stasisArt.ShieldSprite` 同一个形状语言）：
        // 罩子比门大一圈、半透明，并带一行「力场」标签 —— 颜色不单独承载信息。
        <View pointerEvents="none"
              style={[styles.shieldWrap, {
                left: cx - w / 2 - 7, top: cy - h / 2 - 7,
                width: w + 14, height: h + 14,
                borderColor: stasisShield,
                opacity: 0.55 + 0.45 * Math.min(1, shieldMs / 600),
              }]}>
          <Text style={[styles.shieldTag, { color: stasisShield }]}>力场</Text>
        </View>
      ) : null}
      {kind === 'gate' ? (
        <Text style={[styles.gateTag, { left: cx - 14, top: cy + h / 2 + 1 }]}>外闸</Text>
      ) : null}
      {targeted ? (
        <Text style={[styles.targetMark, { left: cx - 26, top: cy - h / 2 - 28 }]}>◤被锁定</Text>
      ) : null}
    </>
  );
}


/** 门与闸门的**一整层**（v13，`memo`）。
 *
 * 为什么值得单独一层：这一屏的慢变量（门血、资源、头像）走 **2.5 Hz** 的
 * `setState`，而 12 扇门 + 最多 6 扇外闸的 `DoorView`（每个含一张 SVG 贴图 +
 * 血条 + 若干标签）原来**直接长在主组件的 JSX 里** —— 每次慢变量刷新都要重建
 * 18 棵子树。抽成 `memo` 层之后，只有**内容和上一次真的不一样**的那几扇门会重建。
 *
 * ⚠️ **`memo` 要真的生效，回调必须稳定**：`onPress` 每次渲染都新建一个闭包的话，
 * props 永远浅比较不等，memo 等于没写。所以这一层收的是**稳定身份的回调**
 * （屏幕层用 `useRef` 存最新实现、`useCallback([])` 包一层，见 `StasisScreen`）。
 * 这与本项目在 UNO/俄罗斯方块上踩过的「拆了组件但没拆依赖」是同一类事。
 */
export const DoorLayer = memo(function DoorLayer({
  map, rooms, gates, doorHp, targeted, shakes, onDoorPress, onGatePress,
}: {
  map: MapGeom;
  rooms: Record<string, StasisRoomState>;
  /** 共用闸门的显示状态（几何在 `map.gates` 里，状态在这里）。 */
  gates: Record<string, { level: number; hp: number; max: number; broken: boolean }>;
  /** 5 Hz tick 覆盖的血量（没有就用 `stasis_state` 里的值）。 */
  doorHp: DoorHpMap;
  /** 静默体正在砸的目标（房间 id 或 `gate:G1`）—— 决定「被锁定」标记与抖动的对象。 */
  targeted: string | null;
  /** 每扇门的抖动值（`Animated.ValueXY` 先建好、由主循环写，见 `StasisScreen`）。 */
  shakes: { get: (id: string) => Animated.ValueXY | undefined };
  onDoorPress: (rid: string) => void;
  onGatePress: (gid: string) => void;
}) {
  return (
    <>
      {Object.keys(map.gates).map((gid) => {
        const g = map.gates[gid];
        const st = gates[gid];
        if (!g || !st || st.broken) return null;
        return (
          <DoorView
            key={`g${gid}`}
            id={gid}
            at={g.doorCell}
            side={g.side}
            kind="gate"
            level={st.level}
            hp={st.hp}
            max={st.max}
            showBar
            targeted={targeted === `gate:${gid}`}
            shake={shakes.get(gid)}
            onPress={() => onGatePress(gid)}
          />
        );
      })}
      {map.roomIds.map((rid) => {
        const r = rooms[rid];
        if (!r) return null;
        return (
          <DoorView
            key={`d${rid}`}
            id={rid}
            at={[r.door.door[0], r.door.door[1]]}
            side={r.door.side as Side}
            level={r.door_level}
            hp={doorHp[rid]?.hp ?? r.door_hp}
            max={doorHp[rid]?.max ?? r.door_max}
            showBar={r.owner != null || targeted === rid}
            targeted={targeted === rid}
            shieldMs={doorHp[rid]?.shield_ms ?? 0}
            shake={shakes.get(rid)}
            onPress={() => onDoorPress(rid)}
          />
        );
      })}
    </>
  );
});

/**
 * 建筑：**整格贴图**（`stasisArt`），不再是「一个色块 + 22px 小图标」。
 *
 * 队友的建筑整体降透明度（`owner=false`）—— 一眼分得出「这格是谁的」，
 * 而不用去读头顶的产出数字。
 *
 * ⚠️ **这个 `TouchableOpacity` 必须自己带尺寸**（`cellHit` + 一格宽高）。
 * 贴图与产出文字都是 `position: absolute`，**没有一个是参与布局的子元素** ——
 * 包装层于是高度为 0，浏览器认为它「不可见」，点不到（Playwright 报
 * `element is not visible`，真机上则是「点建筑没反应」）。
 * 同一类事故：**绝对定位的子元素撑不起父容器**。
 */
export function BuildingView({ x, y, kind, level, owner, onPress }: {
  x: number; y: number; kind: string; level: number; owner: boolean;
  onPress: () => void;
}) {
  const rate = buildingRate(kind, level);
  return (
    <TouchableOpacity
      testID={`stasis-building-${x}-${y}`}
      activeOpacity={0.8}
      onPress={onPress}
      style={[styles.cellHit, {
        left: x * TILE, top: y * TILE, width: TILE, height: TILE,
        opacity: owner ? 1 : 0.55,
      }]}
    >
      {buildingSprite(kind, level, TILE)}
      {level > 1 ? <LevelBadge text={`${level}`} color={buildingColor(kind)} /> : null}
      {rate ? (
        <Text style={[styles.gain, styles.gainOnCell, { color: rate.alloy ? stasisAlloy : stasisBio }]}>
          {rate.alloy ? '⚡' : '●'}{rate.text}
        </Text>
      ) : null}
    </TouchableOpacity>
  );
}

/** 建筑/休眠舱右下角的等级徽标。**贴图里不写数字**（见 `stasisArt` 文件头），
 *  数字在这一层画 —— 它能跟着主题走、也能被视觉审计看见。 */
export function LevelBadge({ text, color }: { text: string; color: string }) {
  return (
    <View pointerEvents="none" style={[styles.lvBadge, { borderColor: color }]}>
      <Text style={styles.lvBadgeText}>{text}</Text>
    </View>
  );
}

// ── 只依赖「归属」的图层（记忆化）──────────────────────
//
// 这几层的内容**只在 `stasis_state`（离散动作）时变**，但 tick 5 Hz 会让整个
// 主组件重渲染一次 —— 不抽出来 + `memo`，它们就跟着每 tick 重建。见文件头。

export const RoomLightLayer = memo(function RoomLightLayer({
  map, rooms,
}: { map: MapGeom; rooms: Record<string, StasisRoomState> }) {
  const out: React.ReactNode[] = [];
  for (const rid of map.roomIds) {
    const r = rooms[rid];
    if (!r || r.owner == null) continue;
    const [ox, oy, bw, bh] = r.box;
    out.push(
      <View
        key={`sl${rid}`}
        pointerEvents="none"
        style={[styles.shellLit, { left: ox * TILE - 3, top: oy * TILE - 3, width: bw * TILE + 6, height: bh * TILE + 6 }]}
      />,
    );
    for (const [x, y] of r.cells) {
      out.push(
        <View
          key={`lit${x}-${y}`}
          pointerEvents="none"
          style={[styles.litTile, { left: x * TILE, top: y * TILE, width: TILE, height: TILE }]}
        />,
      );
    }
  }
  return <>{out}</>;
});

export const BedLayer = memo(function BedLayer({
  map, rooms, inBed, bedRate, onPress,
}: {
  map: MapGeom;
  rooms: Record<string, StasisRoomState>;
  inBed: Record<string, boolean>;
  /** 每间舱室主人当前的产率 —— 床等级挂在玩家身上，不在房间上。 */
  bedRate: Record<string, number>;
  onPress: (rid: string) => void;
}) {
  return (
    <>
      {map.roomIds.map((rid) => {
        const r = rooms[rid];
        if (!r) return null;
        const [bx, by] = r.bed;
        const owner = r.owner;
        const lit = owner != null;
        const lying = !!inBed[rid];
        return (
          <TouchableOpacity
            key={`b${rid}`}
            testID={`stasis-bed-${rid}`}
            activeOpacity={0.8}
            onPress={() => onPress(rid)}
            style={[styles.bedHit, { left: bx * TILE, top: by * TILE, width: TILE, height: TILE }]}
          >
            <PodSprite lit={lit} occupied={lying} size={TILE} />
            {lit ? (
              <Text style={[styles.gain, styles.gainOnObj, { color: stasisBio }]}>
                ●{bedRate[rid] ?? 1}
              </Text>
            ) : null}
          </TouchableOpacity>
        );
      })}
    </>
  );
});

/**
 * 攻击范围圈。**点开炮台的升级面板时画出来**（关掉就消失）。
 *
 * 惯例来自塔防的通用做法（`增强调研.md` §10.4）：「鼠标移到炮塔上显示攻击范围，
 * 单击炮塔弹出管理菜单」。触屏没有 hover，所以本作把两步合成一步 ——
 * **点炮台 = 画圈 + 开面板**。
 *
 * 画在**地板之上、建筑之下**：填充很淡（0.08），描边用**虚线** —— 线型是第二
 * 通道，不与实线的门框/建筑描边混。半径直接取服务端那条公式（`turretRange`）。
 */
export const RangeLayer = memo(function RangeLayer({ cell, level }: { cell: string | null; level: number }) {
  if (!cell) return null;
  const [x, y] = cell.split(',').map(Number);
  const r = turretRange(level) * TILE;
  const cx = (x + 0.5) * TILE;
  const cy = (y + 0.5) * TILE;
  const box = { left: cx - r, top: cy - r, width: r * 2, height: r * 2, borderRadius: r };
  return (
    <>
      <View testID="stasis-range" pointerEvents="none" style={[styles.rangeFill, box]} />
      <View pointerEvents="none" style={[styles.rangeEdge, box]} />
      <Text pointerEvents="none" style={[styles.rangeLabel, { left: cx - 34, top: cy + r - 16 }]}>
        {`射程 ${turretRange(level)} 格`}
      </Text>
    </>
  );
});

export const EmptyTileLayer = memo(function EmptyTileLayer({
  map, myRoom, rooms, buildings, onPress, highlight,
}: {
  map: MapGeom;
  myRoom: string | null;
  rooms: Record<string, StasisRoomState>;
  buildings: Record<string, { kind: string; level: number; owner: boolean }>;
  onPress: (x: number, y: number) => void;
  /** 建造面板打开时，**把玩家刚点的那一格框出来** —— 面板盖住了半屏，
   *  不标一下玩家会忘了自己点的是哪一格（见 `增强调研.md` §10.5）。 */
  highlight: string | null;
}) {
  const room = myRoom ? rooms[myRoom] : null;
  if (!myRoom || !room) return null;
  const [bx, by] = room.bed;
  return (
    <>
      {room.cells.map(([x, y]) => {
        if (bx === x && by === y) return null;
        if (buildings[cellKey(x, y)]) return null;
        const on = highlight === cellKey(x, y);
        return (
          <TouchableOpacity
            key={`e${x}-${y}`}
            testID={`stasis-tile-${x}-${y}`}
            activeOpacity={0.6}
            onPress={() => onPress(x, y)}
            style={[styles.emptyTile, on && styles.emptyTileOn,
              { left: x * TILE, top: y * TILE, width: TILE, height: TILE }]}
          >
            <Text style={[styles.emptyPlus, on && styles.emptyPlusOn]}>+</Text>
          </TouchableOpacity>
        );
      })}
    </>
  );
});

export const BuildingLayer = memo(function BuildingLayer({
  buildings, onPress,
}: {
  buildings: Record<string, { kind: string; level: number; owner: boolean }>;
  onPress: (key: string, owner: boolean) => void;
}) {
  return (
    <>
      {Object.keys(buildings).map((key) => {
        const b = buildings[key];
        const [x, y] = key.split(',').map(Number);
        return (
          <BuildingView
            key={`bd${key}`}
            x={x} y={y} kind={b.kind} level={b.level} owner={b.owner}
            onPress={() => onPress(key, b.owner)}
          />
        );
      })}
    </>
  );
});

/** 玩家角色（Q 版小人）。位置由父层的 Animated 容器负责，这里只画。
 *
 *  `lying` 就把小人**放倒**（旋转 70° + 半透明）—— v4 之前这一位叫 `down`
 *  （被静默体撞倒），现在撞倒不存在了（碰到即出局），于是这个姿势改成表达
 *  「躺下了」：**一眼能看出队友是不是已经进屋躺好**，这正是本作最要紧的一条状态。
 */
export function HeroView({ color, name, mine, lying, size = 30 }: {
  color: string; name: string; mine: boolean; lying: boolean; size?: number;
}) {
  return (
    <View style={styles.heroWrap} pointerEvents="none">
      <Text style={[styles.heroName, mine && styles.heroNameMine]} numberOfLines={1}>{name}</Text>
      <HeroSprite color={color} lying={lying} size={size} />
    </View>
  );
}

export function EntityView({ mood, level, hp, maxHp }: { mood: string; level: number; hp: number; maxHp: number }) {
  const color = mood === 'retreat' ? stasisEntityRetreat
    : mood === 'overload' ? stasisEntityOverload : stasisEntity;
  const ratio = clampRatio(hp, maxHp);
  return (
    <View style={styles.entityWrap} pointerEvents="none">
      <Text style={[styles.entityLv, { color }]}>Lv{level} 静默体</Text>
      <View style={styles.entityBar}>
        <View style={[styles.entityBarFill, { width: `${ratio * 100}%`, backgroundColor: color }]} />
      </View>
      <EntitySprite size={36} mood={mood as 'normal' | 'retreat' | 'overload'} />
    </View>
  );
}

// ── 摇杆 ────────────────────────────────────────────────

/** 摇杆的半径（px）。屏幕层要用它给「躺下」按钮定位（摇杆右侧）。 */
export const STICK_R = 52;
const KNOB_R = 22;

/**
 * 摇杆的**径向死区**（占满量程的比例）。
 *
 * 手指按下时天然有零点几像素的抖动，没有死区的话「按住不动」也会持续上行一个
 * 极小方向 —— 服务端按这个方向积分，人会慢慢飘。
 *
 * **必须用径向缩放，不能按轴裁剪**（见 `增强调研.md` §10.5 与
 * [thumbstick deadzones]）：按轴裁剪（`|dx| < T ? 0 : dx`）会让斜向出现**方形
 * 死区**，而且过死区时速度突跳。径向版只跟「推了多远」有关，方向永远连续。
 */
const STICK_DEAD = 0.12;
/**
 * 死区之外的**响应曲线**指数（`t^1.25`）。轻微指数 = 小幅推杆时更精细、
 * 推到边缘仍然给满速。1.0 是纯线性。
 */
const STICK_CURVE = 1.25;

/**
 * 虚拟摇杆。**只在方向变化时上报**，松手补一条 `(0,0)` —— 不补的话服务端会一直
 * 按最后的方向推着人走。上行频率由**主循环**单独节流（`SEND_MIN_MS`），这里不管。
 */
export function Joystick({ onDir }: { onDir: (dx: number, dy: number) => void }) {
  const knob = useRef(new Animated.ValueXY({ x: 0, y: 0 })).current;
  const dirRef = useRef<[number, number]>([0, 0]);
  const [held, setHeld] = useState(false);

  // ⚠️ **这里曾经按 180ms 节流 `onDir`，那是「摇杆移动非常卡」的一半原因。**
  // `onDir` 一头连着**本地预演的朝向**、一头连着上行 —— 节流它等于让角色每
  // 180ms 才换一次朝向（拖动时一跳一跳地拐弯）。**预演必须每个 move 事件都
  // 拿到新方向**；上行频率由主循环那侧单独节流。
  //
  // ⚠️⚠️ **这里还曾经挂过一个 700ms 看门狗（「方向非零但 700ms 没再上报就补一条
  // (0,0)」），它是另一半原因、而且更致命**：手指按住摇杆不动时**本来就不发
  // move 事件**，于是「按住不动」被它判成了「手势被抢走」——角色的实测轨迹是
  // 「匀速走 0.45 秒 → 原地停住」，玩家体验就是「推着摇杆人却一顿一顿地走、
  // 走两步停一下」。看门狗要防的是「release 事件丢了」（浏览器把横拖当成返回
  // 手势），而那个问题**已经由 `touch-action: none` 根治**（横拖不再被浏览器
  // 接管 → release 一定到），剩下的兜底是 `onPanResponderTerminate`（被抢走时
  // 系统会派发 cancel，RNW 会走这一支）。**不要把它加回来。**
  const responder = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onMoveShouldSetPanResponder: () => true,
    onPanResponderGrant: () => { setHeld(true); dirRef.current = [0, 0]; },
    onPanResponderMove: (_e, g) => {
      const d = Math.hypot(g.dx, g.dy);
      const k = d > STICK_R ? STICK_R / d : 1;
      knob.setValue({ x: g.dx * k, y: g.dy * k });
      // 径向死区 + 响应曲线：方向取单位向量、幅度单独算，两者互不干扰
      const mag = Math.min(1, d / STICK_R);
      if (mag <= STICK_DEAD || d === 0) {
        dirRef.current = [0, 0];
        onDir(0, 0);
        return;
      }
      const amp = Math.pow((mag - STICK_DEAD) / (1 - STICK_DEAD), STICK_CURVE);
      const nx = (g.dx / d) * amp;
      const ny = (g.dy / d) * amp;
      dirRef.current = [nx, ny];
      onDir(nx, ny);
    },
    onPanResponderRelease: () => {
      setHeld(false);
      Animated.spring(knob, { toValue: { x: 0, y: 0 }, useNativeDriver: false, friction: 6 }).start();
      dirRef.current = [0, 0];
      onDir(0, 0);
    },
    onPanResponderTerminate: () => {
      setHeld(false);
      knob.setValue({ x: 0, y: 0 });
      dirRef.current = [0, 0];
      onDir(0, 0);
    },
  }), [onDir, knob]);

  return (
    <View
      testID="stasis-stick"
      // **Web 端必须 `touch-action: none`**：摇杆的横拖会被浏览器当成「返回上一页」
      // 的滑动手势抢走 —— 抢走之后 JS 收不到 release，摇杆永远停在最后的方向上，
      // 角色会一直朝那边走（同 2048/五子棋的教训，写法也照抄它们的）。
      style={[styles.stick, held && styles.stickHeld, WEB_TOUCH_NONE]}
      {...responder.panHandlers}
    >
      {/* 内圈：按下才出现 —— 触摸没有 hover，**按下必须有确认**，
          否则「按了没反应」的错觉会让人反复重按（见 `增强调研.md` §10.5） */}
      <View style={[styles.stickInner, held && styles.stickInnerHeld]} pointerEvents="none" />
      <Animated.View
        pointerEvents="none"
        style={[styles.stickKnob, held && styles.stickKnobHeld, { transform: knob.getTranslateTransform() }]}
      />
      <Text style={styles.stickHint} pointerEvents="none">移动</Text>
    </View>
  );
}

/** 摇杆的占位盒（出局后不画摇杆，但底部布局要留住）。 */
export const StickPlaceholder = memo(function StickPlaceholder() {
  return <View style={styles.stickPlaceholder} />;
});

const styles = StyleSheet.create({
  corridor: { position: 'absolute', backgroundColor: stasisCorridor, borderWidth: 1, borderColor: '#2A3A52' },
  // 通道护栏：把通道读成「一条沟」而不是「一块深色地板」。明度差不够（1.41:1），
  // 靠这两条亮边给形状通道（同「引格靠线不靠填充」）。
  // 两种朝向各一条样式：**位置与长度全部由调用方给**（游程的长度是算出来的），
  // 样式里只留厚度与颜色。
  corridorRailV: { position: 'absolute', width: 2, backgroundColor: withAlpha(stasisGrid, 0.55) },
  corridorRailH: { position: 'absolute', height: 2, backgroundColor: withAlpha(stasisGrid, 0.55) },
  // 舱壁：**整块 7×6 涂满**，地板格再盖上去（见 `StaticMap` 的注释）。
  //   `stasisWall` 比地板亮一档（1.52:1），真正把它读成「墙」的是下面那条内壁
  //   高光 + 深色外框 —— 单靠填充色差和地板糊在一起。
  wallBox: {
    position: 'absolute',
    backgroundColor: stasisWall,
    borderWidth: 2,
    // 斜面：上/左亮、下/右暗 —— 一格厚的「砖块」立体感靠这个，
    // 不是靠填充色差（墙与地板只差 2.24:1）。
    borderTopColor: withAlpha(stasisWallBevel, 0.55),
    borderLeftColor: withAlpha(stasisWallBevel, 0.55),
    borderRightColor: stasisInk,
    borderBottomColor: stasisInk,
    borderRadius: 4,
    overflow: 'hidden',
  },
  // 内壁：墙在地板边缘投下的一圈阴影，读作「房间被墙围住」。
  wallInner: {
    position: 'absolute',
    borderWidth: 2,
    borderColor: withAlpha(stasisInk, 0.5),
    borderRadius: 2,
  },
  shellLit: { position: 'absolute', backgroundColor: '#070C15', borderWidth: 2, borderColor: withAlpha(stasisPod, 0.55), borderRadius: 5 },
  // 舱壁板缝：两道横向接缝，把「一整块 7×6 色块」读成拼装舱壁。
  wallRib: { position: 'absolute', height: 2, backgroundColor: withAlpha(stasisInk, 0.28) },
  floorTile: { position: 'absolute', backgroundColor: stasisFloor, borderWidth: 1, borderColor: withAlpha(stasisGrid, 0.75) },
  // 有人的舱室再叠一层**暖色**（不是更亮的灰）—— 只靠明度加亮会和地板糊在一起，
  // 换色相才一眼分得出「这间有人」。
  litTile: { position: 'absolute', backgroundColor: withAlpha(stasisGold, 0.10) },
  airlock: {
    position: 'absolute', backgroundColor: '#2A1A1E', borderWidth: 2, borderColor: stasisEntity,
    borderTopWidth: 0, alignItems: 'center', justifyContent: 'center', zIndex: 3,
  },
  airlockText: { color: stasisEntity, fontSize: 9, fontWeight: '800' },

  doorFrame: { position: 'absolute', backgroundColor: '#070C15', borderRadius: 5, zIndex: 4 },
  // **共用闸门**的门框描一圈金色 —— 它和自家房门长得一样的话，玩家分不清
  // 「这扇破了我也完了」和「这扇破了只是邻居完了」。
  gateFrame: { position: 'absolute', backgroundColor: '#070C15', borderRadius: 5, borderWidth: 2, borderColor: withAlpha(stasisGold, 0.75), zIndex: 4 },
  gateTag: { position: 'absolute', width: 28, textAlign: 'center', color: stasisGold, fontSize: 8.5, fontWeight: '800', zIndex: 7 },
  doorWrap: { position: 'absolute', zIndex: 5 },
  door: { borderRadius: 3, alignItems: 'center', justifyContent: 'center' },
  doorLv: { color: stasisInk, fontSize: 11, fontWeight: '800' },
  doorBar: { position: 'absolute', height: 7, borderRadius: 4, backgroundColor: stasisSpace, overflow: 'hidden', zIndex: 6 },
  doorBarFill: { height: '100%' },
  targetMark: { position: 'absolute', color: stasisDoorLow, fontSize: 9, fontWeight: '800', zIndex: 7 },
  // 应急力场的罩子（v13）：六边不行就用圆角矩形 —— Web 上 `borderRadius` 与
  // `transform: rotate` 一起用会被 RNW 拆成两层，罩子会歪。所以罩子取圆角矩形，
  // 「六边形」那个形状语言留给地图上那台**建筑本身**的贴图。
  shieldWrap: { position: 'absolute', borderRadius: 10, borderWidth: 2.5, zIndex: 8,
                alignItems: 'center', justifyContent: 'flex-end' },
  shieldTag: { fontSize: 8, fontWeight: '800', marginBottom: -2 },

  // **可点的整格容器必须自己带尺寸**：里面全是绝对定位的子元素，撑不起高度
  // （见 `BuildingView` 的注释）。
  cellHit: { position: 'absolute', alignItems: 'center', justifyContent: 'center', zIndex: 3 },
  // 休眠舱那颗也是可点的，尺寸同样必须撑满一格（贴图本身留了内边距）
  bedHit: { position: 'absolute', alignItems: 'center', justifyContent: 'center', zIndex: 3 },
  // 等级徽标：贴图里不写数字，数字在这一层画（能跟主题走、能被视觉审计看见）
  lvBadge: {
    position: 'absolute', right: 2, bottom: 2, minWidth: 13, height: 13,
    paddingHorizontal: 2, borderRadius: 4, borderWidth: 1.4,
    backgroundColor: withAlpha(stasisInk, 0.85), alignItems: 'center', justifyContent: 'center',
  },
  lvBadgeText: { color: ON_SPACE, fontSize: 8.5, fontWeight: '800' },
  gain: { position: 'absolute', fontSize: 10, fontWeight: '800', zIndex: 7 },
  gainOnObj: { position: 'absolute', top: -12, left: 0 },
  // 建筑那一格的产出数字：相对**格子**定位（`cellHit` 就在格子上），不是相对地图
  gainOnCell: { position: 'absolute', top: -13, left: 2 },
  emptyTile: { position: 'absolute', alignItems: 'center', justifyContent: 'center', zIndex: 2 },
  emptyTileOn: { backgroundColor: withAlpha(stasisTurret, 0.18) },
  emptyPlus: { color: withAlpha(stasisGrid, 0.7), fontSize: 18, fontWeight: '300' },
  emptyPlusOn: { color: stasisTurret, fontWeight: '700' },

  // 攻击范围圈（点开炮台面板时画）：填充很淡，描边**虚线** —— 线型是第二通道，
  // 不与实线的门框/建筑描边混。
  // ⚠️ Android 上 `borderStyle:'dashed'` 配 `borderRadius` 有已知的渲染成实线的问题，
  // 所以**不能把「这是射程圈」只押在线型上**：圈里那颗「射程 N 格」的文字标签
  // 才是保底通道（Web/iOS 上是虚线 + 文字两条）。
  rangeFill: { position: 'absolute', backgroundColor: withAlpha(stasisTurret, 0.08), zIndex: 2 },
  rangeEdge: {
    position: 'absolute', borderWidth: 2, borderStyle: 'dashed',
    borderColor: withAlpha(stasisTurret, 0.85), zIndex: 2,
  },
  rangeLabel: { position: 'absolute', width: 68, textAlign: 'center', color: stasisTurret, fontSize: 10, fontWeight: '700', zIndex: 7 },

  heroWrap: { alignItems: 'center' },
  heroName: { color: ON_SPACE, fontSize: 9, fontWeight: '700', marginBottom: 1 },
  heroNameMine: { color: stasisDoorOk },

  entityWrap: { alignItems: 'center' },
  entityLv: { fontSize: 9.5, fontWeight: '800', marginBottom: 1 },
  entityBar: { width: 44, height: 5, borderRadius: 3, backgroundColor: stasisSpace, overflow: 'hidden', marginBottom: 1 },
  entityBarFill: { height: '100%' },

  stick: {
    position: 'absolute', left: 18, bottom: 26, width: STICK_R * 2, height: STICK_R * 2,
    borderRadius: STICK_R, backgroundColor: withAlpha('#0F1A29', 0.9),
    borderWidth: 2, borderColor: '#2E4059', alignItems: 'center', justifyContent: 'center',
  },
  stickHeld: { borderColor: withAlpha(stasisRepair, 0.8), backgroundColor: withAlpha('#0F1A29', 0.98) },
  stickInner: {
    position: 'absolute', width: KNOB_R * 2 + 16, height: KNOB_R * 2 + 16,
    borderRadius: KNOB_R + 8, borderWidth: 1.5, borderColor: 'transparent',
  },
  stickInnerHeld: { borderColor: withAlpha(stasisRepair, 0.35) },
  stickKnob: {
    width: KNOB_R * 2, height: KNOB_R * 2, borderRadius: KNOB_R,
    backgroundColor: withAlpha(stasisRepair, 0.28), borderWidth: 2, borderColor: stasisRepair,
  },
  stickKnobHeld: { backgroundColor: withAlpha(stasisRepair, 0.55) },
  stickHint: { position: 'absolute', bottom: -16, color: ON_SPACE_SOFT, fontSize: 10 },
  stickPlaceholder: { position: 'absolute', left: 18, bottom: 26, width: STICK_R * 2, height: STICK_R * 2 },
});
