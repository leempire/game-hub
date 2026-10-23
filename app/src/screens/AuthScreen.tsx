import React, { useRef, useState, useEffect } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, KeyboardAvoidingView, Platform, ScrollView } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { AppLogo } from '../icons';
import { useAuth, LAST_USERNAME_KEY } from '../auth';
import { colors, spacing, radius, fontSize } from '../theme';

type Tab = 'login' | 'register';

export default function AuthScreen() {
  const { login, register, authError, clearAuthError } = useAuth();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const passwordRef = useRef<TextInput>(null);
  const confirmRef = useRef<TextInput>(null);

  // 上次登录的用户名（改密 / token 过期被踢回时预填，免去重新输入）
  useEffect(() => {
    AsyncStorage.getItem(LAST_USERNAME_KEY)
      .then((v) => { if (v) setUsername((cur) => cur || v); })
      .catch(() => {});
  }, []);

  const handleSubmit = async () => {
    setFormError(null);
    clearAuthError();
    const name = username.trim();
    if (!name || !password) {
      setFormError('请填写用户名和密码');
      return;
    }
    if (tab === 'register') {
      if (!/^[A-Za-z0-9_]{3,20}$/.test(name)) {
        setFormError('用户名需为 3-20 位字母、数字或下划线');
        return;
      }
      if (password.length < 6 || password.length > 32) {
        setFormError('密码需为 6-32 位');
        return;
      }
      if (password !== confirmPwd) {
        setFormError('两次密码不一致');
        return;
      }
    }
    setSubmitting(true);
    try {
      if (tab === 'login') {
        await login(name, password);
      } else {
        await register(name, password);
      }
    } catch (err) {
      // **按 tab 区分文案**（以前注册失败也写「登录失败」）。错误留在表单里，
      // 不用 toast —— 它 2.6 秒就消失，用户来不及看清。
      setFormError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const switchTab = (t: Tab) => {
    setTab(t);
    setPassword('');
    setConfirmPwd('');
    setFormError(null);
    clearAuthError();
  };

  const shownError = formError || authError;

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
              autoFocus
              autoComplete="username"
              textContentType="username"
              returnKeyType="next"
              onSubmitEditing={() => passwordRef.current?.focus()}
            />
          </View>

          <View style={styles.inputGroup}>
            <Text style={styles.label}>密码</Text>
            <TextInput
              ref={passwordRef}
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder={tab === 'register' ? '6-32位' : '请输入密码'}
              placeholderTextColor={colors.textFaint}
              secureTextEntry
              autoComplete={tab === 'register' ? 'new-password' : 'current-password'}
              textContentType={tab === 'register' ? 'newPassword' : 'password'}
              returnKeyType={tab === 'register' ? 'next' : 'go'}
              onSubmitEditing={() => {
                if (tab === 'register') confirmRef.current?.focus();
                else void handleSubmit();
              }}
            />
          </View>

          {tab === 'register' && (
            <View style={styles.inputGroup}>
              <Text style={styles.label}>确认密码</Text>
              <TextInput
                ref={confirmRef}
                style={styles.input}
                value={confirmPwd}
                onChangeText={setConfirmPwd}
                placeholder="再次输入密码"
                placeholderTextColor={colors.textFaint}
                secureTextEntry
                autoComplete="new-password"
                textContentType="newPassword"
                returnKeyType="go"
                onSubmitEditing={() => void handleSubmit()}
              />
            </View>
          )}

          {shownError ? (
            <Text style={styles.errorText} accessibilityRole="alert">{shownError}</Text>
          ) : null}

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
  errorText: {
    fontSize: fontSize.sm,
    color: colors.danger,
    textAlign: 'center',
    marginTop: spacing.xs,
    lineHeight: 20,
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
