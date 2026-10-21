import React from 'react';
import {
  View, Text, TouchableOpacity, TextInput, ScrollView, StyleSheet,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, spacing, radius, fontSize } from './theme';
import { GAME_CATEGORIES, gameShort, type GameCategoryKey } from './games';

/**
 * 主页顶部三块：主 CTA / 最近玩过 / 搜索与分类筛选。
 *
 * 从 `GamesTab.tsx` 拆出来控制文件长度；这三块纯展示，状态仍在 GamesTab。
 */

export function HomeHero({ onCreate, onJoin, onBrowse }: {
  onCreate: () => void;
  onJoin: () => void;
  onBrowse: () => void;
}) {
  return (
    <View style={styles.hero}>
      <Text style={styles.heroTitle}>和朋友开一局</Text>
      <Text style={styles.heroSub}>13 款小游戏 · 房间号或链接直接进</Text>
      <TouchableOpacity style={styles.heroPrimary} onPress={onCreate} accessibilityRole="button">
        <Ionicons name="add-circle" size={20} color={colors.white} />
        <Text style={styles.heroPrimaryText}>立即开房</Text>
      </TouchableOpacity>
      <View style={styles.heroRow}>
        <TouchableOpacity style={styles.heroSecondary} onPress={onJoin} accessibilityRole="button">
          <Ionicons name="key-outline" size={16} color={colors.primary} />
          <Text style={styles.heroSecondaryText}>输入房间号</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.heroSecondary} onPress={onBrowse} accessibilityRole="button">
          <Ionicons name="home-outline" size={16} color={colors.primary} />
          <Text style={styles.heroSecondaryText}>浏览房间</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

export function RecentGames({ games, onPick }: {
  games: string[];
  onPick: (game: string) => void;
}) {
  if (games.length === 0) return null;
  return (
    <View style={styles.recentBlock}>
      <Text style={styles.sectionLabel}>最近玩过</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.recentRow}>
        {games.map((game) => (
          <TouchableOpacity
            key={game}
            style={styles.recentChip}
            onPress={() => onPick(game)}
            accessibilityRole="button"
            accessibilityLabel={`再开一局${gameShort(game)}`}
          >
            <Text style={styles.recentText}>{gameShort(game)}</Text>
            <Text style={styles.recentAction}>再开一局</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

export type CategoryFilter = GameCategoryKey | 'all';

export function GameFilters({ query, onChangeQuery, category, onChangeCategory }: {
  query: string;
  onChangeQuery: (value: string) => void;
  category: CategoryFilter;
  onChangeCategory: (value: CategoryFilter) => void;
}) {
  const chips: Array<{ key: CategoryFilter; name: string }> = [
    { key: 'all', name: '全部' },
    ...GAME_CATEGORIES.map((c) => ({ key: c.key as CategoryFilter, name: c.name })),
  ];
  return (
    <View style={styles.filters}>
      <View style={styles.searchBox}>
        <Ionicons name="search" size={18} color={colors.textMuted} />
        <TextInput
          style={styles.searchInput}
          value={query}
          onChangeText={onChangeQuery}
          placeholder="搜索游戏"
          placeholderTextColor={colors.textFaint}
          returnKeyType="search"
          accessible
          accessibilityLabel="搜索游戏"
        />
        {query.length > 0 && (
          <TouchableOpacity onPress={() => onChangeQuery('')} accessibilityLabel="清空搜索">
            <Ionicons name="close-circle" size={18} color={colors.textMuted} />
          </TouchableOpacity>
        )}
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
        {chips.map((chip) => {
          const active = category === chip.key;
          return (
            <TouchableOpacity
              key={chip.key}
              style={[styles.chip, active && styles.chipActive]}
              onPress={() => onChangeCategory(chip.key)}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>{chip.name}</Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  hero: {
    backgroundColor: colors.primary,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  heroTitle: { fontSize: fontSize.xl, fontWeight: '800', color: colors.white },
  heroSub: { fontSize: fontSize.sm, color: colors.white, opacity: 0.85, marginTop: 2 },
  heroPrimary: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    minHeight: 48,
    marginTop: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.primaryDark,
  },
  heroPrimaryText: { fontSize: fontSize.md, fontWeight: '700', color: colors.white },
  heroRow: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  heroSecondary: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    minHeight: 44,
    borderRadius: radius.md,
    backgroundColor: colors.white,
  },
  heroSecondaryText: { fontSize: fontSize.sm, fontWeight: '600', color: colors.primary },
  recentBlock: { marginBottom: spacing.md },
  sectionLabel: {
    fontSize: fontSize.sm, fontWeight: '700', color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  recentRow: { gap: spacing.sm, paddingRight: spacing.sm },
  recentChip: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minHeight: 44,
    justifyContent: 'center',
  },
  recentText: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  recentAction: { fontSize: fontSize.xs, color: colors.primary, marginTop: 1 },
  filters: { marginBottom: spacing.sm },
  searchBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    paddingHorizontal: spacing.sm,
    minHeight: 44,
    marginBottom: spacing.sm,
  },
  searchInput: { flex: 1, fontSize: fontSize.md, color: colors.text, paddingVertical: spacing.xs },
  chipRow: { gap: spacing.sm, paddingRight: spacing.sm },
  chip: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radius.full,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.card,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { fontSize: fontSize.sm, fontWeight: '600', color: colors.textSecondary },
  chipTextActive: { color: colors.white },
});
