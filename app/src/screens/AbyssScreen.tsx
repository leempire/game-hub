import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import type { GestureResponderEvent } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AbyssBoard from '../AbyssBoard';
import {
  abyssClock, abyssItemInfo, abyssLevelOf, abyssPassives, abyssWeapons,
  type AbyssChoice, type AbyssPassive, type AbyssState, type AbyssWeapon, type XY,
} from '../abyss';
import { leaveRoom } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave, useWebHeldKeys } from '../backGuard';
import { RoomSocket } from '../socket';
import { abyssColors as c } from '../theme';
import { AppModal } from '../ui';

const keys: Record<string, XY> = {
  w: [0, -1], a: [-1, 0], s: [0, 1], d: [1, 0],
  arrowup: [0, -1], arrowleft: [-1, 0], arrowdown: [0, 1], arrowright: [1, 0],
};
const results = { won: '回收完成', lost: '全队失压', timeout: '断缆守卫仍在活动', abandoned: '试潜中止' };

interface Props { code: string; onBack: () => void; onExit: () => void }

export default function AbyssScreen({ code, onBack, onExit }: Props) {
  const { token, user } = useAuth();
  const navigation = useNavigation();
  const insets = useSafeAreaInsets();
  const socket = useRef<RoomSocket | null>(null);
  const input = useRef<XY>([0, 0]);
  const drag = useRef<XY>([0, 0]);
  const held = useRef(new Set<string>());
  const lastDragSend = useRef(0);
  const dragOrigin = useRef<XY>([0, 0]);
  const canLeave = useRef(false);
  const [game, setGame] = useState<AbyssState | null>(null);
  const [connected, setConnected] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [inventory, setInventory] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState('');
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    ws.onAny(msg => {
      if (msg.type === 'sync') {
        const state = (msg.game ?? msg.abyss_result) as AbyssState | undefined;
        if (state) setGame(state);
        setWaiting(!state);
        setConnected(true);
      } else if (msg.type === 'abyss_state' || msg.type === 'game_start' || msg.type === 'game_over') {
        if (msg.game) {
          setWaiting(false);
          setGame(msg.game as AbyssState);
        }
      } else if (msg.type === 'room_closed') {
        setWaiting(true);
      }
    });
    ws.connect(undefined, () => setConnected(false));
    return () => {
      socket.current = null;
      ws.close();
    };
  }, [code, token]);

  useEffect(() => {
    const requestLeave = () => setConfirm(true);
    const native = navigation.addListener('beforeRemove', event => {
      if (Platform.OS === 'web' || canLeave.current) return;
      event.preventDefault();
      requestLeave();
    });
    const web = onConfirmLeave(requestLeave);
    return () => { native(); web(); };
  }, [navigation]);

  const me = game?.players.find(p => p.id === user?.id);
  const active = connected && !!me?.hp && !game?.choice_seconds && !game?.result && !confirm && !inventory && !waiting;

  // 升级暂停时把装备面板收起：否则 AppModal 会盖住升级候选，6 秒后自动选第一项，
  // 玩家会以为「游戏自己选了」。确认弹窗是用户主动打开的，保留让他自己取消。
  useEffect(() => {
    if (game?.choice_seconds) setInventory(false);
  }, [game?.choice_seconds]);

  const updateInput = useCallback(() => {
    let [x, y] = drag.current;
    for (const key of held.current) {
      x += keys[key][0];
      y += keys[key][1];
    }
    const length = Math.max(1, Math.hypot(x, y));
    input.current = [x / length, y / length];
    socket.current?.send({ type: 'abyss_move', dx: input.current[0], dy: input.current[1] });
  }, []);

  useEffect(() => {
    if (!active) {
      held.current.clear();
      drag.current = [0, 0];
      updateInput();
      return;
    }
    const heartbeat = setInterval(() => {
      if (input.current[0] || input.current[1]) updateInput();
    }, 110);
    return () => clearInterval(heartbeat);
  }, [active, updateInput]);

  useWebHeldKeys(
    key => {
      if (!Object.hasOwn(keys, key)) return false;
      if (!held.current.has(key)) {
        held.current.add(key);
        updateInput();
      }
      return true;
    },
    key => {
      if (held.current.delete(key)) updateInput();
    },
    () => {
      held.current.clear();
      drag.current = [0, 0];
      updateInput();
    },
    active,
  );

  const stopDrag = useCallback(() => {
    drag.current = [0, 0];
    updateInput();
  }, [updateInput]);

  const moveDrag = useCallback((dx: number, dy: number) => {
    const length = Math.max(42, Math.hypot(dx, dy));
    drag.current = [dx / length, dy / length];
    const now = Date.now();
    if (now - lastDragSend.current >= 48) {
      lastDragSend.current = now;
      updateInput();
    }
  }, [updateInput]);

  function startDrag({ nativeEvent }: GestureResponderEvent) {
    dragOrigin.current = [nativeEvent.pageX, nativeEvent.pageY];
    stopDrag();
  }

  function followDrag({ nativeEvent }: GestureResponderEvent) {
    if (!active) return;
    moveDrag(nativeEvent.pageX - dragOrigin.current[0], nativeEvent.pageY - dragOrigin.current[1]);
  }

  function back() {
    canLeave.current = true;
    onBack();
  }

  async function exit() {
    if (!token || leaving) return;
    setLeaving(true);
    setError('');
    try {
      await leaveRoom(token, code);
      canLeave.current = true;
      onExit();
    } catch {
      setError('离开失败，请重试。');
    } finally {
      setLeaving(false);
    }
  }

  function choose(choice: AbyssChoice) {
    if (connected && game) socket.current?.send({ type: 'abyss_pick', level: game.level, choice });
  }

  const boss = game?.enemies.find(e => e[1] === 'warden');
  const chest = game?.chest_events.filter(event => game.frame - event.frame < 60).slice(-1)[0];
  const weaponEntries = me ? Object.entries(me.weapons) as [AbyssWeapon, number][] : [];
  const passiveEntries = me ? Object.entries(me.passives) as [AbyssPassive, number][] : [];
  return <View style={[styles.page, { paddingBottom: insets.bottom }]}>
    <View style={styles.hud}>
      <View style={styles.readout}><Text style={styles.label}>潜水服</Text><Text testID="abyss-hp" style={styles.value}>{me ? `${Math.ceil(me.hp)} / ${Math.round(me.max_hp)}` : '—'}</Text></View>
      <View style={styles.readout}><Text style={styles.label}>沉没回收站</Text><Text testID="abyss-clock" style={styles.value}>{abyssClock(game ? game.duration - game.seconds : 720)}</Text></View>
      <View style={styles.readout}><Text style={styles.label}>等级 {game?.level ?? 1}</Text><Text style={styles.value}>{game?.xp ?? 0} / {game?.next_xp ?? 5} XP</Text></View>
    </View>
    {!!game && game.players.length > 1 && <View style={styles.team}>
      {game.players.map(p => <View key={p.id} style={styles.teammate}>
        <Text style={styles.label} numberOfLines={1}>{p.id === user?.id ? '你' : p.name}</Text>
        <Text style={styles.text}>{p.out ? '出局' : p.hp ? `${Math.ceil(p.hp)} HP` : `待救 ${p.down}s`}</Text>
      </View>)}
    </View>}
    {boss && <View style={styles.boss}><Text style={styles.label}>断缆守卫 · {boss[5]} / {boss[6]}</Text>
      <View style={styles.bar}><View style={[styles.bossFill, { width: `${100 * boss[5] / boss[6]}%` }]} /></View>
    </View>}
    <View testID="abyss-arena" style={[styles.arena, Platform.OS === 'web' && ({ touchAction: 'none' } as object)]}
      onLayout={e => setSize(e.nativeEvent.layout)}
      onStartShouldSetResponder={() => active}
      onResponderGrant={startDrag} onResponderMove={followDrag}
      onResponderRelease={stopDrag} onResponderTerminate={stopDrag}>
      {game && <AbyssBoard game={game} uid={user?.id} size={size} input={input} connected={connected} />}
      {chest && <View testID="abyss-chest" pointerEvents="none" style={styles.chestBanner}>
        <Text style={styles.chestText}>{chest.text}</Text>
      </View>}
      {!game && !waiting && <View style={styles.center}><ActivityIndicator color={c.signal} /><Text style={styles.label}>正在同步潜区…</Text></View>}
      {game && !connected && !game.result && <View pointerEvents="none" style={styles.notice}><Text style={styles.text}>正在重连…</Text></View>}
      {me && !me.hp && !game?.result && <View pointerEvents="none" style={styles.notice}>
        <Text style={styles.text}>{me.out ? '观战中' : `待救援 ${me.down}s · 救援进度 ${Math.floor(me.rescue / 4) * 10}%`}</Text>
      </View>}
      {(game?.result || waiting || !!game?.choice_seconds) && <View style={styles.overlay}>
        <View style={styles.card}>
          {game?.result || waiting ? <>
            <Text style={styles.title}>{game?.result ? results[game.result] : '对局已结束'}</Text>
            {game && <Text style={styles.label}>全队击退 {game.kills} 只 · 等级 {game.level} · 打捞分 {game.salvage} · {abyssClock(game.seconds)}</Text>}
            <Pressable style={styles.primary} accessibilityRole="button" onPress={back}><Text style={styles.primaryText}>返回房间</Text></Pressable>
          </> : <>
            <Text style={styles.title}>选择强化 · {game?.choice_seconds}s</Text>
            {me?.choices.length ? me.choices.map(choice => {
              const info = abyssItemInfo(choice);
              const rank = abyssLevelOf(me, choice);
              const suffix = info.kind === 'utility' ? '' : rank ? ` Lv.${rank} → ${rank + 1}` : ' 新';
              return <Pressable key={choice} testID={`abyss-choice-${choice}`} style={styles.choice} accessibilityRole="button" onPress={() => choose(choice)} disabled={!connected}>
                <Text style={styles.text}>{info.title}{suffix}</Text>
                <Text style={styles.label}>{info.detail}</Text>
              </Pressable>;
            }) : <Text style={styles.label}>{me && !me.hp ? '倒地中，等待队友救援' : '等待队友选择'}</Text>}
          </>}
        </View>
      </View>}
    </View>
    <View style={styles.footer}>
      <View style={styles.equipment}>
        <Text style={styles.label}>{me ? `武器 ${weaponEntries.length}/6 · 被动 ${passiveEntries.length}/6 · 打捞分 ${game?.salvage ?? 0}` : '等待同步'}</Text>
        <Text style={styles.label}>{Platform.OS === 'web' ? 'WASD / 方向键或拖动移动' : '在战场内拖动移动'}</Text>
      </View>
      <Pressable testID="abyss-inventory" style={styles.plain} accessibilityRole="button" onPress={() => setInventory(true)}><Text style={styles.text}>装备</Text></Pressable>
      <Pressable testID="abyss-leave" style={styles.plain} accessibilityRole="button" onPress={() => setConfirm(true)}><Text style={styles.text}>离开</Text></Pressable>
    </View>
    <AppModal visible={inventory} onClose={() => setInventory(false)} cardStyle={styles.sheet} testID="abyss-inventory-modal">
      <Text style={styles.title}>装备与属性</Text>
      {!me ? <Text style={styles.label}>等待同步</Text> : <>
        <Text style={styles.section}>武器 {weaponEntries.length}/6</Text>
        {weaponEntries.length ? weaponEntries.map(([weapon, level]) =>
          <View key={weapon} style={styles.inventoryRow}>
            <Text style={styles.text}>{abyssWeapons[weapon].title}</Text>
            <Text style={styles.label}>Lv.{level}</Text>
          </View>
        ) : <Text style={styles.label}>暂无武器</Text>}
        <Text style={styles.section}>被动 {passiveEntries.length}/6</Text>
        {passiveEntries.length ? passiveEntries.map(([passive, level]) =>
          <View key={passive} style={styles.inventoryRow}>
            <Text style={styles.text}>{abyssPassives[passive].title}</Text>
            <Text style={styles.label}>Lv.{level}</Text>
          </View>
        ) : <Text style={styles.label}>暂无被动</Text>}
        <Text style={styles.section}>属性</Text>
        <View style={styles.stats}>
          <Text style={styles.label}>伤害 +{Math.round((me.stats.might - 1) * 100)}%</Text>
          <Text style={styles.label}>范围 +{Math.round((me.stats.area - 1) * 100)}%</Text>
          <Text style={styles.label}>冷却 -{Math.round((1 - me.stats.cooldown) * 100)}%</Text>
          <Text style={styles.label}>持续 +{Math.round((me.stats.duration - 1) * 100)}%</Text>
          <Text style={styles.label}>弹速 +{Math.round((me.stats.speed - 1) * 100)}%</Text>
          <Text style={styles.label}>数量 +{me.stats.amount}</Text>
          <Text style={styles.label}>移速 +{Math.round((me.move_speed / 158 - 1) * 100)}%</Text>
          <Text style={styles.label}>磁吸 x{me.stats.magnet.toFixed(2)}</Text>
          <Text style={styles.label}>幸运 +{Math.round((me.stats.luck - 1) * 100)}%</Text>
          <Text style={styles.label}>成长 +{Math.round((me.stats.growth - 1) * 100)}%</Text>
          <Text style={styles.label}>护甲 {me.stats.armor}</Text>
          <Text style={styles.label}>回复 {me.stats.recovery.toFixed(1)}/s</Text>
        </View>
      </>}
      <Pressable testID="abyss-inventory-close" style={styles.primary} accessibilityRole="button" onPress={() => setInventory(false)}><Text style={styles.primaryText}>关闭</Text></Pressable>
    </AppModal>
    <AppModal visible={confirm} onClose={() => setConfirm(false)} cardStyle={styles.sheet} testID="abyss-confirm-modal">
      <Text style={styles.title}>{game?.result || waiting ? '离开房间？' : '结束本次试潜？'}</Text>
      {!game?.result && !waiting && <Text style={styles.label}>你将退出对局，队友可继续作战。</Text>}
      {!!error && <Text style={styles.label}>{error}</Text>}
      <Pressable style={styles.primary} accessibilityRole="button" disabled={leaving} onPress={() => void exit()}>
        <Text style={styles.primaryText}>{leaving ? '正在离开…' : '确认离开'}</Text>
      </Pressable>
      <Pressable style={styles.plain} accessibilityRole="button" disabled={leaving} onPress={() => setConfirm(false)}><Text style={styles.text}>取消</Text></Pressable>
    </AppModal>
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: c.deep },
  hud: { flexDirection: 'row', padding: 8, gap: 6 },
  readout: { flex: 1, padding: 7, borderWidth: 1, borderColor: c.edge, borderRadius: 8, backgroundColor: c.panel },
  label: { color: c.muted, fontSize: 13 },
  value: { color: c.signal, fontSize: 15, fontWeight: '700', fontVariant: ['tabular-nums'] },
  arena: { flex: 1, overflow: 'hidden', borderTopWidth: 1, borderBottomWidth: 1, borderColor: c.edge },
  team: { flexDirection: 'row', gap: 6, paddingHorizontal: 8, paddingBottom: 8 },
  teammate: { flex: 1, paddingHorizontal: 6, paddingVertical: 4, borderRadius: 5, backgroundColor: c.panel },
  boss: { paddingHorizontal: 12, paddingBottom: 7, gap: 4 },
  bar: { height: 5, backgroundColor: c.panel, overflow: 'hidden', borderRadius: 5 },
  bossFill: { height: 5, backgroundColor: c.warden },
  footer: { flexDirection: 'row', paddingHorizontal: 8, alignItems: 'center', gap: 8 },
  equipment: { flex: 1, padding: 6, gap: 2 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  notice: { position: 'absolute', bottom: 14, alignSelf: 'center', borderRadius: 8, backgroundColor: c.panel, padding: 8 },
  chestBanner: { position: 'absolute', top: 8, alignSelf: 'center', maxWidth: '92%', borderRadius: 8,
    backgroundColor: c.panel, borderWidth: 1, borderColor: c.chest, paddingHorizontal: 10, paddingVertical: 6 },
  chestText: { color: c.chest, fontSize: 13, fontWeight: '700' },
  overlay: { ...StyleSheet.absoluteFill, backgroundColor: c.shadow + 'C9', justifyContent: 'center', alignItems: 'center', padding: 14 },
  card: { width: '100%', maxWidth: 400, backgroundColor: c.panel, borderColor: c.edge, borderWidth: 1, borderRadius: 14, padding: 12, gap: 8 },
  sheet: { backgroundColor: c.panel, borderColor: c.edge, borderWidth: 1 },
  title: { color: c.text, fontSize: 21, fontWeight: '800' },
  text: { color: c.text, fontSize: 15, fontWeight: '600' },
  section: { color: c.signal, fontSize: 14, fontWeight: '700', marginTop: 4 },
  choice: { minHeight: 48, backgroundColor: c.deep, padding: 7, borderRadius: 7, borderWidth: 1, borderColor: c.edge },
  inventoryRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    backgroundColor: c.deep, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 5 },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
  primary: { minHeight: 44, justifyContent: 'center', alignItems: 'center', backgroundColor: c.signal, borderRadius: 8 },
  primaryText: { color: c.deep, fontWeight: '800' },
  plain: { minHeight: 44, minWidth: 56, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8 },
});