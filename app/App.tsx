import React, { useRef, useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import { NavigationContainer } from '@react-navigation/native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { AuthProvider } from './src/auth';
import AppNavigation, { setNavigationRef } from './src/navigation';
import { ToastHost } from './src/toast';
import { initBackGuard } from './src/backGuard';

// Web 端浏览器地址栏与路由同步（返回键语义依赖 history）
const LINKING = {
  prefixes: [],
  config: {
    screens: {
      Auth: 'auth',
      MainTabs: { screens: { Games: '', Rooms: 'rooms', Social: 'social', Profile: 'profile' } },
      Room: 'room/:code',
      Spider: 'spider/:code',
      Gomoku: 'gomoku/:code',
      Minesweeper: 'minesweeper/:code',
      Reversi: 'reversi/:code',
      Battleship: 'battleship/:code',
      Admin: 'admin',
    },
  },
};

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
        <ToastHost />
      </AuthProvider>
    </SafeAreaProvider>
  );
}
