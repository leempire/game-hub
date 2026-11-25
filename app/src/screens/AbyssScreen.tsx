import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Animated, AppState, Image, Platform, Pressable, ScrollView, StyleSheet, Text, Vibration, View } from 'react-native';
import type { GestureResponderEvent } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import AbyssBoard from '../AbyssBoard';
import AbyssMinimap, { abyssMinimapLayout } from '../AbyssMinimap';
import AbyssAudio from '../AbyssAudio';
import AbyssResult from '../AbyssResult';
import { abyssItemArt } from '../abyssItemArt';
import {
  abyssChangeText, abyssClock, abyssEvolutionPreview, abyssItemInfo, abyssLevelOf, abyssLowPressure, abyssNewWeaponText, abyssPassives, abyssRescue, abyssResults, abyssVibrationDuration, abyssWeapons,
  type AbyssChoice, type AbyssPassive, type AbyssState, type AbyssWeapon, type XY,
} from '../abyss';
import { leaveRoom, type RoomPlayer } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave, useWebHeldKeys } from '../backGuard';
import { RoomSocket } from '../socket';
import { abyssColors as c } from '../theme';
import { AppModal } from '../ui';

const dragSendInterval = 48;
const vibrationKey = 'abyss.feedback.vibration';
const upgradeRetryMs = 900;

const keys: Record<string, XY> = {
  w: [0, -1], a: [-1, 0], s: [0, 1], d: [1, 0],
  arrowup: [0, -1], arrowleft: [-1, 0], arrowdown: [0, 1], arrowright: [1, 0],
};

interface Props { code: string; onBack: () => void; onExit: () => void }

export default function AbyssScreen({ code, onBack, onExit }: Props) {
  const { token, user } = useAuth();
  const navigation = useNavigation();
  const focused = useIsFocused();
  const [foreground, setForeground] = useState(() => !AppState.currentState || AppState.currentState === 'active');
  const insets = useSafeAreaInsets();
  const socket = useRef<RoomSocket | null>(null);
  const lastSnapshotAt = useRef(0);
  const input = useRef<XY>([0, 0]);
  const drag = useRef<XY>([0, 0]);
  const held = useRef(new Set<string>());
  const lastInputSend = useRef(-Infinity);
  const pendingInput = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dragOrigin = useRef<{ id: string; x: number; y: number } | null>(null);
  const [stick] = useState(() => ({
    anchor: new Animated.ValueXY(),
    thumb: new Animated.ValueXY(),
    opacity: new Animated.Value(0),
  }));
  const previousGame = useRef<AbyssState | null>(null);
  const lastVibration = useRef(-Infinity);
  const canLeave = useRef(false);
  const submittedChoice = useRef<{ level: number; sentAt: number } | null>(null);
  const [pendingChoice, setPendingChoice] = useState<AbyssChoice | null>(null);
  const [game, setGame] = useState<AbyssState | null>(null);
  const [connected, setConnected] = useState(false);
  const [stalled, setStalled] = useState(false);
  const [delayed, setDelayed] = useState(false);
  const [offlinePeers, setOfflinePeers] = useState<ReadonlySet<number>>(() => new Set());
  const [waiting, setWaiting] = useState(false);
  const [watchedId, setWatchedId] = useState<number | undefined>(undefined);
  const [confirm, setConfirm] = useState(false);
  const [inventory, setInventory] = useState(false);
  const [audioVisible, setAudioVisible] = useState(false);
  const [vibration, setVibration] = useState<boolean | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [error, setError] = useState('');
  const [bannerHeight, setBannerHeight] = useState(0);
  const [noticeHeight, setNoticeHeight] = useState(0);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    let threateningAttack: number | undefined;
    let offline = new Set<number>();
    let latestState: AbyssState | null = null;
    function updateSpectator() {
      const players = latestState?.players;
      if (!players?.some(player => player.id === user?.id && player.out)) {
        setWatchedId(undefined);
        return;
      }
      const living = players.filter(player => player.hp > 0);
      const online = living.filter(player => !offline.has(player.id));
      const candidates = online.length ? online : living;
      setWatchedId(current => candidates.find(player => player.id === current)?.id ?? candidates[0]?.id);
    }
    function updateOfflinePeers(peers: Set<number>) {
      offline = peers;
      setOfflinePeers(peers);
      updateSpectator();
    }
    function clearChoice() {
      if (!submittedChoice.current) return;
      submittedChoice.current = null;
      setPendingChoice(null);
    }
    function applySnapshot(state: AbyssState | null, battleUpdate = false) {
      if (battleUpdate && state && latestState && state.frame < latestState.frame) return;
      const now = performance.now();
      // Same-frame choice updates can change the UI. The countdown still advances frame numbers.
      if (!battleUpdate || !state || !latestState || state.frame > latestState.frame) {
        lastSnapshotAt.current = now;
        setStalled(false);
        setDelayed(false);
      }
      const player = state?.players.find(diver => diver.id === user?.id);
      const submission = submittedChoice.current;
      if (submission && (!state?.choice_seconds || state.level !== submission.level
        || !player?.choices.length || now - submission.sentAt >= upgradeRetryMs)) clearChoice();

      const attack = state?.warden_attack;
      const threatened = !!(attack && !attack.impact && player?.hp && attack.zones.some(([x, y, radius]) =>
        Math.hypot(x - player.position[0], y - player.position[1]) <= radius));
      const newThreat = threatened && attack?.id !== threateningAttack;
      threateningAttack = threatened ? attack?.id : undefined;
      if (state?.choice_seconds || state?.result || newThreat) {
        setInventory(false);
        setAudioVisible(false);
      }
      latestState = state;
      updateSpectator();
      setGame(state);
    }
    ws.onAny(msg => {
      if (msg.type === 'sync' || msg.type === 'lobby') {
        const room = msg.room as { players: RoomPlayer[] } | undefined;
        if (room) updateOfflinePeers(new Set(room.players.filter(player => player.connected === false).map(player => player.id)));
      }
      if (msg.type === 'sync') {
        clearChoice();
        const state = (msg.game ?? msg.abyss_result) as AbyssState | undefined;
        applySnapshot(state ?? null);
        setWaiting(!state);
        setConnected(true);
      } else if (msg.type === 'abyss_state' || msg.type === 'game_start' || msg.type === 'game_over') {
        if (msg.game) {
          setWaiting(false);
          applySnapshot(msg.game as AbyssState, msg.type === 'abyss_state');
        }
      } else if ((msg.type === 'player_disconnected' || msg.type === 'player_reconnected')
        && typeof msg.user_id === 'number') {
        const id = msg.user_id;
        const peers = new Set(offline);
        if (msg.type === 'player_disconnected') peers.add(id);
        else peers.delete(id);
        updateOfflinePeers(peers);
      } else if (msg.type === 'room_closed') {
        clearChoice();
        setWaiting(true);
        setGame(null);
      }
    });
    ws.connect(
      () => { lastSnapshotAt.current = performance.now(); },
      () => { clearChoice(); setConnected(false); setStalled(false); },
    );
    return () => {
      socket.current = null;
      ws.close();
    };
  }, [code, token, user?.id]);

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

  const watchSnapshots = focused && foreground && !waiting && !game?.result;
  useEffect(() => {
    if (!watchSnapshots) return;
    const startedAt = performance.now();
    const timer = setInterval(() => {
      const ws = socket.current;
      if (!ws?.isConnected) return;
      const age = performance.now() - Math.max(startedAt, lastSnapshotAt.current);
      if (age >= 3500) {
        setConnected(false);
        setStalled(false);
        ws.reconnect();
      } else if (age >= 1250) {
        setStalled(true);
      } else if (age >= 450) {
        setDelayed(true);
      }
    }, 125);
    return () => clearInterval(timer);
  }, [watchSnapshots]);

  const me = game?.players.find(p => p.id === user?.id);
  const liveConnection = connected && !stalled && focused && foreground && !waiting;
  const inPlay = liveConnection && !!game && !game.result;
  const active = inPlay && !!me?.hp && !game?.choice_seconds && !confirm && !inventory && !audioVisible;

  useEffect(() => {
    if (Platform.OS === 'web') return;
    let mounted = true;
    void AsyncStorage.getItem(vibrationKey).catch(() => null).then(value => {
      if (mounted) setVibration(value !== 'off');
    });
    return () => { mounted = false; };
  }, []);

  function toggleVibration() {
    if (Platform.OS === 'web' || vibration === null) return;
    const next = !vibration;
    setVibration(next);
    if (!next) Vibration.cancel();
    void AsyncStorage.setItem(vibrationKey, next ? 'on' : 'off').catch(() => {});
  }

  useEffect(() => {
    if (Platform.OS === 'web' || !liveConnection) return;
    return () => Vibration.cancel();
  }, [liveConnection]);

  useEffect(() => {
    const previous = previousGame.current;
    previousGame.current = liveConnection ? game : null;
    if (!vibration || !liveConnection || user?.id === undefined || !previous || !game) return;
    const now = performance.now();
    const duration = abyssVibrationDuration(previous, game, user.id, now - lastVibration.current, Platform.OS === 'android');
    if (!duration) return;
    Vibration.vibrate(duration);
    lastVibration.current = now;
  }, [game, liveConnection, user?.id, vibration]);

  const cancelPendingInput = useCallback(() => {
    clearTimeout(pendingInput.current);
    pendingInput.current = undefined;
  }, []);

  const sendInput = useCallback((preserveTap = false) => {
    cancelPendingInput();
    lastInputSend.current = performance.now();
    socket.current?.send({ type: 'abyss_move', dx: input.current[0], dy: input.current[1], ...(preserveTap ? { tap: true } : {}) });
  }, [cancelPendingInput]);

  useEffect(() => cancelPendingInput, [cancelPendingInput]);

  const updateInput = useCallback((send = true, preserveTap = false) => {
    let left = false, right = false, up = false, down = false;
    for (const key of held.current) {
      const [dx, dy] = keys[key];
      left ||= dx < 0;
      right ||= dx > 0;
      up ||= dy < 0;
      down ||= dy > 0;
    }
    const x = drag.current[0] + Number(right) - Number(left);
    const y = drag.current[1] + Number(down) - Number(up);
    const length = Math.hypot(x, y);
    input.current = length > 1e-6 ? [x / length, y / length] : [0, 0];
    if (send) sendInput(preserveTap);
  }, [sendInput]);

  const resetDrag = useCallback(() => {
    dragOrigin.current = null;
    drag.current = [0, 0];
    stick.opacity.setValue(0);
  }, [stick]);

  const clearInput = useCallback(() => {
    const wasMoving = input.current.some(Boolean) || pendingInput.current !== undefined;
    held.current.clear();
    resetDrag();
    updateInput(wasMoving);
  }, [resetDrag, updateInput]);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', state => {
      if (state !== 'active') clearInput();
      setForeground(state === 'active');
    });
    return () => subscription.remove();
  }, [clearInput]);

  const showAudio = useCallback((visible: boolean) => {
    if (visible) clearInput();
    setAudioVisible(visible);
  }, [clearInput]);

  function showInventory() {
    clearInput();
    setInventory(true);
  }

  useEffect(() => {
    if (!active) {
      clearInput();
      return;
    }
    const heartbeat = setInterval(() => {
      if (input.current.some(Boolean) || drag.current.some(Boolean) || held.current.size) updateInput();
    }, 110);
    return () => clearInterval(heartbeat);
  }, [active, clearInput, updateInput]);

  const livingPeers = me?.out ? game?.players.filter(player => player.hp > 0) ?? [] : [];
  const onlineWatchable = livingPeers.filter(player => !offlinePeers.has(player.id));
  const watchable = onlineWatchable.length ? onlineWatchable : livingPeers;
  const watchIndex = Math.max(0, watchable.findIndex(player => player.id === watchedId));
  const watchedDiver = watchable[watchIndex];
  const canSwitchSpectator = watchable.length > 1;
  const spectatorKeys = canSwitchSpectator && inPlay && !game?.choice_seconds
    && !inventory && !audioVisible && !confirm;
  const upgradeKeys = inPlay && !!game?.choice_seconds && !!me?.choices.length
    && !inventory && !audioVisible && !confirm;
  function changeWatchedDiver(step: number) {
    setWatchedId(current => {
      const index = Math.max(0, watchable.findIndex(player => player.id === current));
      return watchable[(index + step + watchable.length) % watchable.length]?.id;
    });
  }

  const choose = useCallback((choice: AbyssChoice) => {
    const ws = socket.current;
    if (!inPlay || !game?.choice_seconds || !me?.choices.includes(choice) || !ws?.isConnected
      || submittedChoice.current) return;
    submittedChoice.current = { level: game.level, sentAt: performance.now() };
    setPendingChoice(choice);
    ws.send({ type: 'abyss_pick', level: game.level, choice });
  }, [game, inPlay, me]);

  useWebHeldKeys(
    (key, repeated) => {
      if (upgradeKeys && /^[1-4]$/.test(key)) {
        const item = me?.choices[Number(key) - 1];
        if (!item) return false;
        if (!repeated) choose(item);
        return true;
      }
      if (spectatorKeys) {
        const previous = key === 'arrowleft' || key === 'a';
        const next = key === 'arrowright' || key === 'd';
        if (!previous && !next) return false;
        if (!repeated) changeWatchedDiver(previous ? -1 : 1);
        return true;
      }
      if (!active || !Object.hasOwn(keys, key)) return false;
      if (!repeated && !held.current.has(key)) {
        held.current.add(key);
        updateInput();
      }
      return true;
    },
    key => {
      if (held.current.delete(key)) updateInput(true, true);
    },
    clearInput,
    active || spectatorKeys || upgradeKeys,
  );

  function stopDrag(preserveTap = false) {
    if (!dragOrigin.current) return;
    const moved = drag.current.some(Boolean);
    resetDrag();
    updateInput(true, preserveTap && moved);
  }

  function startDrag({ nativeEvent }: GestureResponderEvent) {
    const touch = nativeEvent.changedTouches[0] ?? nativeEvent;
    drag.current = [0, 0];
    dragOrigin.current = { id: touch.identifier, x: touch.pageX, y: touch.pageY };
    stick.anchor.setValue({ x: touch.locationX, y: touch.locationY });
    stick.thumb.setValue({ x: 0, y: 0 });
    stick.opacity.setValue(1);
    updateInput();
  }

  function followDrag({ nativeEvent }: GestureResponderEvent) {
    const origin = dragOrigin.current;
    if (!active || !origin) return;
    const touch = nativeEvent.changedTouches.find(item => item.identifier === origin.id);
    if (!touch) return;

    const dx = touch.pageX - origin.x;
    const dy = touch.pageY - origin.y;
    const wasMoving = drag.current.some(Boolean);
    const distance = Math.hypot(dx, dy);
    const moving = distance > 4;
    if (!wasMoving && !moving) return;
    // Thumb displacement is visual. Outside the dead zone, movement uses a unit direction.
    drag.current = moving ? [dx / distance, dy / distance] : [0, 0];
    const thumbOffset = Math.min(1, Math.max(0, (distance - 4) / 38)) * 28;
    stick.thumb.setValue({ x: drag.current[0] * thumbOffset, y: drag.current[1] * thumbOffset });

    const wait = dragSendInterval - (performance.now() - lastInputSend.current);
    const send = wasMoving !== moving || wait <= 0;
    updateInput(send);
    // Send the final direction even if the finger stays still after a quick turn.
    if (!send && pendingInput.current === undefined) {
      pendingInput.current = setTimeout(sendInput, wait);
    }
  }

  function endTouch({ nativeEvent }: GestureResponderEvent) {
    // A final release or cancellation has its own responder callback.
    if (nativeEvent.touches.length && nativeEvent.changedTouches.some(touch => touch.identifier === dragOrigin.current?.id)) {
      stopDrag();
    }
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

  const networkStatus = !connected ? '正在重连…' : stalled ? '战场同步中断，正在恢复…'
    : delayed && watchSnapshots ? '网络延迟，等待战场更新…' : null;
  const finished = !!game?.result || waiting;
  const choosingTeammates = game?.choice_seconds ? game.players.filter(player =>
    player.id !== user?.id && player.choices.length > 0) : [];
  const choosingNames = choosingTeammates.map(player =>
    offlinePeers.has(player.id) ? `${player.name}（离线）` : player.name).join('、');
  let nearestRescue: typeof me;
  let rescueDistance = abyssRescue.radius * 2;
  if (me?.hp && game) {
    for (const player of game.players) {
      if (player.out || player.hp > 0) continue;
      const distance = Math.hypot(player.position[0] - me.position[0], player.position[1] - me.position[1]);
      if (distance < rescueDistance) {
        nearestRescue = player;
        rescueDistance = distance;
      }
    }
  }
  const insideRescue = !!nearestRescue && rescueDistance < abyssRescue.radius;
  const rescueTicks = me?.hp ? insideRescue ? nearestRescue?.rescue ?? 0 : 0 : me?.rescue ?? 0;
  const rescuePercent = Math.floor(rescueTicks * 10 / abyssRescue.ticks) * 10;
  const rescuer = me?.rescuer === undefined ? undefined : game?.players.find(player => player.id === me.rescuer);
  const rescueNotice = me?.hp
    ? insideRescue ? `救援 ${nearestRescue?.name} · ${rescuePercent}% · 剩 ${nearestRescue?.down ?? 0}s`
      : nearestRescue ? `靠近 ${nearestRescue.name} · 进入救援圈` : null
    : me?.out ? (watchedDiver ? `观战中 · ${watchedDiver.name}${offlinePeers.has(watchedDiver.id) ? '（离线）' : ''}` : '观战中') : me && (rescueTicks
      ? `${rescuer?.name ?? '队友'}正在救援 · ${rescuePercent}% · 剩 ${me.down}s`
      : `待救援 ${me.down}s · 等待队友靠近`);
  const showArenaNotice = !!game && !finished && !game.choice_seconds && !!(networkStatus || rescueNotice);
  const minimap = game && !finished && !game.choice_seconds
    ? abyssMinimapLayout(size.width, size.height, showArenaNotice ? noticeHeight : 0) : null;
  const rescueSeconds = me?.hp ? nearestRescue?.down : me && !me.out ? me.down : undefined;
  const rescueUrgent = rescueSeconds !== undefined && rescueSeconds <= 3;
  const growthBoost = game?.growth_boost ?? 1;
  const lowPressure = !!me && abyssLowPressure(me.hp, me.max_hp);
  const wardenWarning = game?.warden_attack && !game.warden_attack.impact ? game.warden_attack.kind : null;
  const boss = game?.enemies.find(e => e[1] === 'warden');
  const bossDistance = game?.map_size === 'endless' && boss && me?.hp
    ? Math.round(Math.hypot(boss[2] - me.position[0], boss[3] - me.position[1]) / 10) * 10 : null;
  const recentChests = game?.chest_events.filter(event => game.frame - event.frame < 60) ?? [];
  const ownChestRewards = recentChests.filter(event => event.player === user?.id);
  const chestRewards = ownChestRewards.length ? ownChestRewards : recentChests.slice(-1);
  const showChestRewards = chestRewards.length > 0;
  const weaponEntries = me ? Object.entries(me.weapons) as [AbyssWeapon, number][] : [];
  const passiveEntries = me ? Object.entries(me.passives) as [AbyssPassive, number][] : [];
  const readyEvolutions = me ? weaponEntries.flatMap(([weapon, rank]) => {
    const evolution = me.evolution_requirements?.[weapon];
    return evolution && rank >= evolution.rank && me.passives[evolution.passive]
      ? [abyssWeapons[evolution.target].title] : [];
  }) : [];
  return <View style={[styles.page, { paddingBottom: insets.bottom }]}>
    <View style={styles.hud}>
      <View style={styles.readout}>
        <Text accessibilityLiveRegion="polite" style={styles.label}>{lowPressure ? '低压警告' : '潜水服'}</Text>
        <Text testID="abyss-hp" style={[styles.value, lowPressure && styles.warning]}>
          {me ? `${Math.ceil(me.hp)} / ${Math.round(me.max_hp)}` : '—'}
        </Text>
        <View style={styles.bar}>
          <View style={[styles.fill, lowPressure && styles.warningFill, { width: `${me ? 100 * me.hp / me.max_hp : 0}%` }]} />
        </View>
      </View>
      <View style={styles.readout}>
        <Text style={styles.label}>沉没回收站</Text>
        <Text testID="abyss-clock" style={styles.value}>{abyssClock(game ? game.duration - game.seconds : 720)}</Text>
      </View>
      <View style={styles.readout}>
        <Text testID="abyss-level" numberOfLines={1} style={styles.label}
          accessibilityLabel={growthBoost > 1 ? `等级 ${game?.level}，团队经验 ${growthBoost} 倍` : undefined}>
          等级 {game?.level ?? 1}{growthBoost > 1 ? <Text style={styles.growthBoost}> · ×{growthBoost}</Text> : ' · XP'}
        </Text>
        <Text numberOfLines={1} style={styles.value}>{game?.xp ?? 0}/{game?.next_xp ?? 5}</Text>
        <View style={styles.bar}>
          <View style={[styles.fill, { width: `${game ? Math.min(100, 100 * game.xp / game.next_xp) : 0}%` }]} />
        </View>
      </View>
    </View>
    {!!game && game.players.length > 1 && <View testID="abyss-team" style={styles.team}>
      {game.players.map(p => {
        const offline = offlinePeers.has(p.id);
        const tight = game.players.length === 4 && size.width > 0 && size.width < 380;
        const teammateLowPressure = !offline && !p.out && abyssLowPressure(p.hp, p.max_hp);
        const health = Math.ceil(p.hp);
        const status = p.out ? '出局' : !p.hp ? `${offline ? '离线 · ' : ''}待救 ${p.down}s`
          : offline ? '离线' : `${teammateLowPressure ? '低压 · ' : ''}${health} HP`;
        const compactStatus = tight && offline && !p.hp && !p.out ? '离线待救'
          : tight && teammateLowPressure ? `低压 ${health}` : status;
        return <View key={p.id} style={styles.teammate}>
          <Text style={styles.label} numberOfLines={1} accessibilityLabel={p.name}>
            {p.id === user?.id ? '你' : p.name}
          </Text>
          <Text testID={`abyss-team-status-${p.id}`} numberOfLines={1} accessibilityLabel={status}
            style={[styles.text, tight && styles.compactStatus, teammateLowPressure && styles.warning]}>
            {compactStatus}
          </Text>
        </View>;
      })}
    </View>}
    {boss && <View style={styles.boss}>
      <View style={styles.bossReadout}>
        <Text testID="abyss-warden-health" style={[styles.label, game?.warden_enraged && styles.warning]}>
          断缆守卫{game?.warden_enraged ? ' · 狂暴' : ''} · {boss[5]} / {boss[6]}
        </Text>
        {bossDistance !== null && <Text testID="abyss-warden-distance" style={styles.label}>
          距离 {bossDistance} px
        </Text>}
      </View>
      <View style={styles.bar}><View style={[styles.bossFill, game?.warden_enraged && styles.warningFill, { width: `${100 * boss[5] / boss[6]}%` }]} /></View>
    </View>}
    <View testID="abyss-arena" style={[styles.arena, Platform.OS === 'web' && active && ({ touchAction: 'none' } as object)]}
      onLayout={e => setSize(e.nativeEvent.layout)}
      pointerEvents={active ? 'box-only' : 'auto'}
      onStartShouldSetResponder={() => active}
      onResponderGrant={startDrag} onResponderMove={followDrag} onResponderEnd={endTouch}
      onResponderRelease={() => stopDrag(true)} onResponderTerminate={() => stopDrag()}>
      {game && <AbyssBoard game={game} uid={user?.id} watching={watchedDiver?.id} offlinePeers={offlinePeers} size={size} input={input} connected={liveConnection}
        bannerHeight={showChestRewards || wardenWarning ? bannerHeight : 0}
        noticeHeight={showArenaNotice ? noticeHeight : 0} minimap={minimap} />}
      {game && minimap && <AbyssMinimap game={game} uid={user?.id} watching={watchedDiver?.id}
        offlinePeers={offlinePeers} {...minimap} />}
      <Animated.View testID="abyss-stick" pointerEvents="none" accessible={false}
        style={[styles.stick, { opacity: stick.opacity, transform: stick.anchor.getTranslateTransform() }]}>
        <Animated.View testID="abyss-stick-thumb" style={[styles.stickThumb, { transform: stick.thumb.getTranslateTransform() }]} />
      </Animated.View>
      {(showChestRewards || wardenWarning) && <View testID="abyss-arena-notices" pointerEvents="none" style={styles.arenaNotices}
        onLayout={event => setBannerHeight(event.nativeEvent.layout.height)}>
        {wardenWarning && <View style={[styles.arenaBanner, styles.wardenWarning]}>
          <Text testID="abyss-warden-warning" accessibilityLiveRegion="polite" style={[styles.label, styles.warning]}>
            {wardenWarning === 'strike' ? '断缆落点 · 离开十字标记圈' : '压强脉冲 · 离开守卫周围'}
          </Text>
        </View>}
        {showChestRewards && <View testID="abyss-chest" style={[styles.arenaBanner, styles.chestBanner]}>
          {chestRewards.map((reward, index) => <Text key={`${reward.frame}-${index}`} style={styles.chestText}>
            {reward.text}
          </Text>)}
        </View>}
      </View>}
      {!game && !waiting && <View style={styles.center}><ActivityIndicator color={c.signal} /><Text style={styles.label}>正在同步潜区…</Text></View>}
      {showArenaNotice && <View testID="abyss-arena-status"
        pointerEvents={!networkStatus && canSwitchSpectator ? 'auto' : 'none'} style={styles.notice}
        onLayout={event => setNoticeHeight(event.nativeEvent.layout.height)}>
        {networkStatus
          ? <Text testID="abyss-network-status" accessibilityLiveRegion="polite" style={styles.text}>{networkStatus}</Text>
          : <>
            <Text testID="abyss-rescue-notice" style={[styles.text, rescueUrgent && styles.warning]}>{rescueNotice}</Text>
            {canSwitchSpectator && <Pressable testID="abyss-spectator-next" accessibilityRole="button"
              style={styles.spectatorSwitch} onPress={() => changeWatchedDiver(1)}>
              <Text style={styles.section}>切换队友</Text>
            </Pressable>}
          </>}
      </View>}
      {(finished || !!game?.choice_seconds) && <View style={styles.overlay}>
        <ScrollView testID="abyss-overlay-scroll" style={styles.overlayScroll} contentContainerStyle={styles.overlayContent} keyboardShouldPersistTaps="handled">
          <View style={styles.card}>
            {finished ? <>
              <Text style={styles.title}>{game?.result ? abyssResults[game.result] : '对局已结束'}</Text>
              {game && <AbyssResult game={game} uid={user?.id} />}
            </> : <>
              <Text style={styles.title}>
                {me?.choices.length ? `选择强化 · ${game?.choice_seconds}s`
                  : choosingNames ? `等待 ${choosingTeammates.length} 名队友 · ${game?.choice_seconds}s` : '即将恢复战斗'}
              </Text>
              {Platform.OS === 'web' && upgradeKeys && <Text style={styles.label}>按数字键选择强化</Text>}
              {!!networkStatus && <Text testID="abyss-network-status" accessibilityLiveRegion="polite" style={styles.warning}>{networkStatus}</Text>}
              {pendingChoice && me?.choices.length ? (
                <Text testID="abyss-pick-pending" accessibilityLiveRegion="polite" style={styles.label}>
                  已提交 {abyssItemInfo(pendingChoice).title}，等待确认…
                </Text>
              ) : null}
              {me?.choices.length ? me.choices.map((choice, index) => {
                const info = abyssItemInfo(choice);
                const rank = abyssLevelOf(me, choice);
                const changes = me.choice_changes?.[choice];
                const repair = choice === 'repair' || choice === 'rest' ? me.repair_health?.[choice] : undefined;
                const detail = changes?.length ? abyssChangeText(changes) : info.detail;
                const evolutions = abyssEvolutionPreview(me, choice);
                const newWeaponStats = choice in abyssWeapons && !rank
                  ? me.new_weapon_stats?.[choice as keyof typeof abyssWeapons] : undefined;
                const suffix = info.kind === 'utility' ? '' : rank ? ` Lv.${rank} → ${rank + 1}` : ' 新';
                return <Pressable key={choice} testID={`abyss-choice-${choice}`} style={[styles.choice, pendingChoice === choice && styles.selectedChoice]}
                  accessibilityRole="button" onPress={() => choose(choice)} disabled={!inPlay || pendingChoice !== null}>
                  <Image testID="abyss-item-art" accessible={false} source={abyssItemArt[choice]} resizeMode="contain" style={styles.choiceArt} />
                  <View style={styles.choiceBody}>
                    <View style={styles.choiceTitle}>
                      {Platform.OS === 'web' && <Text style={styles.choiceKey}>{index + 1}</Text>}
                      <Text testID="abyss-item-kind" style={[styles.choiceKind, info.kind === 'weapon' && styles.weaponKind]}>
                        {info.kind === 'weapon' ? '武器' : '道具'}
                      </Text>
                      <Text style={styles.choiceName}>{info.title}{suffix}</Text>
                    </View>
                    <Text style={styles.label}>{repair
                      ? repair[0] === repair[1] ? '耐久已满' : `耐久 ${repair[0]} → ${repair[1]}`
                      : detail}</Text>
                    {!!newWeaponStats?.length && <Text style={styles.label}>{abyssNewWeaponText(newWeaponStats)}</Text>}
                    {!!evolutions.length && <Text style={styles.evolutionReady}>
                      可用宝箱进化为{evolutions.join('、')}
                    </Text>}
                  </View>
                </Pressable>;
              }) : <>
                {!!choosingNames && <Text testID="abyss-choice-waiting" accessibilityLiveRegion="polite" style={styles.label}>
                  待选择：{choosingNames}
                </Text>}
                {!!choosingNames && <Text style={styles.label}>
                  {me?.hp ? '你已完成选择。' : ''}全员选完或倒计时结束后继续战斗。
                </Text>}
                {me && !me.hp && !me.out && <Text style={styles.label}>倒地中，等待队友救援</Text>}
              </>}
            </>}
          </View>
        </ScrollView>
        {finished && <View style={styles.resultAction}>
          <Pressable style={styles.primary} accessibilityRole="button" onPress={back}>
            <Text style={styles.primaryText}>返回房间</Text>
          </Pressable>
        </View>}
      </View>}
    </View>
    <View testID="abyss-footer" style={[styles.footer, size.width >= 520 && styles.footerWide]}>
      <Text testID="abyss-footer-status" style={[styles.label, styles.footerStatus]}>
        {me ? `武器 ${weaponEntries.length}/6 · 被动 ${passiveEntries.length}/6 · 打捞分 ${game?.salvage ?? 0}` : '等待同步'}
      </Text>
      <View style={styles.footerActions}>
        <Text testID="abyss-footer-hint" style={[styles.label, styles.footerHint]}
          accessibilityLabel={Platform.OS === 'web'
            ? canSwitchSpectator ? '左右方向键切换观战队友' : '拖动、WASD 或方向键移动'
            : '拖动移动'}>{Platform.OS === 'web'
          ? canSwitchSpectator ? '← / → 切换队友' : '拖动·WASD·↑↓←→'
          : '拖动移动'}</Text>
        <AbyssAudio game={game} uid={user?.id} live={liveConnection} inPlay={inPlay}
          visible={audioVisible} onVisibilityChange={showAudio}
          vibration={vibration} onToggleVibration={toggleVibration} />
        <Pressable testID="abyss-inventory" style={[styles.plain, !!game?.choice_seconds && styles.disabled]}
          accessibilityRole="button" accessibilityLabel={readyEvolutions.length
            ? `装备，可用宝箱进化为${readyEvolutions.join('、')}` : '装备'}
          disabled={!!game?.choice_seconds} onPress={showInventory}>
          <Text style={styles.text}>装备{!!readyEvolutions.length && <Text testID="abyss-evolution-ready"
            style={styles.evolutionReady}> ↑</Text>}</Text>
        </Pressable>
        <Pressable testID="abyss-leave" style={styles.plain} accessibilityRole="button" onPress={() => setConfirm(true)}><Text style={styles.text}>离开</Text></Pressable>
      </View>
    </View>
    <AppModal visible={inventory} onClose={() => setInventory(false)} cardStyle={styles.sheet} testID="abyss-inventory-modal">
      <Text style={styles.title}>装备与属性</Text>
      {!me ? <Text style={styles.label}>等待同步</Text> : <>
        <Text style={styles.section}>武器 {weaponEntries.length}/6</Text>
        {weaponEntries.length ? weaponEntries.map(([weapon, level]) => {
          const info = abyssWeapons[weapon];
          const evolved = 'evolved' in info;
          const evolution = me.evolution_requirements?.[weapon];
          const missing = evolution && [
            level < evolution.rank && `武器升到 Lv.${evolution.rank}`,
            !me.passives[evolution.passive] && abyssPassives[evolution.passive].title,
          ].filter(Boolean).join(' + ');
          return <View key={weapon} style={styles.gearRow}>
            <View style={styles.inventoryRow}>
              <View style={styles.gearName}>
                <Image accessible={false} source={abyssItemArt[weapon]} resizeMode="contain" style={styles.gearArt} />
                <Text style={styles.choiceName}>{info.title}</Text>
              </View>
              <Text style={evolved ? styles.evolutionReady : styles.label}>{evolved ? '已进化' : `Lv.${level}`}</Text>
            </View>
            <Text style={[styles.itemDetail, styles.label]}>{info.detail}</Text>
            {evolution && <Text style={[styles.itemDetail, missing ? styles.label : styles.evolutionReady]}>
              {abyssWeapons[evolution.target].title} · {missing ? `还需 ${missing}` : '开启宝箱即可进化'}
            </Text>}
          </View>;
        }) : <Text style={styles.label}>暂无武器</Text>}
        <Text style={styles.section}>被动 {passiveEntries.length}/6</Text>
        {passiveEntries.length ? passiveEntries.map(([passive, level]) =>
          <View key={passive} style={styles.gearRow}>
            <View style={styles.inventoryRow}>
              <View style={styles.gearName}>
                <Image accessible={false} source={abyssItemArt[passive]} resizeMode="contain" style={styles.gearArt} />
                <Text style={styles.choiceName}>{abyssPassives[passive].title}</Text>
              </View>
              <Text style={styles.label}>Lv.{level}</Text>
            </View>
            <Text style={[styles.itemDetail, styles.label]}>{abyssPassives[passive].detail}</Text>
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
  readout: { flex: 1, padding: 7, gap: 3, borderWidth: 1, borderColor: c.edge, borderRadius: 8, backgroundColor: c.panel },
  label: { color: c.muted, fontSize: 13 },
  choiceTitle: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  choiceBody: { flex: 1, minWidth: 0, gap: 2 },
  choiceArt: { width: 48, height: 48, borderRadius: 6, backgroundColor: c.panel },
  choiceKind: { color: c.brass, fontSize: 12, fontWeight: '700', borderWidth: 1,
    borderColor: c.edge, borderRadius: 4, paddingHorizontal: 4 },
  weaponKind: { color: c.signal },
  choiceName: { flexShrink: 1, color: c.text, fontSize: 15, fontWeight: '600' },
  choiceKey: { color: c.signal, fontSize: 13, fontWeight: '700', minWidth: 20, textAlign: 'center',
    borderWidth: 1, borderColor: c.edge, borderRadius: 4 },
  value: { color: c.signal, fontSize: 15, fontWeight: '700', fontVariant: ['tabular-nums'] },
  stick: { position: 'absolute', top: -42, left: -42, width: 84, height: 84, borderRadius: 42,
    borderWidth: 1.5, borderColor: c.edge, backgroundColor: c.shadow + '66',
    alignItems: 'center', justifyContent: 'center' },
  stickThumb: { width: 24, height: 24, borderRadius: 12,
    borderColor: c.signal, borderWidth: 2, backgroundColor: c.deep + 'B8' },
  arena: { flex: 1, overflow: 'hidden', borderTopWidth: 1, borderBottomWidth: 1, borderColor: c.edge },
  team: { flexDirection: 'row', gap: 6, paddingHorizontal: 8, paddingBottom: 8 },
  teammate: { flex: 1, minWidth: 0, paddingHorizontal: 6, paddingVertical: 4, borderRadius: 5, backgroundColor: c.panel },
  compactStatus: { fontSize: 12 },
  boss: { paddingHorizontal: 12, paddingBottom: 7, gap: 4 },
  bossReadout: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', columnGap: 6 },
  bar: { height: 5, backgroundColor: c.deep, overflow: 'hidden', borderRadius: 5 },
  fill: { height: '100%', backgroundColor: c.signal },
  warning: { color: c.charge },
  growthBoost: { color: c.beacon, fontWeight: '700' },
  wardenWarning: { borderColor: c.charge },
  warningFill: { backgroundColor: c.charge },
  bossFill: { height: 5, backgroundColor: c.warden },
  footer: { paddingHorizontal: 12, paddingTop: 6 },
  footerWide: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', columnGap: 12, paddingTop: 0 },
  footerStatus: { paddingHorizontal: 2 },
  footerActions: { flexGrow: 1, flexDirection: 'row', alignItems: 'center', columnGap: 4 },
  footerHint: { flex: 1, paddingHorizontal: 2 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  notice: { position: 'absolute', bottom: 14, alignSelf: 'center', maxWidth: '92%', borderRadius: 8, backgroundColor: c.panel, padding: 8 },
  spectatorSwitch: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  arenaNotices: { position: 'absolute', top: 8, alignSelf: 'center', alignItems: 'center', maxWidth: '92%', gap: 6 },
  arenaBanner: { borderRadius: 8, backgroundColor: c.panel, borderWidth: 1, paddingHorizontal: 10, paddingVertical: 6 },
  chestBanner: { borderColor: c.chest },
  chestText: { color: c.chest, fontSize: 13, fontWeight: '700' },
  overlay: { ...StyleSheet.absoluteFill, backgroundColor: c.shadow + 'C9' },
  resultAction: { flexShrink: 0, alignSelf: 'center', width: '100%', maxWidth: 428, paddingHorizontal: 14, paddingBottom: 8 },
  overlayScroll: { flex: 1, minHeight: 0 },
  overlayContent: { flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: 14 },
  card: { flexShrink: 0, width: '100%', maxWidth: 400, backgroundColor: c.panel, borderColor: c.edge, borderWidth: 1, borderRadius: 14, padding: 12, gap: 8 },
  sheet: { backgroundColor: c.panel, borderColor: c.edge, borderWidth: 1 },
  title: { color: c.text, fontSize: 21, fontWeight: '800' },
  text: { color: c.text, fontSize: 15, fontWeight: '600' },
  section: { color: c.signal, fontSize: 14, fontWeight: '700', marginTop: 4 },
  choice: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: c.deep, padding: 7, borderRadius: 7, borderWidth: 1, borderColor: c.edge },
  gearRow: { backgroundColor: c.deep, borderRadius: 6 },
  gearName: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 },
  gearArt: { width: 32, height: 32 },
  evolutionReady: { color: c.signal, fontSize: 13, fontWeight: '700' },
  itemDetail: { paddingHorizontal: 8, paddingBottom: 6 },
  inventoryRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    backgroundColor: c.deep, borderRadius: 6, paddingHorizontal: 8, paddingVertical: 5 },
  stats: { flexDirection: 'row', flexWrap: 'wrap', gap: 4 },
  primary: { minHeight: 44, justifyContent: 'center', alignItems: 'center', backgroundColor: c.signal, borderRadius: 8 },
  primaryText: { color: c.deep, fontWeight: '800' },
  selectedChoice: { borderColor: c.signal },
  disabled: { opacity: .5 },
  plain: { minHeight: 44, minWidth: 56, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8 },
});
