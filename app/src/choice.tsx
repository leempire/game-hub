/** 通用「点一下弹窗选一个值」的字段与弹窗。
 *
 * 游戏有 15 款，建房 / 排行榜如果全铺成一排排芯片，一屏全是按钮还找不到目标。
 * 这里把「当前值」收成一行字段，点开才显示候选；候选按分类分组。建房弹窗是
 * 嵌套在 Modal 里的，所以那一处用内联展开的 `GameChoiceGrid`（不叠第二层 Modal）。
 */
import React from 'react';
import {
  StyleSheet, Text, TouchableOpacity, View,
} from 'react-native';
import type { StyleProp, ViewStyle } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { AppModal } from './ui';
import { GAMES, GAME_CATEGORIES, type GameKey } from './games';
import { colors, fontSize, radius, spacing } from './theme';

export interface ChoiceOption<K extends string | number = string> {
  key: K;
  label: string;
  hint?: string;
}

export interface ChoiceGroup<K extends string | number = string> {
  title?: string;
  options: ChoiceOption<K>[];
}

/** 一行字段：标签 + 当前值 + 向下箭头。整行都是 44pt 以上的触控目标。 */
export function ChoiceField({ label, value, onPress, testID, style }: {
  label: string;
  value: string;
  onPress: () => void;
  testID?: string;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <TouchableOpacity
      testID={testID}
      style={[styles.field, style]}
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${label}，当前${value}，点击更改`}
    >
      <Text style={styles.fieldLabel}>{label}</Text>
      <Text style={styles.fieldValue} numberOfLines={1}>{value}</Text>
      <Ionicons name="chevron-down" size={16} color={colors.textMuted} />
    </TouchableOpacity>
  );
}

/** 分组候选弹窗。`closeOnPick=false` 时用于「一次改多项」的筛选面板。 */
export function ChoiceModal<K extends string | number>({ visible, title, hint, groups, value, activeKeys, onPick, onClose, closeOnPick = true, testID }: {
  visible: boolean;
  title: string;
  hint?: string;
  groups: ChoiceGroup<K>[];
  value?: K;
  /** 多组共用一个弹窗时，用这组键标出各组的当前值（不传则只比 `value`）。 */
  activeKeys?: K[];
  onPick: (key: K) => void;
  onClose: () => void;
  closeOnPick?: boolean;
  testID?: string;
}) {
  return (
    <AppModal visible={visible} onClose={onClose} testID={testID}>
      <Text style={styles.title}>{title}</Text>
      {hint ? <Text style={styles.hint}>{hint}</Text> : null}
      {groups.map((group, index) => (
        <View key={group.title ?? `group-${index}`} style={styles.group}>
          {group.title ? <Text style={styles.groupTitle}>{group.title}</Text> : null}
          <View style={styles.options}>
            {group.options.map((option) => {
              const active = activeKeys ? activeKeys.includes(option.key) : option.key === value;
              return (
                <TouchableOpacity
                  key={String(option.key)}
                  testID={`choice-${String(option.key)}`}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  style={[styles.option, active && styles.optionActive]}
                  onPress={() => {
                    onPick(option.key);
                    if (closeOnPick) onClose();
                  }}
                >
                  <Text style={[styles.optionText, active && styles.optionTextActive]}>
                    {active ? '✓ ' : ''}{option.label}
                  </Text>
                  {option.hint ? (
                    <Text style={[styles.optionHint, active && styles.optionHintActive]}>{option.hint}</Text>
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </View>
        </View>
      ))}
      <TouchableOpacity style={styles.close} onPress={onClose} accessibilityRole="button">
        <Text style={styles.closeText}>完成</Text>
      </TouchableOpacity>
    </AppModal>
  );
}

/** 游戏候选（弹窗版，按分类分组）。 */
export function GameChoiceModal({ visible, value, onPick, onClose }: {
  visible: boolean;
  value: GameKey;
  onPick: (key: GameKey) => void;
  onClose: () => void;
}) {
  return (
    <AppModal visible={visible} onClose={onClose} testID="game-choice-modal">
      <Text style={styles.title}>选择游戏</Text>
      {GAME_CATEGORIES.map((category) => (
        <View key={category.key} style={styles.group}>
          <Text style={styles.groupTitle}>{category.name}</Text>
          <View style={styles.options}>
            {GAMES.filter((game) => game.category === category.key).map((game) => {
              const active = game.key === value;
              return (
                <TouchableOpacity
                  key={game.key}
                  testID={`choose-game-${game.key}`}
                  accessibilityRole="button"
                  accessibilityState={{ selected: active }}
                  style={[styles.gameOption, active && styles.optionActive]}
                  onPress={() => { onPick(game.key); onClose(); }}
                >
                  <Ionicons name={game.ionicon} size={18}
                    color={active ? colors.white : colors.textSecondary} />
                  <Text style={[styles.optionText, active && styles.optionTextActive]}>{game.short}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>
      ))}
      <TouchableOpacity style={styles.close} onPress={onClose} accessibilityRole="button">
        <Text style={styles.closeText}>取消</Text>
      </TouchableOpacity>
    </AppModal>
  );
}

/** 游戏候选（内联版，建房弹窗里展开用；不叠第二层 Modal）。 */
export function GameChoiceGrid({ value, onPick }: {
  value: GameKey;
  onPick: (key: GameKey) => void;
}) {
  return (
    <View style={styles.grid}>
      {GAME_CATEGORIES.map((category) => {
        const games = GAMES.filter((game) => game.category === category.key);
        if (!games.length) return null;
        return (
          <View key={category.key} style={styles.group}>
            <Text style={styles.groupTitle}>{category.name}</Text>
            <View style={styles.options}>
              {games.map((game) => {
                const active = game.key === value;
                return (
                  <TouchableOpacity
                    key={game.key}
                    testID={`create-game-${game.key}`}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    style={[styles.gameOption, active && styles.optionActive]}
                    onPress={() => onPick(game.key)}
                  >
                    <Ionicons name={game.ionicon} size={18}
                      color={active ? colors.white : colors.textSecondary} />
                    <Text style={[styles.optionText, active && styles.optionTextActive]}>{game.short}</Text>
                  </TouchableOpacity>
                );
              })}
            </View>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  field: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.card,
  },
  fieldLabel: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  fieldValue: { flex: 1, minWidth: 0, textAlign: 'right', color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  title: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, textAlign: 'center' },
  hint: { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 20, textAlign: 'center' },
  group: { gap: spacing.xs },
  groupTitle: { color: colors.textSecondary, fontSize: fontSize.xs, fontWeight: '700' },
  options: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs },
  option: {
    flexGrow: 1,
    flexShrink: 1,
    flexBasis: '30%',
    minHeight: 44,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    justifyContent: 'center',
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.bg,
  },
  optionActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  optionText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  optionTextActive: { color: colors.white },
  optionHint: { color: colors.textMuted, fontSize: fontSize.xs },
  optionHintActive: { color: colors.white, opacity: 0.85 },
  gameOption: {
    flexGrow: 1,
    flexShrink: 1,
    flexBasis: '30%',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    minHeight: 44,
    paddingHorizontal: spacing.xs,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.bg,
  },
  grid: { gap: spacing.sm },
  close: {
    minHeight: 48,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.md,
    backgroundColor: colors.bg,
  },
  closeText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});