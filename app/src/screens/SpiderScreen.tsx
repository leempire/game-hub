import React, { useEffect, useRef, useState } from 'react';
import { View, Text, TouchableOpacity, ScrollView, Modal, StyleSheet, Platform, ActivityIndicator } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useAuth } from '../auth';
import { RoomSocket } from '../socket';
import { Room, leaveRoom } from '../api';
import { onConfirmLeave, useModalBackGuard } from '../backGuard';
import { GameHelpButton } from '../help';
import { colors, spacing, radius, spiderTable } from '../theme';
import SpiderBoard, { Spade } from '../SpiderBoard';
import { SpiderState } from '../spider';

export default function SpiderScreen({ code, onLeave }: { code: string; onLeave: () => void }) {
  const { token, user } = useAuth();
  const navigation = useNavigation();
  const socket = useRef<RoomSocket | null>(null);
  const [game, setGame] = useState<SpiderState | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const gameRef = useRef<SpiderState | null>(null);
  const pendingRevision = useRef<number | null>(null);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState<{ source: number; index: number } | null>(null);
  const [message, setMessage] = useState('全员共享牌桌，点选明牌后再点目标列');
  const [confirm, setConfirm] = useState<'leave' | 'end' | 'undo' | null>(null);
  const leaving = useRef(false);
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    const receive = (next: SpiderState, actor?: number, acknowledge = false) => {
      const previous = gameRef.current;
      if (acknowledge || (actor === user?.id && pendingRevision.current !== null &&
          next.revision === pendingRevision.current + 1)) {
        pendingRevision.current = null; setPending(false);
      }
      if (previous && next.revision < previous.revision) return;
      gameRef.current = next;
      setGame(next);
      setSelected(selection => selection && previous &&
        JSON.stringify(previous.columns[selection.source]) === JSON.stringify(next.columns[selection.source]) &&
        next.legal_moves.some(move => move.source === selection.source && move.index === selection.index)
        ? selection : null);
      if (next.finished) setMessage(next.reason === 'completed' ? '所有顺牌已收齐，合作通关！' : '本局已结束，请返回房间');
      else if (actor !== undefined && actor !== user?.id) setMessage('队友已更新牌桌，可以继续操作');
    };
    ws.on('sync', msg => {
      setRoom(msg.room as unknown as Room);
      if (msg.game) receive(msg.game as unknown as SpiderState, undefined, true);
      else setMessage('请返回房间，由房主开始游戏');
    });
    ws.on('game_start', msg => receive(msg.game as unknown as SpiderState, undefined, true));
    ws.on('spider_state', msg => receive(msg.game as unknown as SpiderState, msg.actor_id as number | undefined));
    ws.on('lobby', msg => setRoom(msg.room as unknown as Room));
    ws.on('error', msg => { setMessage(String(msg.message)); pendingRevision.current = null; setPending(false); });
    ws.on('room_closed', () => { setMessage('房间已解散，请返回'); setGame(null); gameRef.current = null; });
    ws.connect(() => setConnected(true), () => { setConnected(false); pendingRevision.current = null; setPending(false); });
    return () => { ws.close(); socket.current = null; };
  }, [code, token, user?.id]);

  useEffect(() => {
    const unsub = navigation.addListener('beforeRemove', event => {
      if (Platform.OS !== 'web' && !leaving.current) {
        event.preventDefault(); setConfirm('leave');
      }
    });
    const off = onConfirmLeave(() => setConfirm('leave'));
    return () => { unsub(); off(); };
  }, [navigation]);

  const act = (action: string, fields: Record<string, number> = {}) => {
    const current = gameRef.current;
    if (!connected || !current || pendingRevision.current !== null) { setMessage('正在同步牌局，请稍候'); return; }
    if (current.departed.includes(user?.id ?? -1)) { setMessage('你已离开本局，请返回房间'); return; }
    setMessage('全员共享牌桌，点选明牌后再点目标列');
    pendingRevision.current = current.revision; setPending(true);
    socket.current?.send({ type: 'spider_action', action, revision: current.revision, ...fields });
  };
  const pick = (source: number, index: number) => {
    if (!game || game.finished || pending || !connected || game.departed.includes(user?.id ?? -1)) return;
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
    } else setMessage(game.can_deal ? '当前没有可移动牌组，可以发下一轮牌' : '暂无合法移动，可尝试撤销或由房主结束本局');
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
  const inactive = !game || game.finished || game.departed.includes(user?.id ?? -1);
  const isHost = room?.host_id === user?.id;

  return <View style={styles.page}>
    <View style={styles.header}>
      <View><Text style={styles.eyebrow}>合作接龙 · 房间 {code}</Text><Text style={styles.title}>一起把顺牌收齐</Text></View>
      <GameHelpButton game="spider" />
    </View>
    <View style={styles.stats}>
      <Text style={styles.stat} testID="spider-progress">已收齐 {game?.completed ?? 0} / {game?.total_runs ?? 8} 组</Text>
      <Text style={styles.stat} testID="spider-moves">{game?.move_count ?? 0} 步</Text>
      <Text style={styles.stat}>{connected ? pending ? '保存中…' : '已自动保存' : '正在连接…'}</Text>
    </View>
    <Text accessibilityLiveRegion="polite" style={styles.message} testID="spider-message">{message}</Text>
    <ScrollView style={styles.playArea} contentContainerStyle={styles.playContent} nestedScrollEnabled>
      {!game ? <View style={styles.loading}>{!connected && <ActivityIndicator color={colors.white} />}<Text style={styles.light}>等待房间中的牌局</Text></View> : <>
        {game.finished && <View style={styles.win} testID="spider-win">
          <Text style={styles.winTitle}>{game.reason === 'completed' ? '合作通关' : '本局已结束'}</Text><Text style={styles.light}>已收齐 {game.completed} / {game.total_runs} 组，共 {game.move_count} 步</Text>
          <Text style={styles.light}>这局已记入历史战绩，返回房间后可开始下一局。</Text>
        </View>}
        <View style={styles.foundations}>{Array.from({ length: game.total_runs }, (_, i) =>
          <View key={i} style={[styles.foundation, i < game.completed && styles.complete]}>
            <Spade size={17} color={i < game.completed ? colors.text : colors.white} />
            <Text style={{ color: i < game.completed ? colors.text : colors.white, fontSize: 11 }}>{i < game.completed ? 'K—A' : `${i + 1}`}</Text>
          </View>)}</View>
        <SpiderBoard game={game} selected={selected} onPick={pick} />
      </>}
    </ScrollView>
    {!inactive && <View style={styles.controls}>
      <TouchableOpacity style={styles.primary} testID="spider-deal" onPress={() => {
        if (!game?.can_deal) { setMessage(game?.stock_count ? '请先填满所有空列，再发牌' : '余牌已经发完'); return; }
        setMessage('每列发一张新牌'); act('deal');
      }}><Text style={styles.primaryText}>发牌 · 剩 {Math.floor((game?.stock_count ?? 0) / (game?.columns.length ?? 10))} 轮</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-undo" onPress={() => {
        if (!game?.can_undo) { setMessage('还没有可以撤销的操作'); return; } setConfirm('undo');
      }}><Text style={styles.buttonText}>撤销</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-hint" onPress={hint}><Text style={styles.buttonText}>提示</Text></TouchableOpacity>
    </View>}
    <View style={styles.footer}>
      {isHost && !inactive && <TouchableOpacity style={styles.button} testID="spider-end" onPress={() => setConfirm('end')}><Text style={styles.buttonText}>结束本局</Text></TouchableOpacity>}
      <TouchableOpacity style={styles.button} testID="spider-back" onPress={() => inactive ? backToRoom() : setConfirm('leave')}><Text style={styles.buttonText}>{inactive ? '返回房间' : '离开本局'}</Text></TouchableOpacity>
    </View>
    <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
      <View style={styles.overlay}><View style={styles.dialog}>
        <Text style={styles.dialogTitle}>{confirm === 'end' ? '结束全队牌局？' : confirm === 'undo' ? '撤销全队最近一步？' : '离开本局？'}</Text>
        <Text style={styles.dialogBody}>{confirm === 'end' ? '当前牌局会结束并记录到战绩。全员返回房间后可重新准备开局。' : confirm === 'undo' ? '会回退共享牌桌的最近一步，包括队友的操作。' : '你将退出本局并返回房间，队友可以继续。最后一人离开时本局结束。'}</Text>
        <TouchableOpacity style={styles.primary} onPress={() => { if (confirm === 'leave') { if (inactive) backToRoom(); else void exitGame(); } else { const action = confirm; setConfirm(null); if (action) act(action); } }}>
          <Text style={styles.primaryText}>{confirm === 'end' ? '确认结束' : confirm === 'undo' ? '确认撤销' : '确认离开'}</Text></TouchableOpacity>
        <TouchableOpacity style={styles.button} onPress={() => setConfirm(null)}><Text style={styles.buttonText}>继续游戏</Text></TouchableOpacity>
      </View></View>
    </Modal>
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  header: { padding: 16, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  eyebrow: { fontSize: 12, color: colors.textSecondary, marginBottom: 4 },
  title: { fontSize: 20, fontWeight: '800', color: colors.text },
  stats: { paddingHorizontal: 16, flexDirection: 'row', justifyContent: 'space-between', gap: 8 },
  stat: { fontSize: 12, color: colors.textSecondary },
  message: { color: colors.textSecondary, fontSize: 12, minHeight: 44, padding: 12 },
  playArea: { flex: 1, backgroundColor: spiderTable }, playContent: { paddingBottom: 24 },
  loading: { padding: 40, alignItems: 'center', gap: 16 }, light: { color: colors.white, fontSize: 13, lineHeight: 22 },
  foundations: { padding: 12, flexDirection: 'row', justifyContent: 'center', gap: 6, flexWrap: 'wrap' },
  foundation: { width: 34, height: 48, borderRadius: 5, borderWidth: 1, borderColor: colors.borderStrong,
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
