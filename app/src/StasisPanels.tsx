// 深空休眠的**面板层**：底部 sheet（建造 / 建筑 / 气密门 / 休眠舱）+ 三个浮层
// （全站、确认、结算）。
//
// 从 `StasisScreen.tsx` 拆出来的（那一屏原来 2700 行）。拆的判据不是「行数」而是
// **谁需要知道什么**：这些面板全都要读对局状态、全都要能发动作，但屏幕**不需要**
// 知道「建造菜单里每一项怎么排版」。拆开之后屏幕只管「面板开没开、点了调谁」。
//
// ── 每个面板只拿它自己要用的东西 ──────────────────────────────
//
// 一开始想把七个面板塞进一个 `StasisPanels(props)`，那样 props 会有二十几个 ——
// 一个组件接口宽到这个程度，读的人得先把整份 props 表背下来才知道它画什么，
// 拆分反而变成了搬家。所以这里是**七个独立的小组件，每个 4~9 个 props**。
//
// ── 两条从屏幕层带过来的规矩（改面板时别丢）──────────────────
//
// 1. **买不起 / 门槛没过时不禁用按钮**。RNW 下 `disabled` 等于
//    `pointer-events:none`，点了既没反应也没提示，用户只会觉得「点了没反应」
//    （重力四子棋满列的教训）。一律让它照常可点，由回调弹提示。
// 2. **前置条件必须写在弹窗里**。用户原话：「应该有个门的等级的前置条件，但是
//    升级弹窗中看不出来，生物能够了也没法升级」—— `PodSheet` 的 `gateNote`
//    就是为这句话存在的。
import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ScrollView } from 'react-native';
import { withAlpha, colors, stasisBio, stasisAlloy,
  stasisAlert, stasisDoorOk, stasisDoorLow, stasisGold } from './theme';
import { notify } from './utils';
import { Sheet, ModalM, HpBar, PrimaryButton, GhostButton } from './stasisWidgets';
import {
  BUILDINGS, BUILD_KINDS, MINE_TIERS, BED_RATE, REFINERY_RATE, TURRET_DPS,
  BED_COST, DOOR_COST, PATCH_REMOTE,
  REASON_LABELS, ENTITY_STATE_LABELS, ON_SPACE, ON_SPACE_SOFT,
  bedDoorReq, buildingColor, buildingName, buildingRate, costOf, doorName,
  mineRate, turretRange, upgradeAlloy, upgradeRes, upgradeTable,
} from './stasisRules';
import { buildingSprite } from './StasisBoard';
import type { MapGeom } from './stasisMap';
import type { StasisPayload, StasisRoomState } from './stasisTypes';

/** 共用闸门在面板里的样子：几何（`MapGeom.gates`）＋ 状态（`map.gates` ＋ tick）。 */
export interface GateView {
  level: number; hp: number; max: number; broken: boolean;
}

/** 每扇门的实时血量覆盖（5 Hz 的 tick 层），没收到就退回 `stasis_state` 里的值。
 *  `shield_ms` 是应急力场的剩余护盾时长（v13）—— 0 / 缺省 = 没开罩。 */
export type DoorHpMap = Record<string, { hp: number; max: number; shield_ms?: number }>;

/**
 * 升级价的文案。**第 6 级起还要搭合金**（v7），两个资源都得写出来 ——
 * 只写生物能的话玩家会以为攒够钱就能升，点下去收到一句「合金不够」
 * （原型那条「有前置但弹窗里看不出来」是同一类事故）。
 *
 * `res` 是**主资源**（`upgradeRes`）：矿那一族掏的是合金，文案不能写「生物能」。
 */
function priceLabel(table: number[], level: number, res: 'bio' | 'alloy' = 'bio'): string {
  const main = table[level];
  const unit = res === 'alloy' ? '合金' : '生物能';
  const alloy = res === 'alloy' ? 0 : upgradeAlloy(table, level);
  return alloy > 0 ? `${main} ${unit} + ${alloy} 合金` : `${main} ${unit}`;
}

// ── 建造 ────────────────────────────────────────────────

export function BuildSheet({ visible, onClose, cell, onBuild, canAfford, built }: {
  visible: boolean; onClose: () => void;
  /** 要建在哪一格（`"x,y"`）。面板开着的时候格号不会变，所以直接收下。 */
  cell: string | null;
  onBuild: (kind: string, cell: string) => void;
  canAfford: (res: 'bio' | 'alloy', amount: number) => boolean;
  /** 自己舱室里**已有**的建筑计数（按 kind）—— 用来显示「4/4 已满」。 */
  built: Record<string, number>;
}) {
  return (
    <Sheet visible={visible} onClose={onClose} title="建造">
      {BUILD_KINDS.map((kind) => {
        const info = BUILDINGS[kind];
        // **有上限的建筑（纳米修复器 4 台）到了上限要写出来**：不能只把它画灰，
        // 也不能把行 `disabled`（RNW 下等于 pointer-events:none，点了连提示都没有）。
        // 服务端也会拒（`MAX_REPAIRS`），两边同一句话。
        const full = info.max != null && (built[kind] ?? 0) >= info.max;
        const afford = canAfford(info.res, info.cost);
        return (
          <TouchableOpacity
            key={kind}
            testID={`stasis-build-${kind}`}
            style={[styles.buildRow, !afford && styles.buildRowPoor]}
            activeOpacity={0.8}
            onPress={() => { if (cell) onBuild(kind, cell); }}
          >
            <View style={[styles.buildIcon, { borderColor: buildingColor(kind) }]}>
              {buildingSprite(kind, 1, 34)}
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.buildName}>
                {info.name}
                {info.max != null
                  ? <Text style={styles.buildOrigin}>{`  ${built[kind] ?? 0}/${info.max}`}</Text>
                  : null}
              </Text>
              <Text style={styles.buildEffect}>
                {full ? `已经建满 ${info.max} 台了` : info.effect}
              </Text>
            </View>
            <Text style={[styles.buildCost, { color: info.res === 'bio' ? stasisBio : stasisAlloy }]}>
              {info.cost}
            </Text>
          </TouchableOpacity>
        );
      })}
    </Sheet>
  );
}

// ── 建筑（升级 / 拆除）──────────────────────────────────

/**
 * 某种建筑在 `level` 级「每秒产多少」的一句话。
 *
 * **四种建筑的产率单位完全不同**（DPS / 每秒合金 / 每秒生物能 / 每秒回血百分比），
 * 混在一句话里会读错，所以逐类写清。`asNext` 是「升级后」那一行的措辞差异
 * （炮台要把射程一起说出来）。
 */
function rateTextOf(kind: string, level: number, asNext = false): string {
  if (kind === 'turret') {
    return asNext ? `${TURRET_DPS[level - 1]} DPS · 射程 ${turretRange(level)} 格`
      : `${TURRET_DPS[level - 1]} DPS`;
  }
  if (kind === 'repair') return '每秒回本舱门 2% 血';
  if (kind === 'refinery') return `每秒 ${REFINERY_RATE[level - 1]} 合金`;
  return `每秒 ${mineRate(kind, level)} 生物能`;
}

export function BuildingSheet({ visible, onClose, cell, kind, level, onUpgrade, onDemolish }: {
  visible: boolean; onClose: () => void;
  cell: string | null; kind: string | null; level: number;
  /** `alloy` 是**第 6 级起额外要的那份合金**（0 = 这一级只要生物能）——
   *  屏幕层要两个资源一起判，只判生物能会画出一颗点下去被服务端拒掉的按钮。 */
  onUpgrade: (cell: string, cost: number | null, res: 'bio' | 'alloy', alloy: number) => void;
  onDemolish: (cell: string) => void;
}) {
  const table = kind ? upgradeTable(kind) : null;
  const cost = table ? costOf(table, level) : null;
  const res: 'bio' | 'alloy' = kind ? upgradeRes(kind) : 'bio';
  const rate = kind ? buildingRate(kind, level) : null;
  const nextRate = table && kind ? buildingRate(kind, level + 1) : null;
  const tier = kind ? MINE_TIERS.indexOf(kind) : -1;
  const nextTier = tier >= 0 ? MINE_TIERS[tier + 1] : undefined;
  // 每种建筑的「产率」单位不同（DPS / 每秒合金 / 每秒生物能 / 每秒回血百分比），
  // 混在一句话里会读错，所以逐类写清。
  const rateText = kind ? rateTextOf(kind, level) : '';
  const nextRateText = kind ? rateTextOf(kind, level + 1, true) : '';
  // **升一级可能连名字都换了**（v10：钻石矿 → 氪金矿）。不写出来的话，玩家点完
  // 升级只会看到等级从 1 变 2，不知道它已经变成另一种东西了。
  const curName = kind ? buildingName(kind, level) : '';
  const nextName = kind ? buildingName(kind, level + 1) : '';
  return (
    <Sheet visible={visible} onClose={onClose} title="建筑">
      {kind && cell ? (
        <>
          <Text style={styles.sheetLine}>
            {buildingName(kind, level)} Lv{level} · {rateText}
          </Text>
          {table ? (
            <PrimaryButton
              testID="stasis-upgrade"
              dim={cost === null}
              onPress={() => onUpgrade(cell, cost, res, upgradeAlloy(table, level))}
            >
              {cost === null
                ? '已满级'
                : `升级到 Lv${level + 1} · ${priceLabel(table, level, res)}`}
            </PrimaryButton>
          ) : (
            <Text style={styles.sheetNote}>这种建筑不能升级</Text>
          )}
          {nextRate ? (
            <Text style={styles.sheetNote}>
              {`升级后：${nextName !== curName ? `${nextName} · ` : ''}${nextRateText}`}
            </Text>
          ) : null}
          {kind === 'turret' ? (
            <Text style={styles.sheetNote}>
              {`射程 ${turretRange(level)} 格 —— 攻击范围已经画在地图上了。`}
            </Text>
          ) : null}
          {/* **矿那一族只有钻石矿能升**（v10 的氪金矿），其余三档仍是
              「拆了重造更划算吗」这道选择题，所以照旧把下一档的价与产率写出来。 */}
          {tier >= 0 && !table ? (
            <Text style={styles.sheetNote}>
              {`矿不能升级 —— 想换更快的矿，拆掉再建下一档（拆除返还一半建造费）。
下一档：${nextTier
                ? `${BUILDINGS[nextTier].name}（${mineRate(nextTier, 1)}/秒，${BUILDINGS[nextTier].cost} 合金）`
                : '已经是最高档'}`}
            </Text>
          ) : null}
          <GhostButton testID="stasis-demolish" onPress={() => onDemolish(cell)}>
            拆除（返还一半）
          </GhostButton>
        </>
      ) : null}
    </Sheet>
  );
}

// ── 气密门 ──────────────────────────────────────────────

export function DoorSheet({ visible, onClose, rid, level, hp, max, mine, ownerUid, onUpgrade, onPatch }: {
  visible: boolean; onClose: () => void;
  rid: string | null; level: number; hp: number; max: number;
  /** 自己的门能升级；**队友的门只能远程修补**（这是合作机制的另一半）。 */
  mine: boolean;
  ownerUid: number | null;
  onUpgrade: (cost: number | null, alloy: number) => void;
  onPatch: (uid: number | null) => void;
}) {
  const cost = costOf(DOOR_COST, level);
  return (
    <Sheet visible={visible} onClose={onClose} title="气密门">
      {rid ? (
        <>
          <Text style={styles.sheetLine}>{doorName(level)} · {Math.round(hp)}/{max} HP</Text>
          <HpBar hp={hp} max={max} height={10} />
          <Text style={styles.sheetNote}>
            升级会立刻把门血补满 —— 危急时刻的一次「倾家荡产换口气」。
          </Text>
          {mine ? (
            <PrimaryButton testID="stasis-door-upgrade" dim={cost === null}
              onPress={() => onUpgrade(cost, upgradeAlloy(DOOR_COST, level))}>
              {cost === null
                ? '已满级（振金门 3）'
                : `升级到 ${doorName(level + 1)}（满血）· ${priceLabel(DOOR_COST, level)}`}
            </PrimaryButton>
          ) : (
            <PrimaryButton testID="stasis-door-patch" onPress={() => onPatch(ownerUid)}>
              远程修补 · {PATCH_REMOTE} 生物能
            </PrimaryButton>
          )}
        </>
      ) : null}
    </Sheet>
  );
}

/**
 * **共用闸门**（v9，`suite` 图）：挡在同一个套间两间房外面的那扇门。
 *
 * 与 `DoorSheet` 分开写，因为它的语义有三处不同，混在一个组件里会变成一堆
 * `if`：① 它是**两个人的**，所以「能不能升」看的是「你住不住在它后面」而不是
 * 「这是不是你的房」；② 破了**不能重建**（房间门破了房间会重置）；③ 修补它
 * 一律按「帮别人」的 1.5 倍算（引擎的 `patch(gate=...)`）。
 */
export function GateSheet({ visible, onClose, gate, level, hp, max, mine,
                            contrib, nameOf, onUpgrade, onPatch }: {
  visible: boolean; onClose: () => void;
  /** 闸门 id（`null` = 不显示）。 */
  gate: string | null;
  level: number; hp: number; max: number;
  /** 只有**住在这扇闸门后面**的人能升级它（服务端的规则，这里只负责别说反话）。 */
  mine: boolean;
  /** **出资账本**（v13）：`uid -> {bio, alloy, acts}`，按出得多在前排。
   *  它解决的是「公共品搭便车」那条已知风险里**唯一被采纳的那一档：可见性**。 */
  contrib: Record<string, { bio: number; alloy: number; acts: number }>;
  /** uid → 显示名（队友就叫名字，自己叫「你」）。 */
  nameOf: (uid: number) => string;
  onUpgrade: (cost: number | null, alloy: number) => void;
  onPatch: () => void;
}) {
  const cost = costOf(DOOR_COST, level);
  return (
    <Sheet visible={visible} onClose={onClose} title="共用外闸">
      {gate ? (
        <>
          <Text style={styles.sheetLine}>{doorName(level)} · {Math.round(hp)}/{max} HP</Text>
          <HpBar hp={hp} max={max} height={10} />
          <Text style={styles.sheetNote}>
            它挡着这个套间的两间舱室 —— 破了就再也关不上了，里面的门会直接暴露。
          </Text>
          {mine ? (
            <PrimaryButton testID="stasis-gate-upgrade" dim={cost === null}
              onPress={() => onUpgrade(cost, upgradeAlloy(DOOR_COST, level))}>
              {cost === null
                ? '已满级（振金门 3）'
                : `升级到 ${doorName(level + 1)}（满血）· ${priceLabel(DOOR_COST, level)}`}
            </PrimaryButton>
          ) : null}
          <PrimaryButton testID="stasis-gate-patch" onPress={onPatch}>
            修补外闸 · {PATCH_REMOTE} 生物能
          </PrimaryButton>
          {/* **出资账本**（v13）：谁为这扇公共外闸掏过多少。
              按出得多在前排序 —— 它是「两个人都等对方升外闸」那个僵局的解药：
              把看不见的搭便车变成看得见的一行字。没有任何数值收益挂在这上面。 */}
          <Text style={styles.sheetNote}>
            {contribRows(contrib).length
              ? `出资：${contribRows(contrib).map(([uid, c]) => {
                  const parts = [];
                  if (c.bio > 0) parts.push(`${Math.round(c.bio)} 生物能`);
                  if (c.alloy > 0) parts.push(`${Math.round(c.alloy)} 合金`);
                  return `${nameOf(Number(uid))} ${parts.join(' + ')}（${c.acts} 次）`;
                }).join(' · ')}`
              : '还没有人在这扇外闸上花过钱 —— 它被砸破就再也关不上了。'}
          </Text>
        </>
      ) : null}
    </Sheet>
  );
}

/** 出资账本按「出得多在前」排序（生物能 + 合金当量都算）—— 出得多的人在最前面，
 *  一眼看得出谁在扛这扇公共门。**只影响展示顺序，不产生任何数值收益。** */
function contribRows(contrib: Record<string, { bio: number; alloy: number; acts: number }>) {
  return Object.entries(contrib ?? {})
    .sort((a, b) => (b[1].bio + b[1].alloy) - (a[1].bio + a[1].alloy));
}


// ── 休眠舱 ──────────────────────────────────────────────

export function PodSheet({ visible, onClose, bedLevel, doorLevel, lying, onLie, onUpgrade }: {
  visible: boolean; onClose: () => void;
  bedLevel: number;
  /** 自己那扇门现在的等级 —— 用来算「升床还差不差门」。**拿不到就不算门槛**
   *  （`null`）：房间数据还没到的时候不该凭空拦人一道。 */
  doorLevel: number | null;
  lying: boolean;
  onLie: () => void;
  onUpgrade: (cost: number | null, alloy: number) => void;
}) {
  const cost = costOf(BED_COST, bedLevel);
  // **门槛要看得见**：升床要求气密门跟上（服务端的硬规则）。以前这条只表现为
  // 「点了没反应」，用户只能猜（原话：「应该有个门的等级的前置条件，但是升级
  // 弹窗中看不出来」）。
  //
  // **门槛是间断的**（v4 对齐原型）：`BED_DOOR_REQ` 说「升到 Lv N 需要门几级」
  // —— 门 2 就能把床顶到 4，然后一路卡到门 5。所以文案要说清「下一级需要多少」，
  // 而不是「门要跟上床」。
  const want = bedDoorReq(bedLevel + 1);
  // 门数据拿不到时按「已达标」算（见 `doorLevel` 的注释）—— 写成 `have = want`
  // 就不必在下面再补一次 `doorLevel != null`，那个条件读起来像是漏了什么。
  const have = doorLevel ?? want;
  const blocked = cost !== null && have < want;
  return (
    <Sheet visible={visible} onClose={onClose} title="休眠舱">
      <Text style={styles.sheetLine}>
        Lv{bedLevel} · 每秒 {BED_RATE[bedLevel - 1]} 生物能
        {lying ? ' · 已躺下' : ' · 未躺下（不产）'}
      </Text>
      {/* 没有「起身」按钮了（v4）：躺下不可逆。所以这里只在**还没躺**时给躺下
          按钮，躺下之后这一段只剩一句状态说明。 */}
      {lying ? (
        <Text style={styles.sheetNote}>
          已经躺下了 —— 躺下不能反悔，整局都在这张床上发育、建造、远程救队友。
        </Text>
      ) : (
        <>
          <Text style={styles.sheetNote}>
            走进舱室、走到休眠舱旁边，点「躺下」才开始产出与建造；躺下之后不能再起身。
          </Text>
          <PrimaryButton testID="stasis-lie-sheet" onPress={onLie}>躺下</PrimaryButton>
        </>
      )}
      {blocked ? (
        <Text style={styles.gateNote}>
          休眠舱升到 Lv{bedLevel + 1} 需要{doorName(want)} —— 现在
          {doorName(have)}。先把门升上去（点自己的门）。
        </Text>
      ) : null}
      <PrimaryButton
        testID="stasis-pod-upgrade"
        dim={cost === null || blocked}
        // 门槛没过时**不禁用按钮**（RNW 下 disabled = pointer-events:none，
        // 点了连提示都没有）—— 让它弹一句「先把门升上去」。
        onPress={() => {
          if (blocked) { notify(`先把气密门升到${doorName(want)}`); return; }
          onUpgrade(cost, upgradeAlloy(BED_COST, bedLevel));
        }}
      >
        {cost === null
          ? '已满级（512/秒）'
          : blocked
            ? `需${doorName(want)}`
            : `升级到 Lv${bedLevel + 1} · ${priceLabel(BED_COST, bedLevel)}`}
      </PrimaryButton>
    </Sheet>
  );
}

// ── 全站浮层：缩小看整层楼 ──────────────────────────────

export function WholeStation({ visible, onClose, map, rooms, doorHp, gates, myUid, entity, nameOf, aiOf, seatColorOf, onPatch, onPatchGate }: {
  visible: boolean; onClose: () => void;
  /** 这一局的几何 —— 房间清单与闸门位置都从它来（v9 起没有模块级常量了）。 */
  map: MapGeom;
  rooms: Record<string, StasisRoomState>;
  doorHp: DoorHpMap;
  /** 共用闸门的几何 + 状态（屏幕层把 tick 的血量并进 `map.gates`）。 */
  gates: Record<string, GateView>;
  myUid: number | null;
  entity: StasisPayload['entity'] | null;
  nameOf: (uid: number) => string;
  /** 人机的性格名（真人返回 null）。**显示出来玩家才知道这队友是什么路数** ——
   *  一个只顾升门的「龟缩」和一个把钱全砸炮台的「莽夫」看起来完全不同，
   *  不标的话玩家只会觉得「这人机怎么乱造」。 */
  aiOf?: (uid: number) => string | null;
  seatColorOf: (uid: number) => string;
  onPatch: (uid: number) => void;
  /** 修补**共用闸门**（v9）。它挡的是两个人，所以列在最前面。 */
  onPatchGate: (gid: string) => void;
}) {
  const gateIds = Object.keys(gates);
  return (
    <ModalM visible={visible} onClose={onClose} wide>
      <Text style={styles.modalTitle}>全站</Text>
      <Text style={styles.modalSub}>
        静默体 {entity ? `Lv${entity.level} · ${ENTITY_STATE_LABELS[entity.state] ?? entity.state}` : '—'}
      </Text>
      <ScrollView style={{ maxHeight: 380 }} contentContainerStyle={{ paddingVertical: 4 }}>
        {/* 共用闸门排在最前：它是**两个人的**前线，破了就再也没有了，
            而玩家最容易漏掉的恰恰是这扇不属于自己的门。 */}
        {gateIds.map((gid) => {
          const g = map.gates[gid];
          const st = gates[gid];
          if (!g || !st) return null;
          const mine = (myUid != null) && g.rooms.some((r) => rooms[r]?.owner === myUid);
          return (
            <TouchableOpacity
              key={gid}
              testID={`stasis-overlay-gate-${gid}`}
              style={[styles.ovRow, styles.ovGateRow]}
              activeOpacity={st.broken ? 1 : 0.7}
              onPress={() => { if (!st.broken && mine) { onPatchGate(gid); onClose(); } }}
            >
              <Text style={styles.ovRoom}>外闸</Text>
              <View style={[styles.ovDot, { backgroundColor: st.broken ? withAlpha(ON_SPACE_SOFT, 0.35) : stasisGold }]} />
              <Text style={styles.ovName} numberOfLines={1}>
                {st.broken ? '已砸破' : `${g.rooms.join('/')} 共用`}
              </Text>
              <View style={{ flex: 1, marginHorizontal: 8 }}>
                <HpBar hp={st.hp} max={st.max} height={6} />
              </View>
              <Text style={styles.ovLv}>{st.broken ? '—' : `Lv${st.level}`}</Text>
            </TouchableOpacity>
          );
        })}
        {map.roomIds.map((rid) => {
          const r = rooms[rid];
          if (!r) return null;
          const owner = r.owner;
          const hp = doorHp[rid]?.hp ?? r.door_hp;
          const max = doorHp[rid]?.max ?? r.door_max;
          const patched = owner != null && owner !== myUid;
          const ai = owner != null && aiOf ? aiOf(owner) : null;
          return (
            <TouchableOpacity
              key={rid}
              testID={`stasis-overlay-door-${rid}`}
              style={styles.ovRow}
              activeOpacity={patched ? 0.7 : 1}
              onPress={() => { if (patched) { onPatch(owner); onClose(); } }}
            >
              <Text style={styles.ovRoom}>{rid}</Text>
              <View style={[styles.ovDot, { backgroundColor: owner != null ? seatColorOf(owner) : withAlpha(ON_SPACE_SOFT, 0.35) }]} />
              <Text style={styles.ovName} numberOfLines={1}>
                {owner == null ? '空置' : nameOf(owner)}
              </Text>
              {ai ? <Text style={styles.ovAi}>{ai}</Text> : null}
              <View style={{ flex: 1, marginHorizontal: 8 }}>
                <HpBar hp={hp} max={max} height={6} />
              </View>
              <Text style={styles.ovLv}>Lv{r.door_level}</Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>
      <GhostButton testID="stasis-overlay-close" onPress={onClose}>关闭</GhostButton>
    </ModalM>
  );
}

// ── 确认（弃守 / 离开）──────────────────────────────────

export function ConfirmDialog({ kind, onCancel, onConfirm }: {
  /** `surrender` 弃守 / `leave` 离开对局；`null` 表示不显示。 */
  kind: 'surrender' | 'leave' | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <ModalM visible={kind !== null} onClose={onCancel}>
      <Text style={styles.modalTitle}>
        {kind === 'surrender' ? '确认弃守？' : '确认离开对局？'}
      </Text>
      <Text style={styles.modalSub}>
        {kind === 'surrender'
          ? '你的舱室会立刻失压，遗产平分给队友，他们继续打。'
          : '中途离开算你失压出局，遗产同样分给队友。'}
      </Text>
      <View style={styles.modalRow}>
        <GhostButton testID="stasis-cancel" onPress={onCancel}>取消</GhostButton>
        <PrimaryButton testID="stasis-confirm" style={{ flex: 1, backgroundColor: colors.danger }} onPress={onConfirm}>
          {kind === 'surrender' ? '确认弃守' : '确认离开'}
        </PrimaryButton>
      </View>
    </ModalM>
  );
}

// ── 结算 ────────────────────────────────────────────────

export function ResultDialog({ visible, game, nameOf, seatColorOf, onBack }: {
  visible: boolean;
  game: StasisPayload | null;
  nameOf: (uid: number) => string;
  seatColorOf: (uid: number) => string;
  onBack: () => void;
}) {
  return (
    <ModalM visible={visible} onClose={onBack} wide>
      {/* **标题跟着「唯一获胜条件」走**（v8）：赢只可能是打死了静默体，
          所以不写「守住了」—— 那读起来像「撑满了时间」。 */}
      <Text style={styles.modalTitle}>
        {game?.winner === 'players' ? '静默体被击杀' : '任务失败'}
      </Text>
      <Text style={styles.modalSub}>{REASON_LABELS[game?.reason ?? ''] ?? '对局结束'}</Text>
      <ScrollView style={{ maxHeight: 300 }} contentContainerStyle={{ paddingVertical: 4 }}>
        {(game?.seat_uids ?? []).map((uid) => {
          const p = game?.players?.[String(uid)];
          if (!p) return null;
          return (
            <View key={uid} style={styles.ovRow}>
              <View style={[styles.ovDot, { backgroundColor: seatColorOf(uid) }]} />
              <Text style={styles.ovName} numberOfLines={1}>{nameOf(uid)}</Text>
              <Text style={styles.ovLv}>{p.room ?? '—'}</Text>
              <Text style={[styles.ovLv, { marginLeft: 8, color: p.alive ? stasisDoorOk : stasisDoorLow }]}>
                {p.alive ? '存活' : p.surrendered ? '弃守' : '失压'}
              </Text>
            </View>
          );
        })}
      </ScrollView>
      <PrimaryButton testID="stasis-result-back" onPress={onBack}>返回房间</PrimaryButton>
    </ModalM>
  );
}

const styles = StyleSheet.create({
  sheetLine: { color: ON_SPACE, fontSize: 14, fontWeight: '600' },
  sheetNote: { color: ON_SPACE_SOFT, fontSize: 12, lineHeight: 18 },
  // 门槛提示：**警示色 + 文字**双通道（WCAG 1.4.1），文案里带具体等级。
  gateNote: {
    color: stasisAlert, fontSize: 12, lineHeight: 18, fontWeight: '700',
    backgroundColor: withAlpha(stasisAlert, 0.12), borderRadius: 8, padding: 8,
  },

  buildRow: {
    flexDirection: 'row', alignItems: 'center', gap: 12, padding: 10, borderRadius: 10,
    backgroundColor: '#152238', borderWidth: 1, borderColor: '#243349',
  },
  buildRowPoor: { opacity: 0.6 },
  buildIcon: { width: 34, height: 34, borderRadius: 8, borderWidth: 2, alignItems: 'center', justifyContent: 'center', backgroundColor: withAlpha('#000', 0.25) },
  buildName: { color: ON_SPACE, fontSize: 14, fontWeight: '600' },
  // 跟在主名后面的**计数**（「纳米修复器 4/4」）。降一档字号与亮度，不抢主名。
  buildOrigin: { color: ON_SPACE_SOFT, fontSize: 11, fontWeight: '400' },
  buildEffect: { color: ON_SPACE_SOFT, fontSize: 11, marginTop: 1 },
  buildCost: { fontSize: 14, fontWeight: '800' },

  modalTitle: { color: ON_SPACE, fontSize: 18, fontWeight: '800' },
  modalSub: { color: ON_SPACE_SOFT, fontSize: 12.5, lineHeight: 18 },
  modalRow: { flexDirection: 'row', gap: 10, marginTop: 4 },

  ovRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 7 },
  // 共用闸门那两行加一条底色 + 左侧金线：它不属于任何一个人，
  // 混在房间列表里会被当成「又一间房」。
  ovGateRow: {
    backgroundColor: withAlpha(stasisGold, 0.10),
    borderLeftWidth: 3, borderLeftColor: withAlpha(stasisGold, 0.7), paddingLeft: 6,
  },
  ovRoom: { color: ON_SPACE, fontSize: 12, fontWeight: '700', width: 26 },
  ovDot: { width: 10, height: 10, borderRadius: 5, marginRight: 7 },
  ovName: { color: ON_SPACE, fontSize: 12.5, width: 66 },
  ovAi: { color: stasisGold, fontSize: 10, fontWeight: '700', marginLeft: -6, marginRight: 4 },
  ovLv: { color: ON_SPACE_SOFT, fontSize: 11 },
});
