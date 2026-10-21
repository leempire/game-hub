import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { User, AuthResult, login as apiLogin, register as apiRegister, getMe } from './api';
import { setOnUnauthorized } from './api';

const TOKEN_KEY = 'gamehub_token';
const USER_KEY = 'gamehub_user';
/** 上次登录的用户名：改密 / token 过期被踢回登录页时预填，免去重新输入。 */
export const LAST_USERNAME_KEY = 'gamehub_last_username';
/** 回源校验身份的时限：网络挂死时不能让启动页无限等待。 */
const ME_TIMEOUT_MS = 5000;

interface AuthState {
  token: string | null;
  user: User | null;
  loading: boolean;
  /** 被动登出的原因（token 过期 / 改密），登录页据此给一句说明。 */
  authError: string | null;
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: (reason?: string | null) => Promise<void>;
  clearAuthError: () => void;
  refreshMe: () => Promise<void>;
  updateUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/** 给回源请求加一个上限：网络挂死时不能让启动页无限转圈。 */
function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    p.then(
      (v) => { clearTimeout(timer); resolve(v); },
      (e) => { clearTimeout(timer); reject(e); },
    );
  });
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  // 更新内存与缓存里的身份（登录、刷新、改昵称都走这里，避免三处各写一遍缓存）
  const applyUser = useCallback(async (u: User) => {
    setUser(u);
    await AsyncStorage.setItem(USER_KEY, JSON.stringify(u));
  }, []);

  // Restore session on mount, then re-sync the identity from the server.
  //
  // **`loading` 必须等到回源结束再关**：以前读完缓存就 `setLoading(false)`，
  // 于是先渲染主页、`getMe` 401 后又把人踢回登录页 —— 用户看到「主页闪一下」。
  // 回源给 5 秒上限：网络不通时保留缓存身份，但不能让启动页无限等待。
  useEffect(() => {
    (async () => {
      let savedToken: string | null = null;
      try {
        savedToken = await AsyncStorage.getItem(TOKEN_KEY);
        const savedUser = await AsyncStorage.getItem(USER_KEY);
        if (savedToken) setToken(savedToken);
        if (savedUser) setUser(JSON.parse(savedUser));
      } catch {
        // ignore parse errors, treat as logged out
      }
      if (!savedToken) {
        setLoading(false);
        return;
      }
      // 缓存里的身份是会过期的：管理员在后台改了角色、账号被封禁、本人在别的
      // 设备改了昵称，只读缓存会让客户端一直用旧身份（症状：给了管理员权限，
      // app 里怎么都看不到「用户管理」入口，非得退出重登）。这里回源一次覆盖。
      // 401 由 setOnUnauthorized 兜底登出；网络不通则保留缓存身份，不把人踢下线。
      try {
        await applyUser(await withTimeout(getMe(savedToken), ME_TIMEOUT_MS));
      } catch {
        // 保留缓存身份（401 已经在 apiFetch 里触发登出并带上原因）
      } finally {
        setLoading(false);
      }
    })();
  }, [applyUser]);

  const persistSession = useCallback(async (t: string, u: User) => {
    await AsyncStorage.setItem(TOKEN_KEY, t);
    await AsyncStorage.setItem(USER_KEY, JSON.stringify(u));
  }, []);

  const clearSession = useCallback(async () => {
    await AsyncStorage.removeItem(TOKEN_KEY);
    await AsyncStorage.removeItem(USER_KEY);
  }, []);

  const logout = useCallback(async (reason: string | null = null) => {
    setToken(null);
    setUser(null);
    setAuthError(reason);
    await clearSession();
  }, [clearSession]);

  // Register 401 handler to auto-logout. 带一句原因，登录页会显示出来 ——
  // 静默踢回登录页时用户只会以为「应用坏了」。
  useEffect(() => {
    setOnUnauthorized(() => {
      void logout('登录已过期，请重新登录');
    });
  }, [logout]);

  const login = useCallback(async (username: string, password: string) => {
    const result: AuthResult = await apiLogin(username, password);
    setToken(result.token);
    setUser(result.user);
    setAuthError(null);
    await persistSession(result.token, result.user);
    await AsyncStorage.setItem(LAST_USERNAME_KEY, username);
  }, [persistSession]);

  const register = useCallback(async (username: string, password: string) => {
    const result: AuthResult = await apiRegister(username, password);
    setToken(result.token);
    setUser(result.user);
    setAuthError(null);
    await persistSession(result.token, result.user);
    await AsyncStorage.setItem(LAST_USERNAME_KEY, username);
  }, [persistSession]);

  const clearAuthError = useCallback(() => setAuthError(null), []);

  const refreshMe = useCallback(async () => {
    if (!token) return;
    await applyUser(await getMe(token));
  }, [token, applyUser]);

  const updateUser = useCallback((u: User) => {
    void applyUser(u);
  }, [applyUser]);

  return (
    <AuthContext.Provider value={{
      token, user, loading, authError, login, register, logout,
      clearAuthError, refreshMe, updateUser,
    }}>
      {children}
    </AuthContext.Provider>
  );
}
