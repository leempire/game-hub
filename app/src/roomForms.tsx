import React from 'react';
import {
  View, Text, TextInput, TouchableOpacity, ScrollView, Modal,
  KeyboardAvoidingView, Platform, StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, spacing, radius, fontSize } from './theme';
import { GAMES, type GameKey } from './games';

/**
 * 房间列表的两个表单弹窗（创建 / 加入）。
 *
 * 从 `RoomsTab.tsx` 拆出来：那一屏本来 400 出头，加上「三态 + 内联错误 +
 * 键盘可达 + 取消竞态」后涨到 560+，超过项目 500 行的软上限。这里只负责
 * **渲染**，所有状态与请求仍在 `RoomsTab`（弹窗不该知道 socket / REST）。
 */

interface CreateRoomModalProps {
  visible: boolean;
  game: GameKey;
  name: string;
  password: string;
  error: string | null;
  submitting: boolean;
  onPickGame: (game: GameKey) => void;
  onChangeName: (value: string) => void;
  onChangePassword: (value: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}

export function CreateRoomModal({
  visible, game, name, password, error, submitting,
  onPickGame, onChangeName, onChangePassword, onSubmit, onClose,
}: CreateRoomModalProps) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.modalOverlay}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <View style={styles.modalCard}>
          <ScrollView style={styles.modalScroll} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
            <Text style={styles.modalTitle}>创建房间</Text>
            <Text style={styles.modalLabel}>游戏类型</Text>
            {/* 13 个游戏从目录表里生成（`games.tsx`）—— 以前这里是 13 段逐字相同的
                JSX（图标名 + 短名各手抄一遍），加一个游戏要改三处。 */}
            <View style={styles.gameTypeRow}>
              {GAMES.map((g) => {
                const active = game === g.key;
                return (
                  <TouchableOpacity
                    key={g.key}
                    testID={`create-game-${g.key}`}
                    style={[styles.gameTypeBtn, active && styles.gameTypeBtnActive]}
                    onPress={() => onPickGame(g.key)}
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
                `room_setting` 一条消息，选项与当前值在 `room.settings` 里。 */}
            <TextInput
              style={styles.modalInput}
              value={name}
              onChangeText={onChangeName}
              placeholder="房间名称（可选）"
              maxLength={20}
              placeholderTextColor={colors.textFaint}
            />
            <TextInput
              style={styles.modalInput}
              value={password}
              onChangeText={onChangePassword}
              placeholder="房间密码（可选）"
              maxLength={32}
              placeholderTextColor={colors.textFaint}
              secureTextEntry
              returnKeyType="go"
              onSubmitEditing={onSubmit}
            />
            {error ? <Text style={styles.modalError} accessibilityRole="alert">{error}</Text> : null}
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary, submitting && styles.modalBtnDisabled]}
              onPress={onSubmit}
              disabled={submitting}
            >
              <Text style={styles.modalBtnPrimaryText}>{submitting ? '创建中...' : '创建'}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.modalBtn, styles.modalBtnSecondary]} onPress={onClose}>
              <Text style={styles.modalBtnSecondaryText}>取消</Text>
            </TouchableOpacity>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

interface JoinRoomModalProps {
  visible: boolean;
  code: string;
  password: string;
  error: string | null;
  submitting: boolean;
  onChangeCode: (value: string) => void;
  onChangePassword: (value: string) => void;
  onSubmit: () => void;
  onClose: () => void;
}

export function JoinRoomModal({
  visible, code, password, error, submitting,
  onChangeCode, onChangePassword, onSubmit, onClose,
}: JoinRoomModalProps) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.modalOverlay}
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
      >
        <View style={styles.modalCard}>
          <ScrollView style={styles.modalScroll} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
            <Text style={styles.modalTitle}>加入房间</Text>
            <TextInput
              style={styles.modalInput}
              value={code}
              onChangeText={onChangeCode}
              placeholder="输入6位房间号"
              placeholderTextColor={colors.textFaint}
              autoCapitalize="characters"
              maxLength={6}
              returnKeyType="next"
            />
            <TextInput
              style={styles.modalInput}
              value={password}
              onChangeText={onChangePassword}
              placeholder="密码（如有）"
              placeholderTextColor={colors.textFaint}
              secureTextEntry
              returnKeyType="go"
              onSubmitEditing={onSubmit}
            />
            {error ? <Text style={styles.modalError} accessibilityRole="alert">{error}</Text> : null}
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary, submitting && styles.modalBtnDisabled]}
              onPress={onSubmit}
              disabled={submitting}
            >
              <Text style={styles.modalBtnPrimaryText}>{submitting ? '加入中...' : '加入'}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[styles.modalBtn, styles.modalBtnSecondary]} onPress={onClose}>
              <Text style={styles.modalBtnSecondaryText}>取消</Text>
            </TouchableOpacity>
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay, justifyContent: 'center', alignItems: 'center', padding: spacing.md,
  },
  modalCard: {
    width: '100%', maxWidth: 360, maxHeight: '90%',
    backgroundColor: colors.card, borderRadius: radius.xl, padding: spacing.lg,
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
  modalBtnDisabled: { opacity: 0.5 },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
  modalScroll: { flexGrow: 0, flexShrink: 1 },
  modalError: {
    fontSize: fontSize.sm, color: colors.danger, textAlign: 'center',
    marginBottom: spacing.xs, lineHeight: 20,
  },
  modalLabel: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    fontWeight: '600',
    marginBottom: spacing.xs,
  },
  // 13 个游戏单行 flex:1 会把每个按钮挤到 ~38px（「五子棋」三个字放不下），
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
