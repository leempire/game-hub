import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView, TextInput,
  ActivityIndicator,
} from 'react-native';
import { useAuth } from '../auth';
import { adminListUsers, adminSetBanned, adminSetRole, User } from '../api';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';

const PAGE_SIZE = 20;

export default function AdminScreen() {
  const { token, user: me } = useAuth();
  const [keyword, setKeyword] = useState('');
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [searching, setSearching] = useState(false);

  const fetchUsers = useCallback(async (p: number, kw: string) => {
    if (!token) return;
    setLoading(true);
    try {
      const result = await adminListUsers(token, p, PAGE_SIZE, kw || undefined);
      setUsers(result.items);
      setTotal(result.total);
      setPage(p);
    } catch (err) {
      notify('加载失败', (err as Error).message);
    } finally {
      setLoading(false);
      setSearching(false);
    }
  }, [token]);

  useEffect(() => {
    fetchUsers(1, '');
  }, [fetchUsers]);

  const handleSearch = () => {
    setSearching(true);
    fetchUsers(1, keyword);
  };

  const handleBan = async (u: User) => {
    if (!token) return;
    if (u.id === me?.id) {
      notify('提示', '不能封禁自己');
      return;
    }
    try {
      const updated = await adminSetBanned(token, u.id, !u.banned);
      setUsers((prev) => prev.map((x) => (x.id === u.id ? updated : x)));
      notify('成功', updated.banned ? '已封禁' : '已解封');
    } catch (err) {
      notify('操作失败', (err as Error).message);
    }
  };

  const handleRole = async (u: User) => {
    if (!token) return;
    if (u.id === me?.id) {
      notify('提示', '不能修改自己的角色');
      return;
    }
    const newRole = u.role === 'admin' ? 'user' : 'admin';
    try {
      const updated = await adminSetRole(token, u.id, newRole);
      setUsers((prev) => prev.map((x) => (x.id === u.id ? updated : x)));
      notify('成功', newRole === 'admin' ? '已设为管理员' : '已取消管理员');
    } catch (err) {
      notify('操作失败', (err as Error).message);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <View style={styles.container}>
      {/* Search bar */}
      <View style={styles.searchBar}>
        <TextInput
          style={styles.searchInput}
          value={keyword}
          onChangeText={setKeyword}
          placeholder="搜索用户名/昵称"
          placeholderTextColor={colors.textFaint}
          onSubmitEditing={handleSearch}
          returnKeyType="search"
        />
        <TouchableOpacity style={styles.searchBtn} onPress={handleSearch} disabled={searching}>
          <Text style={styles.searchBtnText}>{searching ? '...' : '搜索'}</Text>
        </TouchableOpacity>
      </View>

      {/* Stats */}
      <Text style={styles.stats}>共 {total} 个用户</Text>

      {/* List */}
      {loading ? (
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      ) : (
        <ScrollView contentContainerStyle={styles.list}>
          {users.length === 0 ? (
            <Text style={styles.empty}>暂无数据</Text>
          ) : (
            users.map((u) => (
              <View key={u.id} style={styles.userCard}>
                <View style={styles.userRow}>
                  <View style={styles.userInfo}>
                    <Text style={styles.userName}>{u.nickname || u.username}</Text>
                    <Text style={styles.userSub}>@{u.username} · ID: {u.id}</Text>
                  </View>
                  <View style={styles.userBadges}>
                    {u.role === 'admin' && (
                      <View style={[styles.badge, styles.adminBadge]}>
                        <Text style={styles.badgeText}>管理员</Text>
                      </View>
                    )}
                    {u.banned && (
                      <View style={[styles.badge, styles.bannedBadge]}>
                        <Text style={styles.badgeText}>已封禁</Text>
                      </View>
                    )}
                  </View>
                </View>
                <View style={styles.userActions}>
                  <TouchableOpacity
                    style={[styles.smallBtn, u.banned ? styles.unbanBtn : styles.banBtn]}
                    onPress={() => handleBan(u)}
                    disabled={u.id === me?.id}
                  >
                    <Text style={[styles.smallBtnText, u.id === me?.id && styles.disabledText]}>
                      {u.banned ? '解封' : '封禁'}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    style={[styles.smallBtn, u.role === 'admin' ? styles.demoteBtn : styles.promoteBtn]}
                    onPress={() => handleRole(u)}
                    disabled={u.id === me?.id}
                  >
                    <Text style={[styles.smallBtnText, u.id === me?.id && styles.disabledText]}>
                      {u.role === 'admin' ? '取消管理员' : '设为管理员'}
                    </Text>
                  </TouchableOpacity>
                </View>
              </View>
            ))
          )}
        </ScrollView>
      )}

      {/* Pagination */}
      <View style={styles.pagination}>
        <TouchableOpacity
          style={[styles.pageBtn, page <= 1 && styles.pageBtnDisabled]}
          onPress={() => fetchUsers(page - 1, keyword)}
          disabled={page <= 1}
        >
          <Text style={styles.pageBtnText}>上一页</Text>
        </TouchableOpacity>
        <Text style={styles.pageInfo}>{page} / {totalPages}</Text>
        <TouchableOpacity
          style={[styles.pageBtn, page >= totalPages && styles.pageBtnDisabled]}
          onPress={() => fetchUsers(page + 1, keyword)}
          disabled={page >= totalPages}
        >
          <Text style={styles.pageBtnText}>下一页</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  searchBar: {
    flexDirection: 'row',
    padding: spacing.md,
    gap: spacing.sm,
    backgroundColor: colors.card,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  searchInput: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    fontSize: fontSize.md,
    color: colors.text,
    backgroundColor: colors.bg,
  },
  searchBtn: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    justifyContent: 'center',
  },
  searchBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.sm },
  stats: {
    padding: spacing.sm,
    paddingHorizontal: spacing.md,
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  list: { padding: spacing.md, paddingBottom: spacing.xxl },
  empty: { textAlign: 'center', color: colors.textMuted, paddingVertical: spacing.xxl, fontSize: fontSize.md },
  userCard: {
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.sm,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.04,
    shadowRadius: 4,
    elevation: 2,
  },
  userRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  userInfo: { flex: 1 },
  userName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  userSub: { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  userBadges: { flexDirection: 'row', gap: spacing.xs },
  badge: {
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
    borderRadius: radius.full,
  },
  adminBadge: { backgroundColor: colors.primary },
  bannedBadge: { backgroundColor: colors.danger },
  badgeText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
  userActions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.sm },
  smallBtn: {
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: spacing.xs,
    borderRadius: radius.sm,
    flex: 1,
    alignItems: 'center',
  },
  banBtn: { backgroundColor: colors.danger },
  unbanBtn: { backgroundColor: colors.success },
  promoteBtn: { backgroundColor: colors.primary },
  demoteBtn: { backgroundColor: colors.warning },
  smallBtnText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
  disabledText: { opacity: 0.4 },
  pagination: {
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.card,
  },
  pageBtn: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    backgroundColor: colors.primary,
    borderRadius: radius.md,
  },
  pageBtnDisabled: { opacity: 0.4 },
  pageBtnText: { color: colors.white, fontWeight: '600', fontSize: fontSize.sm },
  pageInfo: { fontSize: fontSize.sm, color: colors.textSecondary },
});
