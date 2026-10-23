import React from 'react';
import { View, Text, TouchableOpacity, ActivityIndicator, StyleSheet } from 'react-native';
import { colors, spacing, radius, fontSize } from './theme';
import { gameShort } from './games';
import type { MyStats } from './api';

/** 「我的」页的战绩卡（总场次 / 胜率 / 当前连胜 + 近 7 天与常玩游戏）。 */
export function ProfileStatsCard({ stats, loading, onOpenHistory }: {
  stats: MyStats | null;
  loading: boolean;
  onOpenHistory: () => void;
}) {
  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.title}>战绩</Text>
        <TouchableOpacity onPress={onOpenHistory} accessibilityRole="button">
          <Text style={styles.more}>查看全部 ›</Text>
        </TouchableOpacity>
      </View>
      {loading ? (
        <View style={styles.loading}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : !stats ? (
        <Text style={styles.empty}>暂时无法加载战绩</Text>
      ) : stats.total === 0 ? (
        <Text style={styles.empty}>还没有对局记录，去玩一局吧</Text>
      ) : (
        <>
          <View style={styles.row}>
            <View style={styles.cell}>
              <Text style={styles.value}>{stats.total}</Text>
              <Text style={styles.label}>总场次</Text>
            </View>
            <View style={styles.divider} />
            <View style={styles.cell}>
              <Text style={styles.value}>
                {stats.win_rate == null ? '—' : `${Math.round(stats.win_rate * 100)}%`}
              </Text>
              <Text style={styles.label}>胜率</Text>
            </View>
            <View style={styles.divider} />
            <View style={styles.cell}>
              <Text style={styles.value}>
                {stats.current_streak > 0 ? `${stats.current_streak} 连胜` : '—'}
              </Text>
              <Text style={styles.label}>当前</Text>
            </View>
          </View>
          <Text style={styles.meta}>
            近 7 天 {stats.recent_7d} 局
            {stats.top_games.length > 0
              ? ` · 常玩 ${stats.top_games.map((g) => gameShort(g.game)).join(' / ')}`
              : ''}
          </Text>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    width: '100%',
    maxWidth: 400,
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.lg,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 3,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  more: { fontSize: fontSize.sm, color: colors.primary, fontWeight: '600' },
  loading: { paddingVertical: spacing.md, alignItems: 'center' },
  empty: {
    fontSize: fontSize.sm, color: colors.textSecondary,
    textAlign: 'center', paddingVertical: spacing.md,
  },
  row: {
    flexDirection: 'row', alignItems: 'center',
    marginTop: spacing.sm, paddingVertical: spacing.xs,
  },
  cell: { flex: 1, alignItems: 'center' },
  divider: { width: 1, height: 28, backgroundColor: colors.border },
  value: { fontSize: fontSize.lg, fontWeight: '800', color: colors.text },
  label: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  meta: {
    fontSize: fontSize.xs, color: colors.textSecondary,
    marginTop: spacing.xs, textAlign: 'center',
  },
});
