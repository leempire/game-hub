import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, ScrollView, Modal, TextInput } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { createRoom, joinRoom } from '../api';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';
import { useModalBackGuard } from '../backGuard';
import { GameHelpButton } from '../help';
// 游戏目录（名字 / 说明 / 徽标 / 分类 / 图标）的唯一来源。
import { GAMES, GAME_CATEGORIES, type GameEntry } from '../games';

interface Props {
  onNavigateToRooms: () => void;
  onNavigateToRoom: (code: string) => void;
}

export default function GamesTab({ onNavigateToRooms, onNavigateToRoom }: Props) {
  const { token } = useAuth();
  // 当前打开的是哪个游戏的「创建 / 加入」弹窗（null = 关着）。
  // 13 个布尔 state + 13 个弹窗收成一个 —— 它们本来就逐字相同。
  const [picked, setPicked] = useState<GameEntry | null>(null);
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [joinCode, setJoinCode] = useState('');
  const [joinPwd, setJoinPwd] = useState('');
  const [creating, setCreating] = useState(false);
  const [joining, setJoining] = useState(false);
  useModalBackGuard(picked !== null, () => setPicked(null));
  useModalBackGuard(showJoinModal, () => setShowJoinModal(false));

  const handleCreateRoom = async () => {
    if (!token || !picked) return;
    setCreating(true);
    try {
      // 游戏级设置（扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋尺寸 / UNO 房规）
      // **一律不在建房时选**，房主进房之后在等待大厅里挑（v1.15 起统一走
      // `room_setting` 一条消息）。建房只负责挑游戏类型。
      const room = await createRoom(token, picked.key);
      setPicked(null);
      onNavigateToRoom(room.code);
    } catch (err) {
      notify('创建失败', (err as Error).message);
    } finally {
      setCreating(false);
    }
  };

  const handleJoinRoom = async () => {
    if (!token) return;
    const code = joinCode.trim().toUpperCase();
    if (!code) {
      notify('提示', '请输入房间号');
      return;
    }
    setJoining(true);
    try {
      const room = await joinRoom(token, code, joinPwd || null);
      setShowJoinModal(false);
      setJoinCode('');
      setJoinPwd('');
      onNavigateToRoom(room.code);
    } catch (err) {
      notify('加入失败', (err as Error).message);
    } finally {
      setJoining(false);
    }
  };

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.sectionTitle}>选择游戏</Text>

        {GAME_CATEGORIES.map((cat) => {
          const games = GAMES.filter((g) => g.category === cat.key);
          if (games.length === 0) return null;
          return (
            <View key={cat.key} style={styles.category}>
              <View style={styles.categoryHead}>
                <Text style={styles.categoryName}>{cat.name}</Text>
                <Text style={styles.categoryBlurb}>{cat.blurb}</Text>
                <Text style={styles.categoryCount}>{games.length}</Text>
              </View>
              {games.map((g) => (
                <TouchableOpacity
                  key={g.key}
                  testID={`game-card-${g.key}`}
                  style={styles.gameCard}
                  onPress={() => setPicked(g)}
                  activeOpacity={0.8}
                >
                  <View style={styles.cardIconWrap}>
                    <g.Icon size={44} />
                  </View>
                  <View style={styles.cardInfo}>
                    <Text style={styles.cardTitle}>{g.title}</Text>
                    <Text style={styles.cardDesc}>{g.desc}</Text>
                    <View style={styles.cardBadge}>
                      <Text style={styles.cardBadgeText}>{g.badge}</Text>
                    </View>
                  </View>
                  <View style={styles.cardHelpCol}>
                    <GameHelpButton game={g.key} />
                  </View>
                </TouchableOpacity>
              ))}
            </View>
          );
        })}

        {/* Quick actions */}
        <View style={styles.actions}>
          <TouchableOpacity style={styles.actionBtn} onPress={onNavigateToRooms}>
            <Ionicons name="home" size={32} color={colors.primary} />
            <Text style={styles.actionBtnText}>浏览房间</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.actionBtn} onPress={() => setShowJoinModal(true)}>
            <Ionicons name="key" size={32} color={colors.primary} />
            <Text style={styles.actionBtnText}>加入房间</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>

      {/* 建房 / 加入（13 个游戏共用这一个弹窗） */}
      <Modal visible={picked !== null} transparent animationType="fade">
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>{picked?.title}</Text>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary]}
              onPress={handleCreateRoom}
              disabled={creating}
            >
              <Text style={styles.modalBtnPrimaryText}>
                {creating ? '创建中...' : '创建房间'}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.modalBtn}
              onPress={() => {
                setPicked(null);
                setShowJoinModal(true);
              }}
            >
              <Text style={styles.modalBtnText}>加入房间</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setPicked(null)}
            >
              <Text style={styles.modalBtnSecondaryText}>取消</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Join room modal */}
      <Modal visible={showJoinModal} transparent animationType="fade">
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
              onPress={handleJoinRoom}
              disabled={joining}
            >
              <Text style={styles.modalBtnPrimaryText}>
                {joining ? '加入中...' : '加入'}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => {
                setShowJoinModal(false);
                setJoinCode('');
                setJoinPwd('');
              }}
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
  scroll: { padding: spacing.md, paddingBottom: spacing.xxl },
  sectionTitle: {
    fontSize: fontSize.lg,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.sm,
  },
  // 分类：标题行 + 一句玩法说明 + 该类的游戏数（数是给「这一类有多大」一个量感）
  category: { marginBottom: spacing.md },
  categoryHead: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginBottom: spacing.xs,
    paddingHorizontal: spacing.xs,
  },
  categoryName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  categoryBlurb: { flex: 1, fontSize: fontSize.xs, color: colors.textMuted },
  categoryCount: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    minWidth: 18,
    textAlign: 'center',
    borderRadius: radius.full,
    backgroundColor: colors.card,
    paddingVertical: 1,
  },
  gameCard: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: colors.card,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  gameCardDisabled: { opacity: 0.6 },
  cardIconWrap: {
    width: 56,
    height: 56,
    borderRadius: radius.md,
    backgroundColor: colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: spacing.md,
  },
  cardInfo: { flex: 1, justifyContent: 'center' },
  cardHelpCol: {
    marginLeft: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cardTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  cardDesc: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  cardBadge: {
    alignSelf: 'flex-start',
    marginTop: spacing.xs,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.full,
    backgroundColor: colors.primary,
  },
  cardBadgeComingSoon: { backgroundColor: colors.textMuted },
  cardBadgeText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
  actions: {
    flexDirection: 'row',
    gap: spacing.md,
    marginTop: spacing.md,
  },
  actionBtn: {
    flex: 1,
    backgroundColor: colors.card,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: colors.border,
  },
  actionBtnText: { fontSize: fontSize.sm, color: colors.text, marginTop: spacing.xs },
  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  modalCard: {
    width: '100%',
    maxWidth: 340,
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.lg,
  },
  modalTitle: {
    fontSize: fontSize.lg,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.md,
    textAlign: 'center',
  },
  modalInput: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSize.md,
    color: colors.text,
    marginBottom: spacing.sm,
  },
  modalBtn: {
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    marginTop: spacing.sm,
  },
  modalBtnText: { fontSize: fontSize.md, color: colors.text, fontWeight: '600' },
  modalBtnPrimary: { backgroundColor: colors.primary, borderColor: colors.primary },
  modalBtnPrimaryText: { fontSize: fontSize.md, color: colors.white, fontWeight: '700' },
  modalBtnSecondary: { borderColor: 'transparent' },
  modalBtnSecondaryText: { fontSize: fontSize.md, color: colors.textMuted },
});
