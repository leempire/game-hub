/**
 * 深渊回声的局外天赋树（v1.29）：房间页一行入口 + 底部抽屉。
 *
 * **树的内容、等级、花费与发点规则全部由服务端下发**（`GET /api/talents`，
 * 见 `server/app/talents.py`）。这里只负责画：加一条天赋不需要改这个文件。
 *
 * 交互约束（与项目其他弹窗一致）：
 * - 免费重置要二次确认；重置按钮不是破坏性红色大按钮，但必须说清「会清空」；
 * - 点数不足 / 已满级**不置灰**（RNW 的 disabled 等于 pointer-events:none，
 *   点了连提示都没有），按下去用 toast 说清楚；
 * - 抽屉自己接 `useModalBackGuard`，返回键只关这一层。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator, Modal, ScrollView, StyleSheet, Text, TouchableOpacity, View,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import {
  allocateTalent, getTalents, resetTalents,
  type TalentNode, type TalentProfile,
} from './api';
import { colors, fontSize, radius, spacing } from './theme';
import { useModalBackGuard } from './backGuard';
import { notify } from './utils';

const BRANCH_ACCENT: Record<string, string> = {
  endurance: colors.success,
  firepower: colors.danger,
  salvage: colors.warning,
};

/** 房间页的入口行：显示未花点数，点开抽屉。 */
export function TalentTreeRow({ token, refreshKey, onOpen }: {
  token: string | null;
  /** 局数 / 加点的变化计数，变了就重新拉一次余额。 */
  refreshKey?: number;
  onOpen: () => void;
}) {
  const [points, setPoints] = useState<number | null>(null);
  useEffect(() => {
    if (!token) {
      setPoints(null);
      return;
    }
    let alive = true;
    getTalents(token)
      .then((profile) => { if (alive) setPoints(profile.points); })
      .catch(() => { if (alive) setPoints(null); });
    return () => { alive = false; };
  }, [token, refreshKey]);

  const badge = points !== null && points > 0 ? `${points} 点可用` : '查看';
  return (
    <TouchableOpacity
      testID="talent-tree-row"
      style={styles.row}
      onPress={onOpen}
      accessibilityRole="button"
      accessibilityLabel={`潜水员天赋，${badge}`}
    >
      <View style={styles.rowIcon}>
        <Ionicons name="git-branch-outline" size={20} color={colors.primary} />
      </View>
      <View style={styles.rowText}>
        <Text style={styles.rowTitle}>潜水员天赋</Text>
        <Text style={styles.rowHint}>每局结算获得天赋点 · 可免费重置</Text>
      </View>
      <Text style={points !== null && points > 0 ? styles.rowBadge : styles.rowValue}>
        {badge}
      </Text>
      <Ionicons name="chevron-forward" size={18} color={colors.textMuted} />
    </TouchableOpacity>
  );
}

function Pips({ rank, max }: { rank: number; max: number }) {
  return (
    <View style={styles.pips} accessibilityLabel={`已点 ${rank} 级，共 ${max} 级`}>
      {Array.from({ length: max }, (_, index) => (
        <View key={index} style={[styles.pip, index < rank && styles.pipOn]} />
      ))}
    </View>
  );
}

function NodeRow({ node, busy, onAdd }: {
  node: TalentNode;
  busy: boolean;
  onAdd: (node: TalentNode) => void;
}) {
  const maxed = node.rank >= node.max;
  return (
    <View style={styles.node} testID={`talent-node-${node.key}`}>
      <View style={styles.nodeMain}>
        <View style={styles.nodeHead}>
          <Text style={styles.nodeName}>{node.name}</Text>
          <Text style={styles.nodeRank}>
            {maxed ? '已满级' : `Lv.${node.rank}/${node.max}`}
          </Text>
        </View>
        <Text style={styles.nodeDetail}>{node.detail}</Text>
        <View style={styles.nodeFoot}>
          <Pips rank={node.rank} max={node.max} />
          <Text style={styles.nodeCost}>
            {maxed ? '完成' : `${node.cost} 点${node.max > 1 ? '/级' : ''}`}
          </Text>
        </View>
      </View>
      <TouchableOpacity
        testID={`talent-add-${node.key}`}
        style={[styles.add, maxed && styles.addDone]}
        onPress={() => onAdd(node)}
        accessibilityRole="button"
        accessibilityLabel={maxed ? `${node.name}已满级` : `加一点${node.name}`}
        accessibilityState={{ busy }}
      >
        <Ionicons
          name={maxed ? 'checkmark' : 'add'}
          size={22}
          color={colors.white}
        />
      </TouchableOpacity>
    </View>
  );
}

/** 天赋抽屉。`onChange` 用于让房间页的入口行同步余额。 */
export function TalentTreeSheet({ visible, token, onClose, onChange }: {
  visible: boolean;
  token: string | null;
  onClose: () => void;
  onChange?: (profile: TalentProfile) => void;
}) {
  useModalBackGuard(visible, onClose);
  const [profile, setProfile] = useState<TalentProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  useModalBackGuard(resetOpen, () => setResetOpen(false));

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const next = await getTalents(token);
      setProfile(next);
      onChange?.(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token, onChange]);

  useEffect(() => {
    if (visible) void load();
  }, [visible, load]);

  const add = async (node: TalentNode) => {
    if (!token || !profile || busy) return;
    if (node.rank >= node.max) {
      notify('已满级', `${node.name}已经点到上限。`);
      return;
    }
    if (profile.points < node.cost) {
      notify('天赋点不足', `${node.name}还需要 ${node.cost - profile.points} 点，打完一局再来。`);
      return;
    }
    setBusy(true);
    try {
      const next = await allocateTalent(token, node.key);
      setProfile(next);
      onChange?.(next);
    } catch (err) {
      notify('加点失败', (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!token || busy) return;
    setBusy(true);
    try {
      const next = await resetTalents(token);
      setProfile(next);
      onChange?.(next);
      setResetOpen(false);
      notify('已重置', `返还了 ${profile?.spent ?? 0} 点天赋点。`);
    } catch (err) {
      notify('重置失败', (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <TouchableOpacity
          style={styles.backdropTap}
          activeOpacity={1}
          onPress={onClose}
          accessibilityLabel="关闭天赋树"
        />
        <View style={styles.sheet} testID="talent-sheet">
          <View style={styles.head}>
            <View style={styles.headText}>
              <Text style={styles.title}>潜水员天赋</Text>
              <Text style={styles.subtitle}>局外成长 · 只影响下一局深渊回声</Text>
            </View>
            {profile && (
              <View style={styles.pointsPill} testID="talent-points">
                <Text style={styles.pointsValue}>{profile.points}</Text>
                <Text style={styles.pointsLabel}>可用</Text>
              </View>
            )}
            <TouchableOpacity
              testID="talent-close"
              onPress={onClose}
              hitSlop={12}
              accessibilityLabel="关闭"
            >
              <Ionicons name="close" size={24} color={colors.textSecondary} />
            </TouchableOpacity>
          </View>

          {loading && !profile && (
            <View style={styles.center}>
              <ActivityIndicator color={colors.primary} />
              <Text style={styles.hint}>正在读取天赋…</Text>
            </View>
          )}
          {error && !profile && (
            <View style={styles.center}>
              <Text style={styles.error}>{error}</Text>
              <TouchableOpacity style={styles.retry} onPress={() => void load()}>
                <Text style={styles.retryText}>重试</Text>
              </TouchableOpacity>
            </View>
          )}

          {profile && (
            <>
              <View style={styles.summaryRow}>
                <Text style={styles.summaryText}>
                  累计获得 {profile.earned} 点 · 已投入 {profile.spent} 点
                </Text>
                <TouchableOpacity
                  testID="talent-reset"
                  onPress={() => (profile.spent > 0
                    ? setResetOpen(true)
                    : notify('无需重置', '还没有投入任何天赋点。'))}
                  style={styles.resetBtn}
                  accessibilityRole="button"
                >
                  <Ionicons name="refresh" size={15} color={colors.primary} />
                  <Text style={styles.resetText}>免费重置</Text>
                </TouchableOpacity>
              </View>
              <Text style={styles.rule}>{profile.rules.note}</Text>
              <ScrollView
                style={styles.body}
                contentContainerStyle={styles.bodyContent}
                testID="talent-scroll"
              >
                {profile.tree.map((branch) => (
                  <View key={branch.key} style={styles.branch}>
                    <View style={styles.branchHead}>
                      <View style={[styles.branchDot, {
                        backgroundColor: BRANCH_ACCENT[branch.key] ?? colors.primary,
                      }]} />
                      <Text style={styles.branchName}>{branch.name}</Text>
                      <Text style={styles.branchSummary}>{branch.summary}</Text>
                    </View>
                    {branch.nodes.map((node) => (
                      <NodeRow key={node.key} node={node} busy={busy} onAdd={add} />
                    ))}
                  </View>
                ))}
                <Text style={styles.footNote}>
                  每局最多 4 点：完成试潜 1 点，回收成功 / 击退守卫 / 坚守到底 /
                  火力压制 / 救援专家再各 1 点。中途离开不结算。
                </Text>
              </ScrollView>
            </>
          )}
        </View>

        {resetOpen && (
          <View style={styles.confirmLayer}>
            <View style={styles.confirmCard} testID="talent-reset-confirm">
              <Text style={styles.confirmTitle}>重置全部天赋？</Text>
              <Text style={styles.confirmBody}>
                会清空当前 {profile?.spent ?? 0} 点已投入的天赋，并全额返还天赋点。
                下一局按新的点法生效。
              </Text>
              <View style={styles.confirmRow}>
                <TouchableOpacity
                  testID="talent-reset-cancel"
                  style={[styles.confirmBtn, styles.cancelBtn]}
                  onPress={() => setResetOpen(false)}
                >
                  <Text style={styles.cancelText}>取消</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="talent-reset-confirm-btn"
                  style={[styles.confirmBtn, styles.dangerBtn]}
                  onPress={() => void reset()}
                >
                  <Text style={styles.dangerText}>确认重置</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    minHeight: 52, paddingVertical: spacing.sm,
  },
  rowIcon: {
    width: 34, height: 34, borderRadius: 17, alignItems: 'center',
    justifyContent: 'center', backgroundColor: colors.surfaceMuted,
  },
  rowText: { flex: 1, minWidth: 0 },
  rowTitle: { fontSize: fontSize.md, fontWeight: '600', color: colors.text },
  rowHint: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 1 },
  rowValue: { fontSize: fontSize.sm, color: colors.textSecondary },
  rowBadge: {
    fontSize: fontSize.xs, fontWeight: '700', color: colors.white,
    backgroundColor: colors.primary, borderRadius: radius.sm,
    paddingHorizontal: 7, paddingVertical: 3, overflow: 'hidden',
  },

  backdrop: { flex: 1, backgroundColor: colors.overlay, justifyContent: 'flex-end' },
  backdropTap: { flex: 1 },
  sheet: {
    backgroundColor: colors.card, borderTopLeftRadius: radius.lg,
    borderTopRightRadius: radius.lg, paddingHorizontal: spacing.md,
    paddingBottom: spacing.lg, maxHeight: '88%',
  },
  head: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    paddingVertical: spacing.md,
  },
  headText: { flex: 1, minWidth: 0 },
  title: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  subtitle: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 1 },
  pointsPill: {
    flexDirection: 'row', alignItems: 'baseline', gap: 2,
    backgroundColor: colors.primary, borderRadius: radius.md,
    paddingHorizontal: spacing.sm, paddingVertical: 4,
  },
  pointsValue: { color: colors.white, fontSize: fontSize.lg, fontWeight: '800' },
  pointsLabel: { color: colors.white, fontSize: fontSize.xs },
  summaryRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: spacing.sm, paddingBottom: spacing.xs,
  },
  summaryText: { flex: 1, fontSize: fontSize.sm, color: colors.textSecondary },
  resetBtn: {
    flexDirection: 'row', alignItems: 'center', gap: 3, minHeight: 44,
    paddingHorizontal: spacing.sm,
  },
  resetText: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '600' },
  rule: { fontSize: fontSize.xs, color: colors.textMuted, lineHeight: 17 },
  body: { flexGrow: 0, marginTop: spacing.sm },
  bodyContent: { paddingBottom: spacing.sm, gap: spacing.sm },

  center: { alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.xl },
  hint: { fontSize: fontSize.sm, color: colors.textMuted },
  error: { fontSize: fontSize.sm, color: colors.danger, textAlign: 'center' },
  retry: {
    minHeight: 44, minWidth: 88, alignItems: 'center', justifyContent: 'center',
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong,
  },
  retryText: { color: colors.primary, fontWeight: '600' },

  branch: {
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.md,
    padding: spacing.sm, gap: spacing.sm,
  },
  branchHead: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  branchDot: { width: 10, height: 10, borderRadius: 5 },
  branchName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  branchSummary: { flex: 1, fontSize: fontSize.xs, color: colors.textMuted, textAlign: 'right' },
  node: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.sm,
    backgroundColor: colors.surfaceMuted, borderRadius: radius.sm,
    padding: spacing.sm,
  },
  nodeMain: { flex: 1, minWidth: 0, gap: 2 },
  nodeHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  nodeName: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text },
  nodeRank: { fontSize: fontSize.xs, color: colors.textSecondary },
  nodeDetail: { fontSize: fontSize.xs, color: colors.textSecondary },
  nodeFoot: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: 2,
  },
  pips: { flexDirection: 'row', gap: 3 },
  pip: {
    width: 8, height: 8, borderRadius: 4, backgroundColor: colors.card,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  pipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  nodeCost: { fontSize: fontSize.xs, color: colors.textMuted },
  add: {
    width: 44, height: 44, borderRadius: 22, alignItems: 'center',
    justifyContent: 'center', backgroundColor: colors.primary,
  },
  addDone: { backgroundColor: colors.success },

  footNote: { fontSize: fontSize.xs, color: colors.textMuted, lineHeight: 17 },

  confirmLayer: {
    position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: colors.overlay, alignItems: 'center',
    justifyContent: 'center', padding: spacing.lg,
  },
  confirmCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card,
    borderRadius: radius.lg, padding: spacing.lg, gap: spacing.md,
  },
  confirmTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  confirmBody: { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 20 },
  confirmRow: { flexDirection: 'row', gap: spacing.sm },
  confirmBtn: {
    flex: 1, minHeight: 46, borderRadius: radius.md,
    alignItems: 'center', justifyContent: 'center',
  },
  cancelBtn: { backgroundColor: colors.surfaceMuted },
  cancelText: { color: colors.text, fontWeight: '600' },
  dangerBtn: { backgroundColor: colors.danger },
  dangerText: { color: colors.white, fontWeight: '700' },
});