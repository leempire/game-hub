import React, { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, KeyboardAvoidingView, Platform, ScrollView } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { AppLogo } from '../icons';
import { useAuth } from '../auth';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';

type Tab = 'login' | 'register';

export default function AuthScreen() {
  const { login, register } = useAuth();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    if (!username.trim() || !password) {
      notify('提示', '请填写用户名和密码');
      return;
    }
    if (tab === 'register' && password !== confirmPwd) {
      notify('提示', '两次密码不一致');
      return;
    }
    setSubmitting(true);
    try {
      if (tab === 'login') {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password);
      }
    } catch (err) {
      notify('登录失败', (err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    setPassword('');
    setConfirmPwd('');
  };

  return (
    <KeyboardAvoidingView
      style={[styles.container, { paddingTop: insets.top }]}
      behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
    >
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <View style={styles.card}>
          <View style={styles.titleRow}>
            <AppLogo size={40} />
            <Text style={styles.title}>GameHub</Text>
          </View>
          <Text style={styles.subtitle}>和朋友一起玩</Text>

          <View style={styles.tabs}>
            <TouchableOpacity
              style={[styles.tabBtn, tab === 'login' && styles.tabActive]}
              onPress={() => switchTab('login')}
            >
              <Text style={[styles.tabText, tab === 'login' && styles.tabTextActive]}>登录</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.tabBtn, tab === 'register' && styles.tabActive]}
              onPress={() => switchTab('register')}
            >
              <Text style={[styles.tabText, tab === 'register' && styles.tabTextActive]}>注册</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.inputGroup}>
            <Text style={styles.label}>用户名</Text>
            <TextInput
              style={styles.input}
              value={username}
              onChangeText={setUsername}
              placeholder="3-20位字母数字下划线"
              placeholderTextColor={colors.textFaint}
              autoCapitalize="none"
              autoCorrect={false}
            />
          </View>

          <View style={styles.inputGroup}>
            <Text style={styles.label}>密码</Text>
            <TextInput
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder={tab === 'register' ? '6-32位' : '请输入密码'}
              placeholderTextColor={colors.textFaint}
              secureTextEntry
            />
          </View>

          {tab === 'register' && (
            <View style={styles.inputGroup}>
              <Text style={styles.label}>确认密码</Text>
              <TextInput
                style={styles.input}
                value={confirmPwd}
                onChangeText={setConfirmPwd}
                placeholder="再次输入密码"
                placeholderTextColor={colors.textFaint}
                secureTextEntry
              />
            </View>
          )}

          <TouchableOpacity
            style={[styles.submitBtn, submitting && styles.submitBtnDisabled]}
            onPress={handleSubmit}
            disabled={submitting}
          >
            <Text style={styles.submitBtnText}>
              {submitting ? '请稍候...' : tab === 'login' ? '登 录' : '注 册'}
            </Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.primary,
  },
  scroll: {
    flexGrow: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.md,
  },
  card: {
    width: '100%',
    maxWidth: 420,
    backgroundColor: colors.white,
    borderRadius: radius.xl,
    padding: spacing.lg,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 12,
    elevation: 8,
  },
  titleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    marginBottom: spacing.xs,
  },
  title: {
    fontSize: fontSize.title,
    fontWeight: '800',
    color: colors.primary,
  },
  subtitle: {
    fontSize: fontSize.md,
    color: colors.textSecondary,
    textAlign: 'center',
    marginBottom: spacing.lg,
  },
  tabs: {
    flexDirection: 'row',
    backgroundColor: colors.bg,
    borderRadius: radius.md,
    padding: spacing.xs,
    marginBottom: spacing.lg,
  },
  tabBtn: {
    flex: 1,
    paddingVertical: spacing.sm + 2,
    borderRadius: radius.sm,
    alignItems: 'center',
  },
  tabActive: {
    backgroundColor: colors.white,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 3,
    elevation: 2,
  },
  tabText: {
    fontSize: fontSize.md,
    color: colors.textSecondary,
    fontWeight: '600',
  },
  tabTextActive: {
    color: colors.primary,
  },
  inputGroup: {
    marginBottom: spacing.md,
  },
  label: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
    fontWeight: '600',
  },
  input: {
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm + 4,
    fontSize: fontSize.md,
    color: colors.text,
    backgroundColor: colors.bg,
  },
  submitBtn: {
    backgroundColor: colors.primary,
    borderRadius: radius.md,
    paddingVertical: spacing.sm + 6,
    alignItems: 'center',
    marginTop: spacing.sm,
  },
  submitBtnDisabled: {
    opacity: 0.6,
  },
  submitBtnText: {
    color: colors.white,
    fontSize: fontSize.lg,
    fontWeight: '700',
  },
});
