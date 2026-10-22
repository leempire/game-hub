import React, { useEffect, useRef, useState } from 'react';
import { View, Text, TouchableOpacity, ScrollView, Modal, StyleSheet, Platform, ActivityIndicator } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useAuth } from '../auth';
import { RoomSocket } from '../socket';
import { Room } from '../api';
import { onConfirmLeave, useModalBackGuard } from '../backGuard';
import { GameHelpButton } from '../help';
import { colors, spacing, radius, spiderTable } from '../theme';
import SpiderBoard, { Spade } from '../SpiderBoard';
import { SpiderState } from '../spider';

export default function SpiderScreen({ code, onLeave }: { code: string; onLeave: () => void }) {
  const { token } = useAuth();
  const navigation = useNavigation();
  const socket = useRef<RoomSocket | null>(null);
  const [game, setGame] = useState<SpiderState | null>(null);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [selected, setSelected] = useState<{ source: number; index: number } | null>(null);
  const [message, setMessage] = useState('点选明牌，再点目标列；左右滑动查看十列');
  const [confirm, setConfirm] = useState<'leave' | 'new' | null>(null);
  const leaving = useRef(false);
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  useEffect(() => {
    if (!token) return;
    const ws = new RoomSocket(code, token);
    socket.current = ws;
    const receive = (next: SpiderState) => {
      setGame(next); setPending(false); setSelected(null);
      if (next.finished) setMessage('八组牌已全部收齐，恭喜通关！');
    };
    ws.on('sync', msg => {
      if (msg.game) receive(msg.game as unknown as SpiderState);
      else if ((msg.room as unknown as Room)?.game === 'spider') ws.start();
    });
    ws.on('game_start', msg => receive(msg.game as unknown as SpiderState));
    ws.on('spider_state', msg => receive(msg.game as unknown as SpiderState));
    ws.on('error', msg => { setMessage(String(msg.message)); setPending(false); });
    ws.connect(() => setConnected(true), () => { setConnected(false); setPending(false); });
    return () => { ws.close(); socket.current = null; };
  }, [code, token]);

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
    if (!connected || !game || pending) { setMessage('正在同步牌局，请稍候'); return; }
    setMessage('点选明牌，再点目标列；左右滑动查看十列');
    setPending(true);
    socket.current?.send({ type: 'spider_action', action, revision: game.revision, ...fields });
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
    } else setMessage(game.can_deal ? '当前没有可移动牌组，可以发下一轮牌' : '暂无合法移动，可尝试撤销或新牌局');
  };
  const saveAndLeave = () => {
    if (pending) { setMessage('正在保存本次操作，请稍候再返回'); return; }
    leaving.current = true; setConfirm(null); socket.current?.close(); onLeave();
  };

  return <View style={styles.page}>
    <View style={styles.header}>
      <View><Text style={styles.eyebrow}>单人 · 单花色</Text><Text style={styles.title}>把每一组理成顺序</Text></View>
      <GameHelpButton game="spider" />
    </View>
    <View style={styles.stats}>
      <Text style={styles.stat} testID="spider-progress">已收齐 {game?.completed ?? 0} / 8 组</Text>
      <Text style={styles.stat} testID="spider-moves">{game?.move_count ?? 0} 步</Text>
      <Text style={styles.stat}>{connected ? pending ? '保存中…' : '已自动保存' : '正在连接…'}</Text>
    </View>
    <Text accessibilityLiveRegion="polite" style={styles.message} testID="spider-message">{message}</Text>
    <ScrollView style={styles.playArea} contentContainerStyle={styles.playContent} nestedScrollEnabled>
      {!game ? <View style={styles.loading}><ActivityIndicator color={colors.white} /><Text style={styles.light}>正在打开牌局…</Text></View> : <>
        {game.finished && <View style={styles.win} testID="spider-win">
          <Text style={styles.winTitle}>恭喜通关</Text><Text style={styles.light}>八组牌已全部收齐，共 {game.move_count} 步</Text>
          <Text style={styles.light}>这局已记入历史战绩，可以开始新牌局。</Text>
        </View>}
        <View style={styles.foundations}>{Array.from({ length: 8 }, (_, i) =>
          <View key={i} style={[styles.foundation, i < game.completed && styles.complete]}>
            <Spade size={17} color={i < game.completed ? colors.text : colors.white} />
            <Text style={{ color: i < game.completed ? colors.text : colors.white, fontSize: 11 }}>{i < game.completed ? 'K—A' : `${i + 1}`}</Text>
          </View>)}</View>
        <SpiderBoard game={game} selected={selected} onPick={pick} />
      </>}
    </ScrollView>
    <View style={styles.controls}>
      <TouchableOpacity style={styles.primary} testID="spider-deal" onPress={() => {
        if (!game?.can_deal) { setMessage(game?.stock_count ? '请先填满所有空列，再发牌' : '余牌已经发完'); return; }
        setMessage('每列发一张新牌'); act('deal');
      }}><Text style={styles.primaryText}>发牌 · 剩 {Math.floor((game?.stock_count ?? 0) / 10)} 轮</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-undo" onPress={() => {
        if (!game?.can_undo) { setMessage('还没有可以撤销的操作'); return; } act('undo');
      }}><Text style={styles.buttonText}>撤销</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-hint" onPress={hint}><Text style={styles.buttonText}>提示</Text></TouchableOpacity>
    </View>
    <View style={styles.footer}>
      <TouchableOpacity style={styles.button} testID="spider-new" onPress={() => setConfirm('new')}><Text style={styles.buttonText}>新牌局</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} testID="spider-save" onPress={() => setConfirm('leave')}><Text style={styles.buttonText}>保存并返回</Text></TouchableOpacity>
    </View>
    <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
      <View style={styles.overlay}><View style={styles.dialog}>
        <Text style={styles.dialogTitle}>{confirm === 'new' ? '开始新牌局？' : '保存并返回？'}</Text>
        <Text style={styles.dialogBody}>{confirm === 'new' ? '当前牌局会结束并记录到战绩，重新随机发牌。' : '当前进度已自动保存，下次点蜘蛛纸牌即可继续。'}</Text>
        <TouchableOpacity style={styles.primary} onPress={() => { if (confirm === 'leave') saveAndLeave(); else { setConfirm(null); act('new'); } }}>
          <Text style={styles.primaryText}>{confirm === 'new' ? '确认新牌局' : '确认返回'}</Text></TouchableOpacity>
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
