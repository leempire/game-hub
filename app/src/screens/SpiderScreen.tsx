import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { View, Text, TouchableOpacity, ScrollView, Modal, StyleSheet, Platform, ActivityIndicator, useWindowDimensions } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import { StatusBar } from 'expo-status-bar';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAuth } from '../auth';
import { RoomSocket } from '../socket';
import { Room, leaveRoom } from '../api';
import { onConfirmLeave, useWebKeys, useModalBackGuard } from '../backGuard';
import GameShell, { GameResult } from '../GameShell';
import { shellPlayers } from '../gamePlayers';
import { colors, spacing, radius, spiderTable } from '../theme';
import SpiderBoard, { Spade } from '../SpiderBoard';
import { SpiderState, spiderProgressLabel } from '../spider';
import { requestSpiderFullscreen } from '../spiderFullscreen';

const PICK_MESSAGE = '点选明牌后，再点目标列';

export default function SpiderScreen({ code, onLeave }: { code: string; onLeave: () => void }) {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const [fullscreen, setFullscreen] = useState(false);
  const stopFullscreen = useRef<(() => void) | null>(null);
  const exitFullscreen = useCallback(() => {
    stopFullscreen.current?.(); stopFullscreen.current = null; setFullscreen(false);
  }, []);
  const socket = useRef<RoomSocket | null>(null);
  const [game, setGame] = useState<SpiderState | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const gameRef = useRef<SpiderState | null>(null);
  const pendingRevision = useRef<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState<{ source: number; index: number } | null>(null);
  const [message, setMessage] = useState(PICK_MESSAGE);
  const [confirm, setConfirm] = useState<'leave' | 'end' | 'undo' | 'endRound' | null>(null);
  const leaving = useRef(false);
  useModalBackGuard(fullscreen, exitFullscreen);
  useModalBackGuard(confirm !== null, () => setConfirm(null));
  useWebKeys(key => {
    if (key !== 'Escape') return false;
    exitFullscreen(); return true;
  }, fullscreen && confirm === null);

  useLayoutEffect(() => {
    navigation.setOptions({ headerShown: !fullscreen, orientation: fullscreen ? 'landscape' : 'portrait',
      navigationBarHidden: fullscreen, autoHideHomeIndicator: fullscreen });
  }, [navigation, fullscreen]);
  useEffect(() => () => stopFullscreen.current?.(), []);

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    const receive = (next: SpiderState, acknowledge = false) => {
      const previous = gameRef.current;
      if (previous && next.revision < previous.revision) return;
      if (acknowledge || (pendingRevision.current !== null && next.revision === pendingRevision.current + 1)) {
        pendingRevision.current = null; setPending(false);
      }
      gameRef.current = next;
      setGame(next);
      setSelected(selection => selection && previous?.revision === next.revision &&
        next.legal_moves.some(move => move.source === selection.source && move.index === selection.index)
        ? selection : null);
      if (next.finished) {
        exitFullscreen();
        setConfirm(null);
        setMessage(next.reason === 'completed' ? '你的顺牌已收齐' : '你的牌局已结束');
      } else if (previous?.finished) setMessage(PICK_MESSAGE);
    };
    ws.on('sync', msg => {
      setRoom(msg.room as unknown as Room);
      if (msg.game) receive(msg.game as unknown as SpiderState, true);
      else if (!gameRef.current?.finished) {
        setGame(null); gameRef.current = null; setSelected(null);
        setMessage('请返回房间，由房主开始游戏');
      }
    });
    ws.on('game_start', msg => {
      if (msg.room) setRoom(msg.room as unknown as Room);
      receive(msg.game as unknown as SpiderState, true);
    });
    ws.on('spider_state', msg => receive(msg.game as unknown as SpiderState));
    ws.on('lobby', msg => {
      const nextRoom = msg.room as unknown as Room;
      setRoom(nextRoom);
      // 房主“结束本局”时，自己已经打完的牌桌 revision 不会变；靠终局大厅消息
      // 收掉“保存中”状态，否则按钮会一直停在 pending。
      if (nextRoom?.status === 'waiting') { pendingRevision.current = null; setPending(false); }
    });
    ws.on('error', msg => { setMessage(String(msg.message)); pendingRevision.current = null; setPending(false); });
    ws.on('room_closed', () => { setMessage('房间已解散，请返回'); setGame(null); gameRef.current = null; });
    ws.connect(() => setConnected(true), () => { setConnected(false); pendingRevision.current = null; setPending(false); });
    return () => { ws.close(); socket.current = null; };
  }, [code, token, exitFullscreen]);

  useEffect(() => {
    const unsub = navigation.addListener('beforeRemove', (event: any) => {
      if (Platform.OS !== 'web' && !leaving.current) {
        event.preventDefault();
        if (fullscreen) exitFullscreen(); else setConfirm('leave');
      }
    });
    const off = onConfirmLeave(() => fullscreen ? exitFullscreen() : setConfirm('leave'));
    return () => { unsub(); off(); };
  }, [navigation, fullscreen, exitFullscreen]);

  const act = (action: string, fields: Record<string, number> = {}) => {
    const current = gameRef.current;
    if (!connected || !current || pendingRevision.current !== null) { setMessage('正在同步牌局，请稍候'); return; }
    if (current.finished) return;
    setMessage(PICK_MESSAGE);
    pendingRevision.current = current.revision; setPending(true);
    socket.current?.send({ type: 'spider_action', action, revision: current.revision, ...fields });
  };
  const pick = (source: number, index: number) => {
    if (!game || game.finished || pending || !connected) return;
    if (selected) {
      const move = game.legal_moves.find(m => m.source === selected.source &&
        m.index === selected.index && m.target === source);
      if (move) { act('move', { ...move }); return; }
      if (selected.source === source && selected.index === index) { setSelected(null); return; }
    }
    if (game.legal_moves.some(m => m.source === source && m.index === index)) {
      setSelected({ source, index }); setMessage(`已选第 ${source + 1} 列牌组，请点标有“可放”的目标列`);
    } else {
      setSelected(null); setMessage('这组牌暂时不能移动；可以换一组、发牌或撤销');
    }
  };
  const hint = () => {
    if (!game) return;
    const move = game.legal_moves.find(m => game.columns[m.target].length > 0) ?? game.legal_moves[0];
    if (move) {
      setSelected({ source: move.source, index: move.index });
      setMessage(`可以把第 ${move.source + 1} 列的牌组移到第 ${move.target + 1} 列`);
    } else setMessage(game.can_deal ? '当前没有可移动牌组，可以发下一轮牌' : '暂无合法移动，可尝试撤销或结束自己的牌局');
  };
  const backToRoom = () => {
    leaving.current = true; setConfirm(null); socket.current?.close(); onLeave();
  };
  const exitGame = async () => {
    if (pendingRevision.current !== null) { setMessage('正在保存本次操作，请稍候再离开'); return; }
    if (!token) return;
    try {
      await leaveRoom(token, code);
      backToRoom();
    } catch (error) { setMessage((error as Error).message); setConfirm(null); }
  };
  const endRound = () => {
    const current = gameRef.current;
    if (!connected || !current || pendingRevision.current !== null) {
      setMessage('正在同步牌局，请稍候');
      return;
    }
    setMessage('正在结束本局…');
    pendingRevision.current = current.revision;
    setPending(true);
    socket.current?.send({ type: 'spider_action', action: 'end_round', revision: current.revision });
  };
  const inactive = !game || game.finished;
  const isHost = room?.host_id === user?.id;
  const roundFinished = game?.finished === true && room?.status === 'waiting';
  const result = game?.reason === 'completed' ? '个人通关' : '你的牌局已结束';
  const confirmTitle = confirm === 'endRound' ? '结束所有人的牌局？'
    : confirm === 'end' ? '结束我的牌局？' : confirm === 'undo' ? '撤销上一步？' : '离开本局？';
  const confirmBody = confirm === 'endRound'
    ? '未完成的牌局会记为结束，所有人按当前进度结算并返回房间。'
    : confirm === 'end' ? '你的牌局将结束，其他玩家可以继续。'
      : confirm === 'undo' ? '你的牌桌将恢复到上一步操作之前。'
        : '你的牌局将结束并返回房间，其他玩家可以继续。';
  const confirmLabel = confirm === 'endRound' ? '确认结束本局'
    : confirm === 'end' ? '确认结束' : confirm === 'undo' ? '确认撤销' : '确认离开';
  const players = shellPlayers(room, user?.id, game).map((player, seat) => {
    const progress = room?.players[seat]?.spider_progress;
    return progress ? { ...player, identity: spiderProgressLabel(progress) } : player;
  });
  const rotated = fullscreen && width < height;
  const landscapeStyle = rotated ? { position: 'absolute' as const, width: height, height: width,
    left: (width - height) / 2, top: (height - width) / 2, transform: [{ rotate: '90deg' }] } : undefined;

  return <View style={styles.page} testID="spider-screen">
    <StatusBar hidden={fullscreen} style="auto" />
    <View testID="spider-stage" style={[styles.stage, landscapeStyle, fullscreen && !rotated && {
      paddingLeft: insets.left, paddingRight: insets.right, paddingTop: insets.top, paddingBottom: insets.bottom }]}>
    <GameShell game="spider" code={code} connected={connected} compact={fullscreen}
      players={players} style={{ backgroundColor: spiderTable }}>
    <View style={[styles.stats, fullscreen && styles.fullscreenStats]}>
      <Text style={styles.stat} testID="spider-progress">已收齐 {game?.completed ?? 0} / {game?.total_runs ?? 8} 组</Text>
      <Text style={styles.stat} testID="spider-moves">{game?.move_count ?? 0} 步</Text>
      <Text style={styles.stat}>{connected ? pending ? '保存中…' : '已自动保存' : '正在连接…'}</Text>
    </View>
    <Text accessibilityLiveRegion="polite" style={[styles.message, fullscreen && styles.fullscreenMessage]} testID="spider-message">{message}</Text>
    <View style={styles.playFrame} testID="spider-play-area">
    <ScrollView style={styles.playArea} contentContainerStyle={styles.playContent} nestedScrollEnabled>
      {!game ? <View style={styles.loading}>{!connected && <ActivityIndicator color={colors.white} />}<Text style={styles.light}>等待房间中的牌局</Text></View> : <>
        {game.finished && !roundFinished && <View style={styles.win} testID="spider-win">
          <Text style={styles.winTitle}>{result}</Text>
          <Text style={styles.light}>其他玩家仍可继续。所有人结束后可查看战绩。</Text>
        </View>}
        {!fullscreen && <View style={styles.foundations}>{Array.from({ length: game.total_runs }, (_, i) =>
          <View key={i} style={[styles.foundation, i < game.completed && styles.complete]}>
            <Spade size={17} color={i < game.completed ? colors.text : colors.white} />
            <Text style={{ color: i < game.completed ? colors.text : colors.white, fontSize: 11 }}>{i < game.completed ? 'K—A' : `${i + 1}`}</Text>
          </View>)}</View>}
        <SpiderBoard game={game} selected={selected} onPick={pick} />
      </>}
    </ScrollView>
      {!inactive && <TouchableOpacity style={styles.fullscreenButton} testID="spider-fullscreen" accessibilityRole="button"
        accessibilityLabel={fullscreen ? '退出横屏全屏' : '横屏全屏'}
        onPress={() => {
          if (fullscreen) exitFullscreen();
          else { setFullscreen(true); stopFullscreen.current = requestSpiderFullscreen(exitFullscreen); }
        }}>
        <Ionicons name={fullscreen ? 'contract-outline' : 'expand-outline'} size={20} color={colors.text} />
        <Text style={styles.buttonText}>{fullscreen ? '退出全屏' : '全屏'}</Text>
      </TouchableOpacity>}
    </View>
    {!inactive && <View style={styles.controls}>
      <TouchableOpacity style={styles.primary} testID="spider-deal" onPress={() => {
        if (!game?.can_deal) { setMessage(game?.stock_count ? '请先填满所有空列，再发牌' : '余牌已经发完'); return; }
        act('deal');
      }}><Text style={styles.primaryText}>发牌 · 剩 {Math.floor((game?.stock_count ?? 0) / (game?.columns.length ?? 10))} 轮</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-undo" onPress={() => {
        if (!game?.can_undo) { setMessage('还没有可以撤销的操作'); return; } setConfirm('undo');
      }}><Text style={styles.buttonText}>撤销</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-hint" onPress={hint}><Text style={styles.buttonText}>提示</Text></TouchableOpacity>
    </View>}
    {!fullscreen && !roundFinished && <View style={styles.footer}>
      {!inactive && <TouchableOpacity style={styles.button} testID="spider-end" onPress={() => setConfirm('end')}><Text style={styles.buttonText}>结束我的牌局</Text></TouchableOpacity>}
      {isHost && room?.status === 'playing' && (
        <TouchableOpacity style={styles.button} testID="spider-end-round" onPress={() => setConfirm('endRound')}>
          <Text style={styles.buttonText}>结束本局</Text>
        </TouchableOpacity>
      )}
      <TouchableOpacity style={styles.button} testID="spider-back" onPress={() => inactive ? backToRoom() : setConfirm('leave')}><Text style={styles.buttonText}>{inactive ? '返回房间' : '离开本局'}</Text></TouchableOpacity>
    </View>}
    </GameShell>
    </View>
    <GameResult visible={roundFinished} result={result}
      players={players} roomButtonTestID="spider-back"
      onBackToRoom={backToRoom} onViewHistory={() => { backToRoom(); navigation.navigate('History'); }}>
      <Text style={{ color: colors.textSecondary }}>已收齐 {game?.completed ?? 0} / {game?.total_runs ?? 8} 组 · 共 {game?.move_count ?? 0} 步</Text>
    </GameResult>
    <Modal visible={confirm !== null} transparent animationType="fade" supportedOrientations={['portrait', 'landscape']}
      onRequestClose={() => setConfirm(null)}>
      {confirm && <View style={[styles.overlay, landscapeStyle]}><View style={styles.dialog}>
        <Text style={styles.dialogTitle}>{confirmTitle}</Text>
        <Text style={styles.dialogBody}>{confirmBody}</Text>
        <TouchableOpacity style={styles.primary} onPress={() => {
          if (confirm === 'leave') { if (inactive) backToRoom(); else void exitGame(); return; }
          const action = confirm;
          setConfirm(null);
          if (action === 'endRound') endRound(); else act(action);
        }}>
          <Text style={styles.primaryText}>{confirmLabel}</Text></TouchableOpacity>
        <TouchableOpacity style={styles.button} onPress={() => setConfirm(null)}><Text style={styles.buttonText}>继续游戏</Text></TouchableOpacity>
      </View></View>}
    </Modal>
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  stage: { flex: 1, backgroundColor: colors.bg },
  playFrame: { flex: 1, backgroundColor: spiderTable },
  header: { padding: 16, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  eyebrow: { fontSize: 12, color: colors.textSecondary, marginBottom: 4 },
  title: { fontSize: 20, fontWeight: '800', color: colors.text },
  stats: { paddingHorizontal: 16, flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  fullscreenStats: { paddingTop: 6, paddingHorizontal: 12 },
  stat: { fontSize: 12, color: colors.white },
  message: { color: colors.white, fontSize: 12, minHeight: 44, padding: 12 },
  fullscreenMessage: { minHeight: 28, padding: 6 },
  playArea: { flex: 1, backgroundColor: spiderTable }, playContent: { paddingBottom: 72 },
  fullscreenButton: { position: 'absolute', right: 12, bottom: 12, minHeight: 44, paddingHorizontal: 12,
    flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'center',
    borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md, backgroundColor: colors.card },
  loading: { padding: 40, alignItems: 'center', gap: 16 }, light: { color: colors.white, fontSize: 13, lineHeight: 22 },
  foundations: { padding: 12, flexDirection: 'row', justifyContent: 'center', gap: 6, flexWrap: 'wrap' },
  foundation: { width: 30, height: 48, borderRadius: 5, borderWidth: 1, borderColor: colors.borderStrong,
    justifyContent: 'center', alignItems: 'center', gap: 2 },
  complete: { backgroundColor: colors.white },
  win: { padding: 20, alignItems: 'center', gap: 6 }, winTitle: { color: colors.white, fontSize: 24, fontWeight: '800' },
  controls: { flexDirection: 'row', gap: 8, padding: 12, paddingBottom: 4, flexWrap: 'wrap' },
  footer: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 12, paddingBottom: 12, gap: 8 },
  button: { minHeight: 44, paddingHorizontal: 16, paddingVertical: 12, justifyContent: 'center', alignItems: 'center',
    borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md, backgroundColor: colors.card },
  buttonText: { color: colors.text, fontSize: 14, fontWeight: '600' },
  primary: { minHeight: 44, padding: 12, paddingHorizontal: 18, backgroundColor: colors.primary,
    borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  primaryText: { color: colors.white, fontWeight: '700', fontSize: 14 },
  overlay: { flex: 1, backgroundColor: colors.overlay, justifyContent: 'center', alignItems: 'center', padding: 24 },
  dialog: { backgroundColor: colors.card, borderRadius: radius.lg, padding: spacing.lg, width: '100%', maxWidth: 400, gap: 12 },
  dialogTitle: { fontSize: 20, fontWeight: '700', color: colors.text },
  dialogBody: { fontSize: 14, lineHeight: 22, color: colors.textSecondary },
});
