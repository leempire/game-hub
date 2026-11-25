import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, AppState, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { GestureResponderEvent } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import FishSea from '../FishSea';
import FishRoundFeedback from '../FishRoundFeedback';
import { fishEventName, fishEventText, still, swimDirection, type FishDirection, type FishState } from '../fish';
import { useFishAudio } from '../fishAudio';
import { leaveRoom } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave, useWebHeldKeys } from '../backGuard';
import { RoomSocket } from '../socket';
import { fishColors as c } from '../theme';
import { AppModal } from '../ui';

const keys: Record<string, FishDirection> = {
  w: [0, -1], a: [-1, 0], s: [0, 1], d: [1, 0],
  arrowup: [0, -1], arrowleft: [-1, 0], arrowdown: [0, 1], arrowright: [1, 0],
};
const sendInterval = 40;
const isSwimKey = (key: string) => Object.hasOwn(keys, key);

export default function FishScreen({ code, onBack, onExit }: {
  code: string; onBack: () => void; onExit: () => void;
}) {
  const { token, user } = useAuth();
  const nav = useNavigation();
  const focused = useIsFocused();
  const insets = useSafeAreaInsets();
  const [game, setGame] = useState<FishState | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [connected, setConnected] = useState(false);
  const [closed, setClosed] = useState(false);
  const [foreground, setForeground] = useState(AppState.currentState !== 'background');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [stick, setStick] = useState<{ x: number; y: number } | null>(null);
  const [knob] = useState(() => new Animated.ValueXY());
  const [watchId, setWatchId] = useState<number>();
  const origin = useRef<{ x: number; y: number;  } | null>(null);
  const held = useRef(new Set<string>());
  const socket = useRef<RoomSocket | null>(null);
  const input = useRef<FishDirection>(still);
  const lastSent = useRef(-Infinity);
  const sendTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const canLeave = useRef(false);
  const lastLives = useRef<number | undefined>(undefined);
  const lastSnapshot = useRef(0);
  const me = game?.players.find(p => p.id === user?.id);
  const living = game?.players.filter(p => p.lives > 0) ?? [];
  const watched = living.find(p => p.id === watchId) ?? living[0];
  const live = focused && foreground && connected && !game?.finished;
  const canSwim = live && !!me?.lives && !confirm;
  const audio = useFishAudio(game, user?.id, focused && foreground);

  const send = useCallback((next: FishDirection, immediate = false) => {
    input.current = next;
    function flush() {
      sendTimer.current = null;
      lastSent.current = performance.now();
      const [dx, dy] = input.current;
      socket.current?.send({ type: 'fish_move', dx, dy });
    }
    const wait = sendInterval - (performance.now() - lastSent.current);
    if (immediate || wait <= 0) {
      if (sendTimer.current) clearTimeout(sendTimer.current);
      flush();
    } else {
      sendTimer.current ??= setTimeout(flush, wait);
    }
  }, []);

  const stop = useCallback(() => {
    held.current.clear();
    origin.current = null;
    setStick(null);
    send(still, true);
  }, [send]);

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    let latestFrame = -1;
    let finished = false;
    ws.onAny(msg => {
      if (msg.type === 'room_closed') {
        setClosed(true);
        setConnected(false);
      }
      if (!['sync', 'game_start', 'fish_state', 'game_over'].includes(msg.type)) return;
      const state = (msg.game ?? msg.fish_result) as FishState | undefined;
      if (state?.game === 'fish') {
        if (msg.type === 'fish_state' && (finished || state.frame <= latestFrame)) return;
        latestFrame = state.frame;
        finished = state.finished;
        lastSnapshot.current = performance.now();
        setGame(state);
        setConnected(true);
      } else if (msg.type === 'sync') {
        setClosed(true);
      }
    });
    ws.connect(undefined, reason => {
      setConnected(false);
      if ([4001, 4003, 4004].includes(reason)) setClosed(true);
    });
    return () => {
      if (sendTimer.current) clearTimeout(sendTimer.current);
      sendTimer.current = null;
      socket.current = null;
      ws.close();
    };
  }, [code, token]);

  const watching = live && !!game;
  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(() => {
      if (performance.now() - lastSnapshot.current < 1800) return;
      setConnected(false);
      socket.current?.reconnect();
    }, 550);
    return () => clearInterval(timer);
  }, [watching]);

  useEffect(() => {
    const sub = AppState.addEventListener('change', state => setForeground(state === 'active'));
    return () => sub.remove();
  }, []);

  const lives = me?.lives;
  useEffect(() => {
    if (!canSwim || (lastLives.current != null && lives != null && lives < lastLives.current)) stop();
    lastLives.current = lives;
  }, [canSwim, stop, lives]);

  useEffect(() => {
    if (!canSwim) return;
    const timer = setInterval(() => {
      const [dx, dy] = input.current;
      if (dx || dy) send(input.current);
    }, 200);
    return () => clearInterval(timer);
  }, [canSwim, send]);

  useEffect(() => {
    const request = () => setConfirm(true);
    const unsubscribe = nav.addListener('beforeRemove', e => {
      if (Platform.OS === 'web' || canLeave.current) return;
      e.preventDefault();
      request();
    });
    const web = onConfirmLeave(request);
    return () => { unsubscribe(); web(); };
  }, [nav]);

  function updateKeys() {
    let x = 0;
    let y = 0;
    for (const key of held.current) {
      x += keys[key][0];
      y += keys[key][1];
    }
    send(swimDirection(x, y), x === 0 && y === 0);
  }
  useWebHeldKeys((key, repeated) => {
    if (!isSwimKey(key)) return false;
    if (!repeated && !origin.current) { held.current.add(key); updateKeys(); }
    return true;
  }, key => {
    if (held.current.delete(key)) updateKeys();
  }, stop, canSwim);

  function begin(e: GestureResponderEvent) {
    const { pageX, pageY, locationX, locationY } = e.nativeEvent;
    held.current.clear();
    send(still, true);
    origin.current = { x: pageX, y: pageY };
    knob.setValue({ x: 0, y: 0 });
    setStick({ x: locationX, y: locationY });
  }
  function drag(e: GestureResponderEvent) {
    const point = origin.current;
    if (!point || !canSwim) return;
    const x = e.nativeEvent.pageX - point.x;
    const y = e.nativeEvent.pageY - point.y;
    const distance = Math.hypot(x, y);
    const strength = Math.max(0, Math.min(1, (distance - 5) / 36));
    const direction: FishDirection = distance ? [x * strength / distance, y * strength / distance] : still;
    send(direction);
    knob.setValue({ x: direction[0] * 24, y: direction[1] * 24 });
  }

  function returnToRoom() {
    canLeave.current = true;
    stop();
    onBack();
  }
  function exitToHub() {
    canLeave.current = true;
    onExit();
  }
  async function exit() {
    if (!token || busy) return;
    setBusy(true);
    try {
      await leaveRoom(token, code);
      exitToHub();
    } catch (cause) {
      setError((cause as Error).message);
      setBusy(false);
    }
  }

  const remaining = Math.ceil(game?.remaining ?? 600);
  const tier = me?.tier ?? 1;
  const growthFrom = me?.growth_from ?? 0;
  const growthTo = me?.growth_to;
  const pointsIntoTier = (me?.score ?? 0) - growthFrom;
  const growth = growthTo == null ? (me?.tier ? 1 : 0) : pointsIntoTier / (growthTo - growthFrom);
  const showHud = !!game && !game.finished && !closed;
  const event = game?.event;
  const upcoming = game?.upcoming;
  const dangerTime = connected && !!game && remaining <= 30 && !game.finished;
  const clock = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
  const status = !connected ? '正在连接海域…'
    : !me?.lives ? `观战 · ${watched?.name ?? '等待结算'}`
      : [me.immune > 0 ? `无敌 ${Math.ceil(me.immune / 30)} 秒` : '', me.boost ? '海流加速' : '', me.shield ? '护盾就绪' : ''].filter(Boolean).join(' · ');

  return <View style={[styles.screen, { paddingBottom: insets.bottom }]}>
    <View style={styles.hud}>
      <View style={styles.stats}>
        <View accessible accessibilityLabel={`剩余 ${me?.lives ?? 3} 条命`} style={styles.hearts}>
          {[0, 1, 2].map(i => <Ionicons key={i} name={i < (me?.lives ?? 3) ? 'heart' : 'heart-outline'} size={17} color={c.coral} />)}
        </View>
        <Text numberOfLines={1} style={styles.score}>{me?.score ?? 0} 分</Text>
      </View>
      <Text testID="fish-clock" style={[styles.clock, dangerTime && { color: c.coral }]}>{clock}</Text>
      <Pressable accessibilityRole="button" accessibilityLabel={audio.enabled ? '关闭音乐和音效' : '开启音乐和音效'}
        onPress={audio.toggle} style={styles.iconButton}>
        <Ionicons name={audio.enabled ? 'volume-high-outline' : 'volume-mute-outline'} size={23} color={c.text} />
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="退出对局" onPress={() => setConfirm(true)} style={styles.iconButton}>
        <Ionicons name="exit-outline" size={23} color={c.text} />
      </Pressable>
    </View>

    {showHud && <View style={styles.growthBar}>
      <Text style={styles.growthLabel}>体型 {tier} 级</Text>
      <View accessibilityRole="progressbar" accessibilityLabel="成长进度"
        accessibilityValue={{ min: 0, max: 100, now: Math.round(growth * 100) }} style={styles.progressTrack}>
        <View style={[styles.progressFill, { width: `${growth * 100}%` }]} />
      </View>
      <Text style={styles.growthDetail}>{growthTo == null ? '最大体型' : `距升级 ${growthTo - (me?.score ?? 0)} 分`}</Text>
    </View>}
    <View testID="fish-sea" style={styles.sea}
      onLayout={e => setSize({ width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height })}
      onStartShouldSetResponder={() => canSwim} onResponderGrant={begin} onResponderMove={drag}
      onResponderRelease={stop} onResponderTerminate={stop}>
      <FishSea game={game} uid={user?.id} width={size.width} height={size.height} input={input} live={live} watchId={watched?.id} />
      {!game?.finished && !closed && <View style={styles.notices} pointerEvents="none">
        {!!status && <Text style={styles.notice}>{status}</Text>}
        {event ? <Text style={[styles.notice, styles.event]}>{fishEventText[event]} · {Math.ceil(game?.event_remaining ?? 0)} 秒</Text>
          : upcoming && <Text style={[styles.notice, styles.event]}>{fishEventName[upcoming[0]]} · {Math.ceil(upcoming[1] / 30)} 秒后</Text>}
      </View>}
      {showHud && <View style={styles.radar} pointerEvents="none">
        <Text style={styles.radarTitle}>海图</Text>
        <View style={styles.radarSea}>
          {living.map(p => <View key={p.id} style={[styles.marker, {
            left: `${p.x / game.width * 100}%`, top: `${p.y / game.height * 100}%`,
            backgroundColor: p.id === user?.id ? c.cyan : c.peer,
            borderColor: p.id === (me?.lives ? user?.id : watched?.id) ? c.text : c.panel,
          }]} />)}
        </View>
      </View>}
      <FishRoundFeedback player={me} round={game?.frame ?? -1} active={showHud && connected && focused} />
      {showHud && me?.lives === 0 && living.length > 1 && <View style={styles.watchControls}>
        <Pressable accessibilityRole="button" onPress={() => setWatchId(living[(living.indexOf(watched) + 1) % living.length].id)}
          style={styles.watchButton}><Text style={styles.growthLabel}>切换观战对象</Text></Pressable>
      </View>}
      {stick && canSwim && <View pointerEvents="none" style={[styles.stick, { left: stick.x - 32, top: stick.y - 32 }]}>
        <Animated.View style={[styles.knob, { transform: knob.getTranslateTransform() }]} />
      </View>}
      {(closed || game?.finished) && <View style={styles.overlay}>
        <View style={styles.result}>
          <Text style={styles.title}>{closed ? '海域已关闭' : '本轮结算'}</Text>
          {!!game?.finished && <ScrollView style={{ maxHeight: 210 }}>
            {game.ranking.map(row => <View key={row.id} style={styles.rank}>
              <Text style={[styles.rankNumber, row.rank === 1 && { color: c.amber }]}>{row.rank ?? '退出'}</Text>
              <Text style={styles.rankName} numberOfLines={1}>{row.name}{row.id === user?.id ? '（你）' : ''}</Text>
              <Text style={styles.score}>{row.score} 分</Text>
            </View>)}
          </ScrollView>}
          <Pressable style={styles.primary} onPress={closed ? exitToHub : returnToRoom}><Text style={styles.primaryText}>{closed ? '返回大厅' : '返回房间'}</Text></Pressable>
        </View>
      </View>}
    </View>
    <View style={styles.legend}>
      <Text style={[styles.legendText, { color: c.green }]}>青绿可吃</Text>
      <Text style={[styles.legendText, { color: c.coral }]}>红圈危险</Text>
      <Text style={styles.legendText}>{Platform.OS === 'web' ? '拖动 / WASD / 方向键' : '拖动水域游动'}</Text>
    </View>
    <AppModal visible={confirm} onClose={() => { if (!busy) setConfirm(false); }}>
      <Text style={styles.modalTitle}>{game?.finished || closed ? '离开对局' : '退出当前对局？'}</Text>
      {!game?.finished && !closed && <Text>退出后本局将不计名次。</Text>}
      {!!error && <Text accessibilityRole="alert">{error}</Text>}
      <Pressable style={styles.primary} disabled={busy} onPress={closed ? exitToHub : game?.finished ? returnToRoom : () => void exit()}>
        <Text style={styles.primaryText}>{busy ? '正在退出…' : '确认'}</Text>
      </Pressable>
      <Pressable style={styles.cancel} disabled={busy} onPress={() => setConfirm(false)}><Text>取消</Text></Pressable>
    </AppModal>
  </View>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: c.deep },
  hud: { minHeight: 58, paddingHorizontal: 12, flexDirection: 'row', gap: 8, alignItems: 'center', backgroundColor: c.panel },
  stats: { flex: 1, flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', columnGap: 9 },
  hearts: { flexDirection: 'row', gap: 3 },
  score: { color: c.cyan, fontSize: 15, fontWeight: '700' },
  clock: { color: c.text, fontSize: 23, fontWeight: '800', fontVariant: ['tabular-nums'] },
  iconButton: { width: 44, height: 44, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  sea: { flex: 1, overflow: 'hidden', ...(Platform.OS === 'web' ? { touchAction: 'none' } : {}) },
  growthBar: { paddingHorizontal: 15, minHeight: 35, flexDirection: 'row', gap: 10,
    alignItems: 'center', backgroundColor: c.panel, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: c.line },
  growthLabel: { color: c.text, fontSize: 13, fontWeight: '800' },
  growthDetail: { color: c.muted, minWidth: 70, textAlign: 'right', fontSize: 13, fontVariant: ['tabular-nums'] },
  progressTrack: { flex: 1, height: 5, borderRadius: 4, overflow: 'hidden', backgroundColor: c.line },
  progressFill: { height: '100%', backgroundColor: c.green },
  radar: { position: 'absolute', right: 12, bottom: 12, padding: 7, gap: 3,
    borderRadius: 10, backgroundColor: c.glass, borderColor: c.line, borderWidth: 1 },
  radarTitle: { color: c.muted, fontWeight: '700', fontSize: 12 },
  radarSea: { width: 82, height: 46, margin: 3, borderRadius: 4, borderColor: c.line, borderWidth: 1 },
  marker: { position: 'absolute', marginLeft: -4, marginTop: -4, width: 8, height: 8, borderRadius: 4, borderWidth: 1 },
  watchControls: { position: 'absolute', bottom: 14, left: 12 },
  watchButton: { minHeight: 44, paddingHorizontal: 12, justifyContent: 'center',
    borderWidth: 1, borderColor: c.line, borderRadius: 12, backgroundColor: c.glass },
  notices: { position: 'absolute', top: 16, alignSelf: 'center', alignItems: 'center', gap: 5, maxWidth: '90%' },
  notice: { overflow: 'hidden', backgroundColor: c.glass, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 7, color: c.text, fontWeight: '700', fontSize: 14 },
  event: { color: c.amber },
  stick: { position: 'absolute', borderWidth: 1, borderColor: c.muted, borderRadius: 32, width: 64, height: 64, alignItems: 'center', justifyContent: 'center', backgroundColor: c.glass, opacity: .65 },
  knob: { width: 24, height: 24, borderRadius: 12, backgroundColor: c.cyan },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, justifyContent: 'center', padding: 12, backgroundColor: c.panel },
  legendText: { fontSize: 13, color: c.muted, fontWeight: '600' },
  overlay: { ...StyleSheet.absoluteFill, backgroundColor: c.glass, alignItems: 'center', justifyContent: 'center', padding: 18 },
  result: { width: '100%', maxWidth: 410, backgroundColor: c.panel, borderWidth: 1, borderColor: c.line, borderRadius: 20, padding: 18, gap: 12 },
  title: { color: c.text, fontSize: 24, fontWeight: '800' },
  rank: { flexDirection: 'row', alignItems: 'center', gap: 9, borderBottomWidth: 1, borderColor: c.line, minHeight: 44 },
  rankNumber: { color: c.muted, width: 36, fontWeight: '800' },
  rankName: { flex: 1, color: c.text, fontSize: 16 },
  primary: { backgroundColor: c.cyan, padding: 12, borderRadius: 10, alignItems: 'center' },
  primaryText: { color: c.deep, fontSize: 16, fontWeight: '800' },
  modalTitle: { fontSize: 21, fontWeight: '800' },
  cancel: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
});
