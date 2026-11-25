/** Daily challenge and per-game leaderboards, backed by indexed battle records.
 *
 * 游戏有 15 款，排行榜原先每类条件各铺一排芯片，一屏十几行按钮还很难找。
 * 现在只留「游戏 / 榜单 / 筛选」三行字段，点开弹窗换值；每日挑战与排行榜
 * 是两个入口（游戏页只进每日挑战，我的页各一条），进屏后仍可切换。 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { RouteProp, useIsFocused, useNavigation, useRoute } from '@react-navigation/native';
import { useAuth } from '../auth';
import { GAMES, gameTitle, type GameKey } from '../games';
import { Card } from '../ui';
import { colors, fontSize, radius, spacing } from '../theme';
import { leaderboard, startChallenge, todayChallenges } from '../competition';
import type { DailyStatus, Leaderboard } from '../competition';
import { ChoiceField, ChoiceModal, GameChoiceModal } from '../choice';
import type { ChoiceGroup, ChoiceOption } from '../choice';
import type { RootStackParamList } from '../navigation';

const WINDOWS: ChoiceOption[] = [{ key: 'all', label: '全部' }, { key: 'day', label: '今日' },
  { key: 'week', label: '近 7 天' }, { key: 'month', label: '近 30 天' }];
const DIFFICULTIES: ChoiceOption[] = [{ key: 'beginner', label: '初级' }, { key: 'intermediate', label: '中级' },
  { key: 'advanced', label: '高级' }];

type Tab = 'daily' | 'boards';

export default function CompetitionScreen() {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const route = useRoute<RouteProp<RootStackParamList, 'Competition'>>();
  const focused = useIsFocused();
  const [tab, setTab] = useState<Tab>(route.params?.tab ?? 'daily');
  const [game, setGame] = useState('gomoku');
  const [metric, setMetric] = useState('wins');
  const [window, setWindow] = useState('all');
  const [difficulty, setDifficulty] = useState('intermediate');
  const [rulesKey, setRulesKey] = useState('');
  const [date, setDate] = useState<string | undefined>();
  const [daily, setDaily] = useState<DailyStatus | null>(null);
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  // 三个选择弹窗：游戏 / 榜单指标 / 其余筛选条件
  const [gameOpen, setGameOpen] = useState(false);
  const [metricOpen, setMetricOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const request = useRef(0);
  const active = useRef(true);
  const startLock = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  // 从「游戏」或「我的」进来时带 tab；再次从另一入口进来也要跟着切。
  useEffect(() => {
    const next = route.params?.tab;
    if (next) setTab(next);
  }, [route.params?.tab]);
  const load = useCallback(async () => {
    if (!token || !focused) return;
    const sequence = ++request.current;
    setLoading(true); setError(null);
    try {
      if (tab === 'daily') {
        const result = await todayChallenges(token);
        if (sequence === request.current) setDaily(result);
      } else {
        const result = await leaderboard(token, game, metric, window, difficulty, date, rulesKey);
        if (sequence === request.current) setBoard(result);
      }
    } catch (cause) {
      if (sequence === request.current) setError((cause as Error).message);
    } finally {
      if (sequence === request.current) setLoading(false);
    }
  }, [token, focused, tab, game, metric, window, difficulty, date, rulesKey]);
  useEffect(() => {
    let current = true;
    void Promise.resolve().then(() => { if (current) return load(); });
    return () => { current = false; request.current += 1; };
  }, [load]);
  const start = async (key: string) => {
    if (!token || startLock.current) return;
    startLock.current = true; setStarting(key); setError(null);
    try {
      const room = await startChallenge(token, key);
      if (active.current) navigation.navigate('Room', { code: room.code });
    } catch (cause) {
      if (active.current) setError((cause as Error).message);
    } finally {
      startLock.current = false;
      if (active.current) setStarting(null);
    }
  };
  const metrics: ChoiceOption[] = [{ key: 'wins', label: '胜场' },
    ...(['2048', 'tetris'].includes(game) ? [{ key: 'score', label: '最佳分数' }] : []),
    ...(game === 'minesweeper' ? [{ key: 'time', label: '最快通关' }] : [])];
  const fmt = (value: number) => metric === 'time' ? `${(value / 1000).toFixed(1)} 秒` : String(value);
  const metricLabel = metrics.find((item) => item.key === metric)?.label ?? '胜场';
  const windowLabel = WINDOWS.find((item) => item.key === window)?.label ?? '全部';
  const difficultyLabel = DIFFICULTIES.find((item) => item.key === difficulty)?.label ?? '中级';
  const scopeLabel = board?.scopes.find((item) => item.key === (rulesKey || board.rules_key))?.label
    ?? board?.rules_label;
  const filterSummary = date
    ? `${date} 每日榜`
    : [windowLabel, game === 'minesweeper' ? difficultyLabel : null,
      board && board.scopes.length > 1 ? scopeLabel : null].filter(Boolean).join(' · ');
  const filterGroups: ChoiceGroup[] = [];
  if (!date) {
    filterGroups.push({ title: '时间范围', options: WINDOWS });
    if (game === 'minesweeper') filterGroups.push({ title: '扫雷难度', options: DIFFICULTIES });
    if (board && board.scopes.length > 1) {
      filterGroups.push({
        title: '人数 / 房规',
        options: board.scopes.map((scope) => ({ key: scope.key, label: scope.label })),
      });
    }
  }
  const filterActive: string[] = [
    ...(!date ? [window] : []),
    ...(!date && game === 'minesweeper' ? [difficulty] : []),
    ...(!date && board && board.scopes.length > 1 ? [rulesKey || board.rules_key] : []),
  ];
  const pickGame = (key: GameKey) => {
    setGame(key); setMetric('wins'); setRulesKey(''); setDate(undefined); setBoard(null); setGameOpen(false);
  };
  const pickFilter = (key: string) => {
    if (WINDOWS.some((item) => item.key === key)) { setWindow(key); return; }
    if (DIFFICULTIES.some((item) => item.key === key)) {
      setDifficulty(key); setRulesKey(''); return;
    }
    setRulesKey(key);
  };
  const openDailyBoard = (item: { game: string }) => {
    setGame(item.game);
    setMetric(item.game === 'minesweeper' ? 'time' : 'score');
    setRulesKey(''); setDate(daily?.date);
    setTab('boards');
  };
  return <ScrollView contentContainerStyle={styles.page} testID="competition-screen">
    <View style={styles.tabs}>
      {([{ key: 'daily', label: '每日挑战' }, { key: 'boards', label: '排行榜' }] as const).map((item) => (
        <TouchableOpacity key={item.key} testID={`competition-tab-${item.key}`}
          accessibilityRole="button" accessibilityState={{ selected: tab === item.key }}
          style={[styles.tab, tab === item.key && styles.tabActive]} onPress={() => setTab(item.key)}>
          <Text style={[styles.tabText, tab === item.key && styles.tabTextActive]}>{item.label}</Text>
        </TouchableOpacity>
      ))}
    </View>
    {loading && <ActivityIndicator accessibilityLabel="加载中" color={colors.primary} />}
    {error && <Card><Text accessibilityRole="alert" style={styles.error}>{error}</Text>
      <TouchableOpacity style={styles.button} onPress={() => void load()}><Text style={styles.onPrimary}>重试</Text></TouchableOpacity>
    </Card>}
    {tab === 'daily' && daily && <>
      <Text style={styles.title}>每日同题 · {daily.date}（UTC）</Text>
      <Text style={styles.hint}>全服相同序列或雷盘，单人挑战。自然完成才上榜，退出、认输与掉线不计完成；可重试取最好成绩。</Text>
      {daily.games.map(item => <Card key={item.game} style={styles.card}>
        <Text style={styles.title}>{gameTitle(item.game)}</Text>
        <Text style={styles.hint}>{item.completed ? '今日已完成' : '今日未完成'} · 连续 {item.streak} 天</Text>
        {item.game === 'minesweeper' && <Text style={styles.hint}>固定初级盘，首次从正中央翻开。</Text>}
        {item.best_value != null && <Text style={styles.hint}>今日最佳：{item.game === 'minesweeper'
          ? `${(item.best_value / 1000).toFixed(1)} 秒` : item.best_value}</Text>}
        <TouchableOpacity testID={`challenge-start-${item.game}`} style={styles.button}
          disabled={!!starting} onPress={() => void start(item.game)}>
          <Text style={styles.onPrimary}>{starting === item.game ? '正在进入…' : item.room_code ? '继续挑战' : item.completed ? '再次挑战' : '开始挑战'}</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.secondary} onPress={() => openDailyBoard(item)}>
          <Text style={styles.chipText}>查看今日榜</Text>
        </TouchableOpacity>
      </Card>)}
    </>}
    {tab === 'boards' && <>
      <ChoiceField testID="board-game" label="游戏" value={gameTitle(game)} onPress={() => setGameOpen(true)} />
      <View style={styles.filterRow}>
        <ChoiceField testID="board-metric" label="榜单" value={metricLabel}
          onPress={() => setMetricOpen(true)} style={styles.filterHalf} />
        {filterGroups.length > 0 && <ChoiceField testID="board-filters" label="筛选" value={filterSummary}
          onPress={() => setFilterOpen(true)} style={styles.filterHalf} />}
      </View>
      {date && <TouchableOpacity style={styles.secondary} onPress={() => setDate(undefined)}>
        <Text style={styles.chipText}>{date} 每日榜 · 切回普通榜</Text>
      </TouchableOpacity>}
      <Text style={styles.hint}>不计人机局和练习局；按相同人数和房规分榜。并列同名次，同分按用户编号稳定排列。</Text>
      {!loading && board && <Card style={styles.card}>
        <Text style={styles.title}>{gameTitle(board.game)} · {board.rules_label}</Text>
        {!board.items.length && <Text style={styles.hint}>暂无成绩，完成正式对局后再来看看。</Text>}
        {board.items.map(item => <View key={item.user_id} style={styles.row}>
          <Text style={styles.rank}>{item.rank}</Text><Text style={styles.name} numberOfLines={1}>
            {item.nickname || item.username}{item.user_id === user?.id ? '（我）' : ''}</Text>
          <Text style={styles.value}>{fmt(item.value)}</Text>
        </View>)}
        <Text style={styles.hint}>{board.own ? `我的名次：${board.own.rank} · ${fmt(board.own.value)}` : '你还没有符合本榜条件的成绩'}</Text>
      </Card>}
    </>}
    <GameChoiceModal visible={gameOpen} value={game as GameKey}
      onPick={pickGame} onClose={() => setGameOpen(false)} />
    <ChoiceModal visible={metricOpen} title="榜单指标" groups={[{ options: metrics }]}
      value={metric} onPick={(key) => { setMetric(key); setBoard(null); }} onClose={() => setMetricOpen(false)} />
    <ChoiceModal visible={filterOpen} title="筛选条件" groups={filterGroups}
      activeKeys={filterActive} onPick={pickFilter} onClose={() => setFilterOpen(false)} closeOnPick={false} />
  </ScrollView>;
}
const styles = StyleSheet.create({
  page: { padding: spacing.md, gap: spacing.sm, paddingBottom: spacing.xxl, backgroundColor: colors.bg },
  tabs: { flexDirection: 'row', backgroundColor: colors.surfaceMuted, padding: spacing.xs, borderRadius: radius.md, gap: spacing.xs },
  tab: { flex: 1, minHeight: 44, justifyContent: 'center', alignItems: 'center', borderRadius: radius.sm },
  tabActive: { backgroundColor: colors.primary },
  tabText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  tabTextActive: { color: colors.white },
  filterRow: { flexDirection: 'row', gap: spacing.sm },
  filterHalf: { flex: 1 },
  card: { gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700' },
  hint: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 21 },
  error: { color: colors.danger, fontSize: fontSize.md },
  button: { minHeight: 48, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, backgroundColor: colors.primary },
  onPrimary: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  secondary: { minHeight: 44, alignItems: 'center', justifyContent: 'center', padding: spacing.sm },
  chipText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  row: { flexDirection: 'row', gap: spacing.sm, alignItems: 'center', paddingVertical: spacing.sm, borderBottomWidth: 1, borderBottomColor: colors.border },
  rank: { width: 30, color: colors.primary, fontWeight: '700' },
  name: { flex: 1, color: colors.text, fontSize: fontSize.md },
  value: { color: colors.text, fontWeight: '700' },
});