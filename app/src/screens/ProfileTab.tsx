import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
  TextInput, Modal, Linking, KeyboardAvoidingView, Platform,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useIsFocused } from '@react-navigation/native';
import { useAuth } from '../auth';
import { updateNickname, changePassword, fetchMyStats, MyStats } from '../api';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify, formatDate } from '../utils';
import { isNative, isMobileWeb } from '../platform';
import { checkLatestVersion, downloadAndInstallApk, getCurrentVersion, VersionInfo } from '../appUpdate';
import { useModalBackGuard } from '../backGuard';
import { ProfileStatsCard } from '../profileStats';
import { API_BASE } from '../config';

interface Props {
  onNavigateToAdmin: () => void;
  onNavigateToHistory: () => void;
}

export default function ProfileTab({ onNavigateToAdmin, onNavigateToHistory }: Props) {
  const { token, user, logout, updateUser } = useAuth();
  const [showNickname, setShowNickname] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [showLogout, setShowLogout] = useState(false);
  // 弹窗内的错误：**不能只靠 toast** —— 原生端 Modal 是独立窗口，根部的
  // ToastHost 会被它盖住，用户看到的是「点了保存没反应」。
  const [modalError, setModalError] = useState<string | null>(null);
  const [newNickname, setNewNickname] = useState('');
  const [oldPwd, setOldPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [updateInfo, setUpdateInfo] = useState<VersionInfo | null>(null);
  const [stats, setStats] = useState<MyStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const isFocused = useIsFocused();
  useModalBackGuard(showNickname, () => { if (!submitting) setShowNickname(false); });
  useModalBackGuard(showPassword, () => { if (!submitting) setShowPassword(false); });
  useModalBackGuard(showLogout, () => setShowLogout(false));
  useModalBackGuard(!!updateInfo, () => { if (!downloading) setUpdateInfo(null); });
  const autoCheckedRef = useRef(false);

  // 战绩卡：回到「我的」时刷新一次（打完一局回来就能看到新数字）
  const loadStats = useCallback(async () => {
    if (!token) return;
    try {
      setStats(await fetchMyStats(token));
    } catch {
      // 网络失败保留上一次的数字；首次失败显示「暂时无法加载」
    } finally {
      setStatsLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (isFocused) void loadStats();
  }, [isFocused, loadStats]);

  // Auto-check for update on native mount (silent - only notify if update available)
  useEffect(() => {
    if (!isNative || autoCheckedRef.current) return;
    autoCheckedRef.current = true;
    (async () => {
      try {
        const info = await checkLatestVersion();
        const current = getCurrentVersion();
        if (info.has_update && info.version_code > current.versionCode) {
          notify('发现新版本', `v${info.version_name} 可用，请到"我的"页面更新`);
        }
      } catch {
        // silent fail on auto-check
      }
    })();
  }, []);

  const handleCheckUpdate = async () => {
    setCheckingUpdate(true);
    try {
      const info = await checkLatestVersion();
      const current = getCurrentVersion();
      if (!info.has_update || info.version_code <= current.versionCode) {
        notify('已是最新', `当前版本 v${current.versionName}`);
        return;
      }
      // 更新弹窗打开前清掉上一次的下载错误
      setModalError(null);
      setUpdateInfo(info);
    } catch (err) {
      notify('检查更新失败', (err as Error).message);
    } finally {
      setCheckingUpdate(false);
    }
  };

  const handleDownloadUpdate = async () => {
    if (!updateInfo) return;
    setModalError(null);
    setDownloading(true);
    try {
      await downloadAndInstallApk(updateInfo.download_url);
      setUpdateInfo(null);
    } catch (err) {
      setModalError((err as Error).message);
    } finally {
      setDownloading(false);
    }
  };

  const handleDownloadApp = () => {
    Linking.openURL(`${API_BASE}/api/app/latest`);
  };

  if (!user || !token) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>未登录</Text>
      </View>
    );
  }

  const handleNickname = async () => {
    setModalError(null);
    setSubmitting(true);
    try {
      const u = await updateNickname(token, newNickname.trim() || null);
      updateUser(u);
      setShowNickname(false);
      setNewNickname('');
      notify('成功', '昵称已更新');
    } catch (err) {
      setModalError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handlePassword = async () => {
    setModalError(null);
    if (!oldPwd || !newPwd) {
      setModalError('请填写完整');
      return;
    }
    if (newPwd.length < 6) {
      setModalError('新密码至少6位');
      return;
    }
    setSubmitting(true);
    try {
      await changePassword(token, oldPwd, newPwd);
      setShowPassword(false);
      setOldPwd('');
      setNewPwd('');
      // 密码改了，手里这张 token 就不该再继续用——退出到登录页，用新密码重新登。
      // 登录页会预填用户名，并显示这句原因。
      await logout('密码已修改，请用新密码重新登录');
    } catch (err) {
      setModalError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleLogout = async () => {
    setShowLogout(false);
    await logout();
  };

  // 关闭弹窗：提交中不允许关闭（否则请求成功后界面状态与已关闭的弹窗打架）
  const closeNickname = () => {
    if (submitting) return;
    setShowNickname(false);
    setModalError(null);
  };
  const closePassword = () => {
    if (submitting) return;
    setShowPassword(false);
    setModalError(null);
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {/* Avatar placeholder */}
      <View style={styles.avatarWrap}>
        <Text style={styles.avatarText}>
          {(user.nickname || user.username || '?').charAt(0).toUpperCase()}
        </Text>
      </View>

      {/* User info card */}
      <View style={styles.infoCard}>
        <Text style={styles.displayName}>{user.nickname || user.username}</Text>
        <Text style={styles.username}>@{user.username}</Text>
        <View style={styles.roleBadge}>
          <Text style={styles.roleText}>{user.role === 'admin' ? '管理员' : '用户'}</Text>
        </View>
        <Text style={styles.joinDate}>注册于 {formatDate(user.created_at)}</Text>
      </View>

      {/* 战绩卡：总场次 / 胜率 / 当前连胜 + 近 7 天与常玩游戏 */}
      <ProfileStatsCard
        stats={stats}
        loading={statsLoading}
        onOpenHistory={onNavigateToHistory}
      />

      {/* Actions */}
      <View style={styles.actions}>
        <TouchableOpacity style={styles.actionItem} onPress={onNavigateToHistory}>
          <Ionicons name="time-outline" size={20} color={colors.text} style={styles.actionIcon} />
          <Text style={styles.actionText}>历史战绩</Text>
          <Text style={styles.actionArrow}>›</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.actionItem} onPress={() => { setNewNickname(user.nickname || ''); setModalError(null); setShowNickname(true); }}>
          <Ionicons name="create-outline" size={20} color={colors.text} style={styles.actionIcon} />
          <Text style={styles.actionText}>修改昵称</Text>
          <Text style={styles.actionArrow}>›</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.actionItem} onPress={() => { setOldPwd(''); setNewPwd(''); setModalError(null); setShowPassword(true); }}>
          <Ionicons name="lock-closed-outline" size={20} color={colors.text} style={styles.actionIcon} />
          <Text style={styles.actionText}>修改密码</Text>
          <Text style={styles.actionArrow}>›</Text>
        </TouchableOpacity>

        {user.role === 'admin' && (
          <TouchableOpacity style={styles.actionItem} onPress={onNavigateToAdmin}>
            <Ionicons name="people-outline" size={20} color={colors.text} style={styles.actionIcon} />
            <Text style={styles.actionText}>用户管理</Text>
            <Text style={styles.actionArrow}>›</Text>
          </TouchableOpacity>
        )}

        {/* Mobile web: download app */}
        {isMobileWeb && (
          <TouchableOpacity style={styles.actionItem} onPress={handleDownloadApp}>
            <Ionicons name="download-outline" size={20} color={colors.primary} style={styles.actionIcon} />
            <Text style={[styles.actionText, { color: colors.primary }]}>下载 APP</Text>
            <Text style={styles.actionArrow}>›</Text>
          </TouchableOpacity>
        )}

        {/* Native Android: check update */}
        {isNative && (
          <TouchableOpacity
            style={styles.actionItem}
            onPress={handleCheckUpdate}
            disabled={checkingUpdate || downloading}
          >
            <Ionicons name="cloud-download-outline" size={20} color={colors.text} style={styles.actionIcon} />
            <Text style={styles.actionText}>
              {downloading ? '下载中...' : checkingUpdate ? '检查中...' : '检查更新'}
            </Text>
            <Text style={styles.actionArrow}>›</Text>
          </TouchableOpacity>
        )}

        <TouchableOpacity style={[styles.actionItem, styles.logoutItem]} onPress={() => setShowLogout(true)}>
          <Ionicons name="log-out-outline" size={20} color={colors.danger} style={styles.actionIcon} />
          <Text style={[styles.actionText, styles.logoutText]}>退出登录</Text>
          <Text style={styles.actionArrow} />
        </TouchableOpacity>
      </View>

      {/* Nickname modal */}
      <Modal visible={showNickname} transparent animationType="fade" onRequestClose={closeNickname}>
        <KeyboardAvoidingView
          style={styles.modalOverlay}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          <View style={styles.modalCard}>
            <ScrollView style={styles.modalScroll} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
              <Text style={styles.modalTitle}>修改昵称</Text>
              <TextInput
                style={styles.modalInput}
                value={newNickname}
                onChangeText={setNewNickname}
                placeholder="输入新昵称（留空清除）"
                placeholderTextColor={colors.textFaint}
                returnKeyType="done"
                onSubmitEditing={() => void handleNickname()}
              />
              {modalError ? <Text style={styles.modalError} accessibilityRole="alert">{modalError}</Text> : null}
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalBtnPrimary, submitting && styles.modalBtnDisabled]}
                onPress={handleNickname}
                disabled={submitting}
              >
                <Text style={styles.modalBtnPrimaryText}>{submitting ? '保存中...' : '保存'}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalBtnSecondary, submitting && styles.modalBtnDisabled]}
                onPress={closeNickname}
                disabled={submitting}
              >
                <Text style={styles.modalBtnSecondaryText}>取消</Text>
              </TouchableOpacity>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* Password modal */}
      <Modal visible={showPassword} transparent animationType="fade" onRequestClose={closePassword}>
        <KeyboardAvoidingView
          style={styles.modalOverlay}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          <View style={styles.modalCard}>
            <ScrollView style={styles.modalScroll} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
              <Text style={styles.modalTitle}>修改密码</Text>
              <TextInput
                style={styles.modalInput}
                value={oldPwd}
                onChangeText={setOldPwd}
                placeholder="当前密码"
                placeholderTextColor={colors.textFaint}
                secureTextEntry
                autoComplete="current-password"
                textContentType="password"
              />
              <TextInput
                style={styles.modalInput}
                value={newPwd}
                onChangeText={setNewPwd}
                placeholder="新密码（至少6位）"
                placeholderTextColor={colors.textFaint}
                secureTextEntry
                autoComplete="new-password"
                textContentType="newPassword"
                returnKeyType="go"
                onSubmitEditing={() => void handlePassword()}
              />
              {modalError ? <Text style={styles.modalError} accessibilityRole="alert">{modalError}</Text> : null}
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalBtnPrimary, submitting && styles.modalBtnDisabled]}
                onPress={handlePassword}
                disabled={submitting}
              >
                <Text style={styles.modalBtnPrimaryText}>{submitting ? '修改中...' : '修改'}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modalBtn, styles.modalBtnSecondary, submitting && styles.modalBtnDisabled]}
                onPress={closePassword}
                disabled={submitting}
              >
                <Text style={styles.modalBtnSecondaryText}>取消</Text>
              </TouchableOpacity>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* Update confirm modal */}
      <Modal
        visible={!!updateInfo}
        transparent
        animationType="fade"
        onRequestClose={() => { if (!downloading) setUpdateInfo(null); }}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>发现新版本</Text>
            <Text style={styles.updateVersionText}>
              v{updateInfo?.version_name}
            </Text>
            <Text style={styles.updateTip}>
              当前版本 v{getCurrentVersion().versionName}，是否立即下载安装？
            </Text>
            {modalError ? <Text style={styles.modalError} accessibilityRole="alert">{modalError}</Text> : null}
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary, downloading && styles.modalBtnDisabled]}
              onPress={handleDownloadUpdate}
              disabled={downloading}
            >
              <Text style={styles.modalBtnPrimaryText}>
                {downloading ? '下载中...' : '立即下载'}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary, downloading && styles.modalBtnDisabled]}
              onPress={() => setUpdateInfo(null)}
              disabled={downloading}
            >
              <Text style={styles.modalBtnSecondaryText}>稍后再说</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Logout confirm modal */}
      <Modal visible={showLogout} transparent animationType="fade" onRequestClose={() => setShowLogout(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>退出登录</Text>
            <Text style={styles.updateTip}>退出后需要重新输入密码才能继续游戏。</Text>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnDanger]}
              onPress={handleLogout}
            >
              <Text style={styles.modalBtnPrimaryText}>退出登录</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setShowLogout(false)}
            >
              <Text style={styles.modalBtnSecondaryText}>取消</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { padding: spacing.md, paddingBottom: spacing.xxl, alignItems: 'center' },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  errorText: { color: colors.danger, fontSize: fontSize.lg },
  avatarWrap: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.lg,
    marginBottom: spacing.md,
  },
  avatarText: { fontSize: 36, fontWeight: '800', color: colors.white },
  infoCard: {
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.lg,
    alignItems: 'center',
    width: '100%',
    maxWidth: 400,
    marginBottom: spacing.lg,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 3,
  },
  displayName: { fontSize: fontSize.xl, fontWeight: '800', color: colors.text },
  username: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  roleBadge: {
    backgroundColor: colors.primary,
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: 2,
    borderRadius: radius.full,
    marginTop: spacing.sm,
  },
  roleText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '600' },
  joinDate: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: spacing.sm },
  actions: {
    width: '100%',
    maxWidth: 400,
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    overflow: 'hidden',
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 3,
  },
  actionItem: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.md,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  logoutItem: { borderBottomWidth: 0 },
  actionIcon: { marginRight: spacing.md },
  actionText: { flex: 1, fontSize: fontSize.md, color: colors.text, fontWeight: '500' },
  logoutText: { color: colors.danger },
  actionArrow: { fontSize: fontSize.xl, color: colors.textFaint },
  // Modal
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
  modalBtnDanger: { backgroundColor: colors.danger },
  modalBtnDisabled: { opacity: 0.5 },
  modalScroll: { flexGrow: 0, flexShrink: 1 },
  modalError: {
    fontSize: fontSize.sm, color: colors.danger, textAlign: 'center',
    marginBottom: spacing.sm, lineHeight: 20,
  },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
  updateVersionText: {
    fontSize: 28,
    fontWeight: '800',
    color: colors.primary,
    textAlign: 'center',
    marginBottom: spacing.xs,
  },
  updateTip: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    textAlign: 'center',
    marginBottom: spacing.md,
  },
});
