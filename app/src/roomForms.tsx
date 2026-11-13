import React, { useEffect, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, spacing, radius, fontSize } from './theme';
import { AppModal, FormInput } from './ui';
import { GAMES, type GameKey } from './games';
import { GameChoiceGrid } from './choice';

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
  // 15 款游戏全铺开是 4 排芯片、把弹窗撑满还很难找。收成一行「当前游戏」，
  // 点开才按分类展开候选；选完自动收起。
  const [pickerOpen, setPickerOpen] = useState(false);
  useEffect(() => {
    if (!visible) setPickerOpen(false);
  }, [visible]);
  const selected = GAMES.find((item) => item.key === game);
  return (
    <AppModal visible={visible} onClose={onClose}>
      <Text style={styles.modalTitle}>创建房间</Text>
      <Text style={styles.modalLabel}>游戏类型</Text>
      <TouchableOpacity
        testID="create-game-picker"
        style={styles.gameField}
        onPress={() => setPickerOpen((value) => !value)}
        accessibilityRole="button"
        accessibilityState={{ expanded: pickerOpen }}
        accessibilityLabel={`游戏类型，当前${selected?.short ?? game}，点击${pickerOpen ? '收起' : '展开'}候选`}
      >
        <Ionicons name={selected?.ionicon ?? 'ellipse'} size={20} color={colors.primary} />
        <Text style={styles.gameFieldValue}>{selected?.short ?? game}</Text>
        <Text style={styles.gameFieldHint}>{pickerOpen ? '收起' : '更改'}</Text>
        <Ionicons name={pickerOpen ? 'chevron-up' : 'chevron-down'} size={16} color={colors.textMuted} />
      </TouchableOpacity>
      {pickerOpen ? (
        <GameChoiceGrid
          value={game}
          onPick={(key) => { onPickGame(key); setPickerOpen(false); }}
        />
      ) : null}
      {/* 游戏级设置（扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋尺寸 / UNO 房规）
          **一律不在建房时选**，房主进房之后在等待大厅里挑 —— v1.15 起统一走
          `room_setting` 一条消息，选项与当前值在 `room.settings` 里。 */}
      <FormInput
        style={styles.modalInput}
        label="房间名称"
        value={name}
        onChangeText={onChangeName}
        placeholder="房间名称（可选）"
        maxLength={20}
        placeholderTextColor={colors.textFaint}
      />
      <FormInput
        style={styles.modalInput}
        label="房间密码"
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
    </AppModal>
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
    <AppModal visible={visible} onClose={onClose}>
      <Text style={styles.modalTitle}>加入房间</Text>
      <FormInput
        style={styles.modalInput}
        label="房间号"
        value={code}
        onChangeText={onChangeCode}
        placeholder="输入6位房间号"
        placeholderTextColor={colors.textFaint}
        autoCapitalize="characters"
        maxLength={6}
        returnKeyType="next"
      />
      <FormInput
        style={styles.modalInput}
        label="房间密码"
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
    </AppModal>
  );
}

const styles = StyleSheet.create({
  modalTitle: {
    fontSize: fontSize.xl, fontWeight: '700', color: colors.text, textAlign: 'center', marginBottom: spacing.md,
  },
  modalInput: {
    borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm + 2,
    fontSize: fontSize.md, color: colors.text, backgroundColor: colors.bg, marginBottom: spacing.sm,
  },
  modalBtn: { borderRadius: radius.md, minHeight: 48, paddingVertical: spacing.sm + 6, alignItems: 'center', marginTop: spacing.sm },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnDisabled: { opacity: 0.5 },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
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
  gameField: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.bg,
  },
  gameFieldValue: { flex: 1, minWidth: 0, fontSize: fontSize.md, color: colors.text, fontWeight: '600' },
  gameFieldHint: { fontSize: fontSize.xs, color: colors.textMuted },
});
