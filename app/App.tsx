import React, { useRef, useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { AuthProvider, useAuth } from './src/auth';
import AppNavigation, { setNavigationRef } from './src/navigation';
import { ToastHost } from './src/toast';
import { initBackGuard } from './src/backGuard';
import { takePendingRoute } from './src/deepLink';

// Web 端浏览器地址栏与路由同步（返回键语义依赖 history）。
// **每个 Stack.Screen 都要有 path**：漏一个，刷新 / 分享链接就落回主页
// （之前只配了 6 个，UNO / 俄罗斯方块 / 象棋等全部刷新即丢）。
const LINKING = {
  prefixes: [],
  config: {
    screens: {
      Auth: 'auth',
      MainTabs: { screens: { Games: '', Rooms: 'rooms', Social: 'social', Profile: 'profile' } },
      Room: 'room/:code',
      Gomoku: 'gomoku/:code',
      Minesweeper: 'minesweeper/:code',
      Reversi: 'reversi/:code',
      Battleship: 'battleship/:code',
      Dots: 'dots/:code',
      Game2048: '2048/:code',
      Ludo: 'ludo/:code',
      Xiangqi: 'xiangqi/:code',
      Luzhanqi: 'luzhanqi/:code',
      ConnectFour: 'connect4/:code',
      Tetris: 'tetris/:code',
      Uno: 'uno/:code',
      Stasis: 'stasis/:code',
      History: 'history',
      Admin: 'admin',
    },
  },
};

/**
 * 未登录时打开房间链接：初始 URL 在 deepLink.ts 里已经记下，
 * 登录成功后再主动导航一次（NavigationContainer 在 Auth-only 时没有目标路由可落）。
 */
function DeepLinkResume({ navRef }: { navRef: React.MutableRefObject<any> }) {
  const { token } = useAuth();
  useEffect(() => {
    if (!token) return;
    let alive = true;
    void takePendingRoute().then((target) => {
      if (!alive || !target) return;
      // 等导航容器 ready 再 navigate；登录瞬间容器可能还没挂好。
      const tryNavigate = (attempt = 0) => {
        if (!alive) return;
        const nav = navRef.current;
        if (!nav || (typeof nav.isReady === 'function' && !nav.isReady())) {
          if (attempt < 20) setTimeout(() => tryNavigate(attempt + 1), 50);
          return;
        }
        const cur = nav.getCurrentRoute();
        if (cur?.name === target.screen && cur?.params?.code === target.params.code) return;
        nav.navigate(target.screen, target.params);
      };
      tryNavigate();
    });
    return () => { alive = false; };
  }, [token, navRef]);
  return null;
}

export default function App() {
  const navigationRef = useRef<any>(null);

  useEffect(() => {
    setNavigationRef(navigationRef);
    initBackGuard(() => navigationRef.current?.getCurrentRoute()?.name);
  }, []);

  return (
    <SafeAreaProvider>
      <AuthProvider>
        <NavigationContainer ref={navigationRef} linking={LINKING as any}>
          <StatusBar style="auto" />
          <AppNavigation />
        </NavigationContainer>
        <DeepLinkResume navRef={navigationRef} />
        <ToastHost />
      </AuthProvider>
    </SafeAreaProvider>
  );
}
