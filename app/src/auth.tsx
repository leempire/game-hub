import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { User, AuthResult, login as apiLogin, register as apiRegister, getMe } from './api';
import { setOnUnauthorized } from './api';

const TOKEN_KEY = 'gamehub_token';
const USER_KEY = 'gamehub_user';

interface AuthState {
  token: string | null;
  user: User | null;
  loading: boolean;
}

interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  updateUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // 更新内存与缓存里的身份（登录、刷新、改昵称都走这里，避免三处各写一遍缓存）
  const applyUser = useCallback(async (u: User) => {
    setUser(u);
    await AsyncStorage.setItem(USER_KEY, JSON.stringify(u));
  }, []);

  // Restore session on mount, then re-sync the identity from the server.
  useEffect(() => {
    (async () => {
      let savedToken: string | null = null;
      try {
        savedToken = await AsyncStorage.getItem(TOKEN_KEY);
        const savedUser = await AsyncStorage.getItem(USER_KEY);
        if (savedToken && savedUser) {
          setToken(savedToken);
          setUser(JSON.parse(savedUser));
        }
      } catch {
        // ignore parse errors, treat as logged out
      } finally {
        setLoading(false);
      }
      if (!savedToken) return;
      // 缓存里的身份是会过期的：管理员在后台改了角色、账号被封禁、本人在别的
      // 设备改了昵称，只读缓存会让客户端一直用旧身份（症状：给了管理员权限，
      // app 里怎么都看不到「用户管理」入口，非得退出重登）。这里回源一次覆盖。
      // 401 由 setOnUnauthorized 兜底登出；网络不通则保留缓存身份，不把人踢下线。
      try {
        await applyUser(await getMe(savedToken));
      } catch {
        // 保留缓存身份
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

  const logout = useCallback(async () => {
    setToken(null);
    setUser(null);
    await clearSession();
  }, [clearSession]);

  // Register 401 handler to auto-logout
  useEffect(() => {
    setOnUnauthorized(() => {
      logout();
    });
  }, [logout]);

  const login = useCallback(async (username: string, password: string) => {
    const result: AuthResult = await apiLogin(username, password);
    setToken(result.token);
    setUser(result.user);
    await persistSession(result.token, result.user);
  }, [persistSession]);

  const register = useCallback(async (username: string, password: string) => {
    const result: AuthResult = await apiRegister(username, password);
    setToken(result.token);
    setUser(result.user);
    await persistSession(result.token, result.user);
  }, [persistSession]);

  const refreshMe = useCallback(async () => {
    if (!token) return;
    await applyUser(await getMe(token));
  }, [token, applyUser]);

  const updateUser = useCallback((u: User) => {
    void applyUser(u);
  }, [applyUser]);

  return (
    <AuthContext.Provider value={{ token, user, loading, login, register, logout, refreshMe, updateUser }}>
      {children}
    </AuthContext.Provider>
  );
}
