/**
 * **房间级设置的统一界面**（v1.15）：房间页一行摘要 + 从底部升起的设置抽屉。
 *
 * 在这之前，五处设置（扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋棋盘 / UNO 房规）
 * 是各写各的芯片循环，光 `RoomScreen` 里就占了约 199 行；UNO 的 3 个档位 + 7 条房规
 * 一铺开，整页就很难看（用户报的就是它）。现在房间页只剩一行
 * 「玩法设置 + 当前摘要 ›」，点开是抽屉。
 *
 * **本文件不认识任何 `key` 的语义**：档位名、每条设置的中文名与说明、当前值的中文名
 * 全由服务端下发（`room.settings`，见 `server/app/room_settings.py`），这里只按
 * `kind` 画控件。加一条房规，这个文件一个字都不用改 —— 同象棋的 `legal`、
 * 飞行棋的 `path`、俄罗斯方块的 `shapes` 那条纪律。
 */

import React from 'react';
import { Modal, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import type { Room, RoomSetting } from './api';
import { colors, fontSize, radius, spacing } from './theme';
import { useModalBackGuard } from './backGuard';
import { boldSegments } from './richText';

/**
 * 抽屉里真正要显示的项。
 *
 * `visible_when` 由**客户端求值**：另一项的值就在同一个数组里，一次遍历就能算；
 * 服务端若先过滤掉，房间页那行摘要还得再过滤一遍，两边口径容易漂。
 *
 * 军棋的「阵亡公开身份」就靠它 —— 明棋下点那个开关界面上什么都不会变
 * （服务端只在暗棋下读它），正是本项目反复栽的「点了没反应」。
 */
export function visibleSettings(items: RoomSetting[]): RoomSetting[] {
  const byKey = new Map(items.map((i) => [i.key, i]));
  return items.filter((item) => {
    if (!item.visible_when) return true;
    return byKey.get(item.visible_when.key)?.value === item.visible_when.value;
  });
}

/**
 * 当前取值的说明。**当前值能在 `choices` 里找到就用那一条的 blurb**，否则用兜底那句
 * —— UNO 的「自定义」正好落在这条兜底上（它不是一个可点的档位，只是「当前这套不
 * 完全是任何一个预设」这个状态）。
 */
export function settingBlurb(item: RoomSetting): string {
  return item.choices?.find((c) => c.value === item.value)?.blurb ?? item.blurb;
}

/**
 * 房间页那一行的摘要：只取 `in_summary` 的项，最多两项。
 *
 * UNO 有 8 项，全拼出来能占满一整行 —— 所以摘要由**服务端**标出哪几项算数
 * （UNO 只标档位，军棋标「玩法 + 阵亡公开」）。
 */
export function settingsSummary(items: RoomSetting[]): string {
  const shown = visibleSettings(items);
  const marked = shown.filter((i) => i.in_summary);
  return (marked.length ? marked : shown)
    .slice(0, 2)
    .map((i) => i.value_label)
    .filter(Boolean)
    .join(' · ');
}

/** 某条设置当前值的中文名（房间列表徽标那种只想显示一个词的地方用）。 */
export function settingValueLabel(
  room: Room | null | undefined, key: string,
): string | null {
  return room?.settings?.find((i) => i.key === key)?.value_label ?? null;
}

/** 房间页那一行。**永远可点** —— 非房主也要能看当前设置，只是抽屉里不能改。 */
export function RoomSettingsRow({ items, onOpen }: {
  items: RoomSetting[];
  onOpen: () => void;
}) {
  const summary = settingsSummary(items);
  return (
    <TouchableOpacity
      style={styles.row}
      onPress={onOpen}
      testID="room-settings-row"
      accessibilityLabel={`玩法设置，当前 ${summary}`}
    >
      <Text style={styles.rowLabel}>玩法设置</Text>
      <Text style={styles.rowValue} numberOfLines={1} ellipsizeMode="tail">
        {summary}
      </Text>
      <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
    </TouchableOpacity>
  );
}

/** 抽屉里的一个选项芯片。 */
function Chip({ testID, label, active, onPress }: {
  testID: string; label: string; active: boolean; onPress: () => void;
}) {
  return (
    <TouchableOpacity
      testID={testID}
      style={[styles.chip, active && styles.chipActive]}
      onPress={onPress}
      accessibilityLabel={`${label}${active ? '（当前）' : ''}`}
      accessibilityState={{ selected: active }}
    >
      {/* **激活态不能只靠颜色**（WCAG 1.4.1）：底色 + 白字 + 一枚勾 + 加粗，
          四条通道一起说「这就是现在生效的那个」。 */}
      {active ? <Ionicons name="checkmark" size={16} color={colors.white} /> : null}
      <Text style={[styles.chipText, active && styles.chipTextActive]}>{label}</Text>
    </TouchableOpacity>
  );
}

/** 一排芯片。开关也是两枚（`[开, 关]` 由服务端给，军棋那条叫「公开 / 不公开」）。 */
function Control({ item, onPick }: {
  item: RoomSetting;
  onPick: (key: string, value: string | number | boolean) => void;
}) {
  if (item.kind === 'toggle') {
    const [onLabel, offLabel] = item.toggle_labels ?? ['开', '关'];
    return (
      <View style={styles.chips}>
        <Chip testID={`room-settings-${item.key}-on`} label={onLabel}
          active={item.value === true} onPress={() => onPick(item.key, true)} />
        <Chip testID={`room-settings-${item.key}-off`} label={offLabel}
          active={item.value === false} onPress={() => onPick(item.key, false)} />
      </View>
    );
  }
  return (
    <View style={styles.chips}>
      {(item.choices ?? []).map((c) => (
        <Chip
          key={String(c.value)}
          testID={`room-settings-${item.key}-${c.value}`}
          label={c.label}
          active={c.value === item.value}
          onPress={() => onPick(item.key, c.value)}
        />
      ))}
    </View>
  );
}

/**
 * 设置抽屉。
 *
 * `editable` 为假（非房主 / 对局中）时**整排芯片都不渲染**，只留当前值与说明 ——
 * 不是渲染一个点不动的按钮。RNW 下 `disabled` 等于 `pointer-events:none`，
 * 点了既没反应也没提示，用户只会觉得「点了没反应」（重力四子棋满列的教训）。
 *
 * 不做本地草稿、没有保存按钮：点一下立即发，服务端广播 `lobby` 回来刷新 ——
 * 界面显示的就是真实生效的那一份，不藏状态。
 */
export function RoomSettingsSheet({ visible, items, editable, onClose, onPick }: {
  visible: boolean;
  items: RoomSetting[];
  editable: boolean;
  onClose: () => void;
  onPick: (key: string, value: string | number | boolean) => void;
}) {
  // 返回键先关抽屉。**由外壳自己接**，与 `stasisWidgets` 的 `ModalM` / `Sheet` 同一套
  // （调用方在提前 return 之后才渲染它，把 hook 放在那边要额外小心顺序）。
  useModalBackGuard(visible, onClose);
  const shown = visibleSettings(items);
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <TouchableOpacity
          style={styles.backdropTap}
          activeOpacity={1}
          onPress={onClose}
          accessibilityLabel="关闭玩法设置"
        />
        <View style={styles.sheet} testID="room-settings-sheet">
          <View style={styles.head}>
            <Text style={styles.title}>玩法设置</Text>
            <TouchableOpacity
              testID="room-settings-close"
              onPress={onClose}
              hitSlop={12}
              accessibilityLabel="关闭"
            >
              <Ionicons name="close" size={24} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>
          {editable ? null : (
            <Text style={styles.hint}>
              只有房主能改，对局中也改不了。下面是这一局当前生效的设置。
            </Text>
          )}
          <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent}>
            {shown.map((item) => (
              <View key={item.key} style={styles.block}>
                <View style={styles.blockHead}>
                  <Text style={styles.blockLabel}>{item.label}</Text>
                  <Text style={styles.blockValue}>{item.value_label}</Text>
                </View>
                {/* blurb 里带 `**强调**`（房规说明一直这么写），
                    原样渲染会把星号显示给玩家看 —— 同 help.tsx 那条教训 */}
                <Text style={styles.blockBlurb}>
                  {boldSegments(settingBlurb(item), styles.blockBlurbBold)}
                </Text>
                {editable ? <Control item={item} onPick={onPick} /> : null}
              </View>
            ))}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
    // 整行都是热区，高度按主控件标准（≥44pt）
    minHeight: 48,
    paddingVertical: spacing.sm,
  },
  rowLabel: { color: colors.textSecondary, fontSize: fontSize.md },
  rowValue: {
    flex: 1,
    textAlign: 'right',
    color: colors.text,
    fontSize: fontSize.md,
    fontWeight: '600',
  },

  backdrop: { flex: 1, backgroundColor: colors.overlay, justifyContent: 'flex-end' },
  backdropTap: { flex: 1 },
  sheet: {
    backgroundColor: colors.card,
    borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg,
    paddingHorizontal: spacing.md,
    paddingBottom: spacing.lg,
    // UNO 有 8 项、说明也长，一定超一屏 —— 抽屉体自己滚
    maxHeight: '80%',
  },
  head: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.md,
  },
  title: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  hint: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    marginBottom: spacing.sm,
  },
  body: { flexGrow: 0 },
  bodyContent: { paddingBottom: spacing.sm },

  block: { paddingVertical: spacing.sm, gap: spacing.xs },
  blockHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  blockLabel: { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  blockValue: { fontSize: fontSize.sm, color: colors.textSecondary },
  blockBlurbBold: { fontWeight: '700' },
  blockBlurb: { fontSize: fontSize.xs, color: colors.textMuted },

  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    // 触控目标 ≥44pt（HIG / WCAG 2.5.5）——旧的 mapChip 只有 ~31px
    minHeight: 44,
    minWidth: 64,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    // `colors.border` 只有 1.24:1，只配当分隔线；控件的可见边界要用 borderStrong（3.19:1）
    borderColor: colors.borderStrong,
    backgroundColor: colors.card,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  chipTextActive: { color: colors.white, fontWeight: '800' },
});
