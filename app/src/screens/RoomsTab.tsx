import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
  RefreshControl, Modal, TextInput, ActivityIndicator, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { listRooms, createRoom, joinRoom, Room } from '../api';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';
import { settingValueLabel } from '../roomSettings';
import { useModalBackGuard } from '../backGuard';
import { GAMES, gameShort, gameIonicon, type GameKey } from '../games';

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

export default function RoomsTab({ onNavigateToRoom }: Props) {
  const { token, user } = useAuth();
  const [rooms, setRooms] = useState<Room[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showJoin, setShowJoin] = useState(false);
  useModalBackGuard(showCreate, () => setShowCreate(false));
  useModalBackGuard(showJoin, () => setShowJoin(false));
  const [roomName, setRoomName] = useState('');
  const [roomPwd, setRoomPwd] = useState('');
  const [createGame, setCreateGame] = useState<GameKey>('gomoku');
  const [joinCode, setJoinCode] = useState('');
  const [joinPwd, setJoinPwd] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchRooms = useCallback(async () => {
    if (!token) return;
    try {
      const result = await listRooms(token);
      setRooms(result.items);
    } catch {
      // silently fail on auto-refresh; user can pull to refresh
    }
  }, [token]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await fetchRooms();
    setRefreshing(false);
  }, [fetchRooms]);

  // Initial load + auto refresh every 3s
  useEffect(() => {
    fetchRooms();
    autoRefreshRef.current = setInterval(fetchRooms, 3000);
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current);
    };
  }, [fetchRooms]);

  const handleCreate = async () => {
    if (!token) return;
    setSubmitting(true);
    try {
      const room = await createRoom(token, createGame, roomName || null, roomPwd || null);
      setShowCreate(false);
      setRoomName('');
      setRoomPwd('');
      setCreateGame('gomoku');
      onNavigateToRoom(room.code);
    } catch (err) {
      notify('创建失败', (err as Error).message);
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
    if (!code) {
      notify('提示', '请输入房间号');
      return;
    }
    setSubmitting(true);
    try {
      const room = await joinRoom(token, code, joinPwd || null);
      setShowJoin(false);
      setJoinCode('');
      setJoinPwd('');
      onNavigateToRoom(room.code);
    } catch (err) {
      notify('加入失败', (err as Error).message);
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

      {rooms.length === 0 && refreshing ? (
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : (
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.primary} />}
        >
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
                  {room.has_password && <Ionicons name="lock-closed" size={14} color={colors.textFaint} />}
                </View>
              </TouchableOpacity>
            ))
          )}
        </ScrollView>
      )}

      {/* Create modal */}
      <Modal visible={showCreate} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>创建房间</Text>
            {/* Game type selection */}
            <Text style={styles.modalLabel}>游戏类型</Text>
            {/* 13 个游戏从目录表里生成（`games.tsx`）—— 以前这里是 13 段逐字相同的
                JSX（图标名 + 短名各手抄一遍），加一个游戏要改三处。 */}
            <View style={styles.gameTypeRow}>
              {GAMES.filter(g => g.key !== 'spider').map((g) => {
                const active = createGame === g.key;
                return (
                  <TouchableOpacity
                    key={g.key}
                    testID={`create-game-${g.key}`}
                    style={[styles.gameTypeBtn, active && styles.gameTypeBtnActive]}
                    onPress={() => setCreateGame(g.key)}
                  >
                    <Ionicons
                      name={g.ionicon}
                      size={16}
                      color={active ? colors.white : colors.textSecondary}
                    />
                    <Text style={[styles.gameTypeBtnText, active && styles.gameTypeBtnTextActive]}>
                      {g.short}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            {/* 游戏级设置（扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋尺寸 / UNO 房规）
                **一律不在建房时选**，房主进房之后在等待大厅里挑 —— v1.15 起统一走
                `room_setting` 一条消息，选项与当前值在 `room.settings` 里。
                建房只负责挑游戏类型。 */}
            <TextInput
              style={styles.modalInput}
              value={roomName}
              onChangeText={setRoomName}
              placeholder="房间名称（可选）"
              placeholderTextColor={colors.textFaint}
            />
            <TextInput
              style={styles.modalInput}
              value={roomPwd}
              onChangeText={setRoomPwd}
              placeholder="房间密码（可选）"
              placeholderTextColor={colors.textFaint}
              secureTextEntry
            />
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary]}
              onPress={handleCreate}
              disabled={submitting}
            >
              <Text style={styles.modalBtnPrimaryText}>{submitting ? '创建中...' : '创建'}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => { setShowCreate(false); setRoomName(''); setRoomPwd(''); setCreateGame('gomoku'); }}
            >
              <Text style={styles.modalBtnSecondaryText}>取消</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Join modal */}
      <Modal visible={showJoin} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>加入房间</Text>
            <TextInput
              style={styles.modalInput}
              value={joinCode}
              onChangeText={setJoinCode}
              placeholder="输入6位房间号"
              placeholderTextColor={colors.textFaint}
              autoCapitalize="characters"
              maxLength={6}
            />
            <TextInput
              style={styles.modalInput}
              value={joinPwd}
              onChangeText={setJoinPwd}
              placeholder="密码（如有）"
              placeholderTextColor={colors.textFaint}
              secureTextEntry
            />
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary]}
              onPress={handleJoin}
              disabled={submitting}
            >
              <Text style={styles.modalBtnPrimaryText}>{submitting ? '加入中...' : '加入'}</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => { setShowJoin(false); setJoinCode(''); setJoinPwd(''); }}
            >
              <Text style={styles.modalBtnSecondaryText}>取消</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
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
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
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
  // Modal styles
  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay, justifyContent: 'center', alignItems: 'center', padding: spacing.md,
  },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card, borderRadius: radius.xl, padding: spacing.lg,
  },
  modalTitle: {
    fontSize: fontSize.xl, fontWeight: '700', color: colors.text, textAlign: 'center', marginBottom: spacing.md,
  },
  modalInput: {
    borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm + 2,
    fontSize: fontSize.md, color: colors.text, backgroundColor: colors.bg, marginBottom: spacing.sm,
  },
  modalBtn: { borderRadius: radius.md, paddingVertical: spacing.sm + 4, alignItems: 'center', marginTop: spacing.sm },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
  modalLabel: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    fontWeight: '600',
    marginBottom: spacing.xs,
  },
  // 8 个游戏了，单行 flex:1 会把每个按钮挤到 ~38px（「五子棋」三个字放不下），
  // 改成 4 个一行换行排。
  gameTypeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginBottom: spacing.sm },
  gameTypeBtn: {
    flexGrow: 1,
    flexShrink: 1,
    flexBasis: '22%',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.bg,
  },
  gameTypeBtnActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  gameTypeBtnText: { fontSize: fontSize.md, color: colors.textSecondary, fontWeight: '600' },
  gameTypeBtnTextActive: { color: colors.white },
});

