// 深空休眠的**通用小部件**：可点容器、血条、资源胶囊、两个弹层外壳、两种按钮。
//
// 从 `StasisScreen.tsx` 拆出来的（那一屏原来 2700 行）。这些东西与深空休眠的规则
// 无关，只与这一屏的**深色底**有关，所以配色跟着 `stasisRules` 的两档文字色走。
import React from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, Platform, Animated, ScrollView,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { withAlpha, spacing, stasisTurret } from './theme';
import { useModalBackGuard } from './backGuard';
import { ON_SPACE, ON_SPACE_SOFT, clampRatio, doorTone } from './stasisRules';

/**
 * 按下即缩小的可点容器。**触摸没有 hover，「按到了没有」只能由按下态回答** ——
 * 这是 game feel 里最便宜也最有效的一招（`增强调研.md` §10.5、[67]）。
 *
 * `wrapStyle` 放外层（绝对定位之类的布局属性），`style` 放内层（外观）——
 * 缩放只加在内层，否则会把绝对定位的坐标一起缩掉。
 */
export function TapScale({ children, style, wrapStyle, onPress, testID }: {
  children: React.ReactNode;
  style?: any;
  wrapStyle?: any;
  onPress: () => void;
  testID?: string;
}) {
  const s = React.useRef(new Animated.Value(1)).current;
  const to = (v: number, d: number) => Animated.timing(s, {
    toValue: v, duration: d, useNativeDriver: Platform.OS !== 'web',
  }).start();
  return (
    <TouchableOpacity
      testID={testID}
      style={wrapStyle}
      activeOpacity={0.9}
      onPressIn={() => to(0.95, 80)}
      onPressOut={() => to(1, 120)}
      onPress={onPress}
    >
      <Animated.View style={[style, { transform: [{ scale: s }] }]}>{children}</Animated.View>
    </TouchableOpacity>
  );
}

/** 血量条。**条长是第二通道** —— 颜色不单独承载信息（WCAG 1.4.1）。 */
export function HpBar({ hp, max, height = 8, tone }: { hp: number; max: number; height?: number; tone?: string }) {
  const ratio = clampRatio(hp, max);
  const color = tone ?? doorTone(ratio);
  return (
    <View style={[styles.hpTrack, { height }]}>
      <View style={[styles.hpFill, { width: `${ratio * 100}%`, backgroundColor: color }]} />
    </View>
  );
}

/** 顶部资源胶囊（色点 + 数字 + 单位名，三个通道都在）。 */
export function ResChip({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <View style={styles.resChip}>
      <View style={[styles.resDot, { backgroundColor: color }]} />
      <Text style={styles.resValue}>{value}</Text>
      <Text style={styles.resLabel}>{label}</Text>
    </View>
  );
}

/**
 * 面板里的两种按钮。抽出来是因为**同一颗按钮长在三处**（底部 sheet、全站浮层、
 * 结算弹窗）—— 复制三份样式必然漂移（本项目「颜色只有一个来源」是同一条教训）。
 *
 * ⚠️ `dim` 只降透明度、**不是 `disabled`**：RNW 下 `disabled` 等于
 * `pointer-events:none`，点了既没反应也没提示，用户只会觉得「点了没反应」
 * （重力四子棋满列的教训）。买不起/门槛没过时一律让它照常可点、由回调给提示。
 */
export function PrimaryButton({ children, onPress, testID, dim, style }: {
  children: React.ReactNode; onPress: () => void; testID?: string; dim?: boolean; style?: any;
}) {
  return (
    <TouchableOpacity testID={testID} style={[styles.primaryBtn, dim && styles.btnDisabled, style]} onPress={onPress}>
      <Text style={styles.primaryBtnText}>{children}</Text>
    </TouchableOpacity>
  );
}

/** 次要动作（拆除 / 关闭 / 取消）：描边幽灵按钮，不与主按钮抢注意力。 */
export function GhostButton({ children, onPress, testID, style }: {
  children: React.ReactNode; onPress: () => void; testID?: string; style?: any;
}) {
  return (
    <TouchableOpacity testID={testID} style={[styles.ghostBtn, style]} onPress={onPress}>
      <Text style={styles.ghostBtnText}>{children}</Text>
    </TouchableOpacity>
  );
}

/** 居中弹窗外壳。**新弹窗一律接 `useModalBackGuard`**（返回键先关弹窗）。 */
export function ModalM({ visible, onClose, wide, children }: {
  visible: boolean; onClose: () => void; wide?: boolean; children: React.ReactNode;
}) {
  useModalBackGuard(visible, onClose);
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <View style={[styles.modalCard, wide && styles.modalCardWide]}>{children}</View>
      </View>
    </Modal>
  );
}

/** 底部抽屉。建造/升级面板用它 —— 内容可能比一屏高，所以 body 是可滚的。 */
export function Sheet({ visible, onClose, title, children }: {
  visible: boolean; onClose: () => void; title: string; children: React.ReactNode;
}) {
  useModalBackGuard(visible, onClose);
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.sheetBackdrop}>
        <TouchableOpacity style={styles.sheetDismiss} activeOpacity={1} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.sheetHead}>
            <Text style={styles.sheetTitle}>{title}</Text>
            <TouchableOpacity testID="stasis-sheet-close" onPress={onClose} hitSlop={12}>
              <Ionicons name="close" size={22} color={ON_SPACE} />
            </TouchableOpacity>
          </View>
          <ScrollView style={styles.sheetBody} contentContainerStyle={styles.sheetBodyInner}>
            {children}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  resChip: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  resDot: { width: 11, height: 11, borderRadius: 6 },
  resValue: { color: ON_SPACE, fontSize: 15, fontWeight: '700', fontVariant: ['tabular-nums'] },
  resLabel: { color: ON_SPACE_SOFT, fontSize: 10 },

  hpTrack: { width: '100%', borderRadius: 4, backgroundColor: withAlpha('#000', 0.55), overflow: 'hidden' },
  hpFill: { height: '100%' },

  primaryBtn: {
    paddingVertical: spacing.sm + 6, borderRadius: 10, alignItems: 'center',
    backgroundColor: withAlpha(stasisTurret, 0.18), borderWidth: 1, borderColor: stasisTurret,
  },
  primaryBtnText: { color: ON_SPACE, fontSize: 14, fontWeight: '700' },
  btnDisabled: { opacity: 0.5 },
  ghostBtn: {
    paddingVertical: spacing.sm + 4, borderRadius: 10, alignItems: 'center',
    borderWidth: 1, borderColor: '#2E4059', marginTop: 4,
  },
  ghostBtnText: { color: ON_SPACE, fontSize: 13, fontWeight: '600' },

  sheetBackdrop: { flex: 1, justifyContent: 'flex-end', backgroundColor: withAlpha('#000', 0.5) },
  sheetDismiss: { flex: 1 },
  sheet: { backgroundColor: '#101A29', borderTopLeftRadius: 16, borderTopRightRadius: 16, maxHeight: '70%' },
  sheetHead: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#1B2739',
  },
  sheetTitle: { color: ON_SPACE, fontSize: 15, fontWeight: '700' },
  sheetBody: { maxHeight: 420 },
  sheetBodyInner: { padding: 16, gap: 10 },

  modalBackdrop: { flex: 1, backgroundColor: withAlpha('#000', 0.62), alignItems: 'center', justifyContent: 'center', padding: 20 },
  modalCard: { width: '100%', maxWidth: 360, backgroundColor: '#101A29', borderRadius: 16, padding: 18, gap: 10 },
  modalCardWide: { maxWidth: 420 },
});
