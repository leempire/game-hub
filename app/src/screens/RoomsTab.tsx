import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
  RefreshControl, ActivityIndicator, Platform, AppState,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useIsFocused } from '@react-navigation/native';
import { useAuth } from '../auth';
import { listRooms, createRoom, joinRoom, Room } from '../api';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';
import { settingValueLabel } from '../roomSettings';
import { useModalBackGuard } from '../backGuard';
import { CreateRoomModal, JoinRoomModal } from '../roomForms';
import { gameShort, gameIonicon, type GameKey } from '../games';

interface Props {
  onNavigateToRoom: (code: string) => void;
}

// 房间只有等待中/游戏中两种状态——对局结束的房间回到等待中
const STATUS_LABELS: Record<string, string> = {
  waiting: '等待中',
  playing: '对局中',
};

const STATUS_COLORS: Record<string, string> = {
  waiting: colors.accent,
  playing: colors.warning,
};

/** 轮询间隔。3 秒太频繁：房间列表不是实时对战消息，5 秒足够看出新房间。 */
const POLL_MS = 5000;

export default function RoomsTab({ onNavigateToRoom }: Props) {
  const { token, user } = useAuth();
  const isFocused = useIsFocused();
  const [rooms, setRooms] = useState<Room[]>([]);
  // 首次请求结束前不能显示「暂无房间」——否则用户先看到假空态，几百毫秒后
  // 列表突然冒出来；断网时更会永远停在空态（以为平台没人）。
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showJoin, setShowJoin] = useState(false);
  const [roomName, setRoomName] = useState('');
  const [roomPwd, setRoomPwd] = useState('');
  const [createGame, setCreateGame] = useState<GameKey>('gomoku');
  const [joinCode, setJoinCode] = useState('');
  const [joinPwd, setJoinPwd] = useState('');
  const [submitting, setSubmitting] = useState(false);
  // 弹窗内的错误（toast 会被 Modal 盖住，见 toast.tsx 与 CLAUDE.md）
  const [modalError, setModalError] = useState<string | null>(null);
  // 提交中点「取消」之后，已经在路上的请求回来时不能再把人带进房
  const createCancelledRef = useRef(false);
  const joinCancelledRef = useRef(false);
  // 请求序号：慢网下后返回的旧响应不能覆盖新列表
  const seqRef = useRef(0);
  const [appActive, setAppActive] = useState(AppState.currentState === 'active');

  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => setAppActive(s === 'active'));
    return () => sub.remove();
  }, []);

  const fetchRooms = useCallback(async () => {
    if (!token) return;
    const seq = ++seqRef.current;
    try {
      const result = await listRooms(token);
      if (seq !== seqRef.current) return;   // 过期响应：丢掉
      setRooms(result.items);
      setLoadError(null);
    } catch (err) {
      if (seq !== seqRef.current) return;
      // 有旧数据时保留列表，只提示「连接不稳定」；没有数据才显示错误页
      setLoadError((err as Error).message);
    } finally {
      if (seq === seqRef.current) setLoaded(true);
    }
  }, [token]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await fetchRooms();
    setRefreshing(false);
  }, [fetchRooms]);

  // 只在「页面可见 + App 在前台」时轮询。Tab 页在 Stack 下常驻挂载，
  // 不做这层守卫的话进对局、切 Tab、切后台都还在每 3 秒请求一次。
  useEffect(() => {
    if (!isFocused || !appActive || !token) return;
    void fetchRooms();
    const timer = setInterval(() => { void fetchRooms(); }, POLL_MS);
    return () => clearInterval(timer);
  }, [isFocused, appActive, token, fetchRooms]);

  const closeCreate = () => {
    createCancelledRef.current = true;
    setShowCreate(false);
    setRoomName('');
    setRoomPwd('');
    setCreateGame('gomoku');
    setModalError(null);
  };

  const closeJoin = () => {
    joinCancelledRef.current = true;
    setShowJoin(false);
    setJoinCode('');
    setJoinPwd('');
    setModalError(null);
  };

  useModalBackGuard(showCreate, closeCreate);
  useModalBackGuard(showJoin, closeJoin);

  const handleCreate = async () => {
    if (!token) return;
    createCancelledRef.current = false;
    setModalError(null);
    setSubmitting(true);
    try {
      const room = await createRoom(token, createGame, roomName || null, roomPwd || null);
      if (createCancelledRef.current) return;   // 用户已经点了取消
      setShowCreate(false);
      setRoomName('');
      setRoomPwd('');
      setCreateGame('gomoku');
      onNavigateToRoom(room.code);
    } catch (err) {
      if (!createCancelledRef.current) setModalError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  // 点击房间卡片：仅可加入 未开始/已结束(上一局完，房间仍活着)且未满
  // 或 已开始且本人是原玩家（断线重连）；其余给出明确拒绝。
  // 有密码的等待房弹出加入框输入密码。
  const handleRoomPress = async (room: Room) => {
    if (!token || !user) return;
    const isMember = room.players.some((p) => p.id === user.id);

    if (isMember) {
      // 已是成员：直接进房（若对局中，join 接口会标记重连）
      try {
        if (room.status === 'playing') await joinRoom(token, room.code, null);
        onNavigateToRoom(room.code);
      } catch (err) {
        notify('进入失败', (err as Error).message);
      }
      return;
    }
    if (room.status === 'playing') {
      notify('无法加入', '对局已开始，仅原玩家可断线重连');
      return;
    }
    // 等待中且非成员：未满即可加入（打完一局的房间也回到等待中，可续开新一局）
    if (room.players.length >= room.max_players) {
      notify('无法加入', '房间已满');
      return;
    }
    if (room.has_password) {
      setJoinCode(room.code);
      setJoinPwd('');
      setShowJoin(true);
      return;
    }
    try {
      const joined = await joinRoom(token, room.code, null);
      onNavigateToRoom(joined.code);
    } catch (err) {
      notify('加入失败', (err as Error).message);
    }
  };

  const handleJoin = async () => {
    if (!token) return;
    const code = joinCode.trim().toUpperCase();
    setModalError(null);
    if (!code) {
      setModalError('请输入房间号');
      return;
    }
    joinCancelledRef.current = false;
    setSubmitting(true);
    try {
      const room = await joinRoom(token, code, joinPwd || null);
      if (joinCancelledRef.current) return;
      setShowJoin(false);
      setJoinCode('');
      setJoinPwd('');
      onNavigateToRoom(room.code);
    } catch (err) {
      if (!joinCancelledRef.current) setModalError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <View style={styles.container}>
      {/* Header actions */}
      <View style={styles.header}>
        <Text style={styles.title}>房间列表</Text>
        <View style={styles.headerBtns}>
          <TouchableOpacity style={styles.headerBtn} onPress={() => setShowJoin(true)}>
            <Text style={styles.headerBtnText}>加入</Text>
          </TouchableOpacity>
          <TouchableOpacity style={[styles.headerBtn, styles.headerBtnPrimary]} onPress={() => setShowCreate(true)}>
            <Text style={styles.headerBtnPrimaryText}>创建</Text>
          </TouchableOpacity>
        </View>
      </View>

      {!loaded ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>正在加载房间…</Text>
        </View>
      ) : loadError && rooms.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="cloud-offline-outline" size={48} color={colors.textMuted} />
          <Text style={styles.emptyText}>房间列表加载失败</Text>
          <Text style={styles.emptyHint}>{loadError}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={refresh}>
            <Text style={styles.retryText}>重试</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.primary} />}
        >
          {loadError ? (
            <View style={styles.errorBanner}>
              <Ionicons name="warning-outline" size={14} color={colors.warning} />
              <Text style={styles.errorBannerText}>连接不稳定，列表可能不是最新</Text>
            </View>
          ) : null}
          {rooms.length === 0 ? (
            <View style={styles.emptyWrap}>
              <Ionicons name="home-outline" size={48} color={colors.textFaint} />
              <Text style={styles.emptyText}>暂无房间</Text>
              <Text style={styles.emptyHint}>创建一个房间开始游戏吧</Text>
            </View>
          ) : (
            rooms.map((room) => (
              <TouchableOpacity
                key={room.code}
                style={styles.roomCard}
                onPress={() => handleRoomPress(room)}
                activeOpacity={0.8}
              >
                <View style={styles.roomHeader}>
                  <Text style={styles.roomName}>{room.name}</Text>
                  <View style={[styles.statusBadge, { backgroundColor: STATUS_COLORS[room.status] || colors.textMuted }]}>
                    <Text style={styles.statusText}>{STATUS_LABELS[room.status] || room.status}</Text>
                  </View>
                </View>
                <View style={styles.roomMeta}>
                  <Text style={styles.roomCode}>#{room.code}</Text>
                  <View style={styles.gameBadge}>
                    <Ionicons name={gameIonicon(room.game)} size={12} color={colors.white} />
                    <Text style={styles.gameBadgeText}>
                      {room.game === 'minesweeper'
                        // 难度名由服务端下发（`room.settings`），客户端不抄一份档位表
                        ? `${gameShort(room.game)}·${settingValueLabel(room, 'difficulty') ?? ''}`
                        : gameShort(room.game)}
                    </Text>
                  </View>
                  <View style={styles.roomPlayers}>
                    <Ionicons name="people" size={14} color={colors.textSecondary} />
                    <Text style={styles.roomPlayersText}> {room.players.length}/{room.max_players}</Text>
                  </View>
                  {room.has_password && (
                    <View style={styles.lockBadge}>
                      <Ionicons name="lock-closed" size={12} color={colors.textMuted} />
                      <Text style={styles.lockText}>有密码</Text>
                    </View>
                  )}
                </View>
              </TouchableOpacity>
            ))
          )}
        </ScrollView>
      )}

      <CreateRoomModal
        visible={showCreate}
        game={createGame}
        name={roomName}
        password={roomPwd}
        error={modalError}
        submitting={submitting}
        onPickGame={setCreateGame}
        onChangeName={setRoomName}
        onChangePassword={setRoomPwd}
        onSubmit={() => void handleCreate()}
        onClose={closeCreate}
      />
      <JoinRoomModal
        visible={showJoin}
        code={joinCode}
        password={joinPwd}
        error={modalError}
        submitting={submitting}
        onChangeCode={setJoinCode}
        onChangePassword={setJoinPwd}
        onSubmit={() => void handleJoin()}
        onClose={closeJoin}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.md,
    paddingBottom: spacing.sm,
    backgroundColor: colors.card,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  title: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text },
  headerBtns: { flexDirection: 'row', gap: spacing.sm },
  headerBtn: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.primary,
  },
  headerBtnText: { color: colors.primary, fontWeight: '600', fontSize: fontSize.sm },
  headerBtnPrimary: { backgroundColor: colors.primary, borderColor: colors.primary },
  headerBtnPrimaryText: { color: colors.white, fontWeight: '600', fontSize: fontSize.sm },
  scroll: { padding: spacing.md, paddingBottom: spacing.xxl },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.lg },
  loadingText: { marginTop: spacing.sm, fontSize: fontSize.sm, color: colors.textSecondary },
  retryBtn: {
    marginTop: spacing.md,
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.xl,
    borderRadius: radius.md,
    backgroundColor: colors.primary,
  },
  retryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    backgroundColor: colors.bg,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    marginBottom: spacing.sm,
  },
  errorBannerText: { flex: 1, fontSize: fontSize.xs, color: colors.warning },
  emptyWrap: { alignItems: 'center', paddingTop: spacing.xxl },
  emptyIcon: { marginBottom: spacing.md },
  emptyText: { fontSize: fontSize.lg, fontWeight: '600', color: colors.text },
  emptyHint: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: spacing.xs },
  roomCard: {
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.sm,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 4,
    elevation: 2,
  },
  roomHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  roomName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text, flex: 1 },
  statusBadge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.full,
    marginLeft: spacing.sm,
  },
  statusText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
  roomMeta: { flexDirection: 'row', alignItems: 'center', marginTop: spacing.xs, gap: spacing.md },
  roomCode: { fontSize: fontSize.sm, color: colors.textSecondary, fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' },
  roomPlayers: { flexDirection: 'row', alignItems: 'center' },
  roomPlayersText: { fontSize: fontSize.sm, color: colors.textSecondary },
  lockBadge: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  lockText: { fontSize: fontSize.xs, color: colors.textMuted },
  gameBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
    backgroundColor: colors.primary,
    borderRadius: radius.full,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  gameBadgeText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
});

