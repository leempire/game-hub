import React from 'react';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useIsFocused } from '@react-navigation/native';
import { AppLogo } from './icons';
import { useExitBackGuard } from './backGuard';
import { useAuth } from './auth';
import AuthScreen from './screens/AuthScreen';
import GamesTab from './screens/GamesTab';
import RoomsTab from './screens/RoomsTab';
import RoomScreen from './screens/RoomScreen';
import GomokuScreen from './screens/GomokuScreen';
import ReversiScreen from './screens/ReversiScreen';
import BattleshipScreen from './screens/BattleshipScreen';
import DotsScreen from './screens/DotsScreen';
import Game2048Screen from './screens/Game2048Screen';
import SpiderScreen from './screens/SpiderScreen';
import MineScreen from './screens/MineScreen';
import LudoScreen from './screens/LudoScreen';
import XiangqiScreen from './screens/XiangqiScreen';
import LuzhanqiScreen from './screens/LuzhanqiScreen';
import ConnectFourScreen from './screens/ConnectFourScreen';
import TetrisScreen from './screens/TetrisScreen';
import UnoScreen from './screens/UnoScreen';
import StasisScreen from './screens/StasisScreen';
import SocialTab from './screens/SocialTab';
import ProfileTab from './screens/ProfileTab';
import AdminScreen from './screens/AdminScreen';
import HistoryScreen from './screens/HistoryScreen';
// 对局页标题也取目录表（`games.tsx`）—— 标题写第二遍就是「改名漏一处」的来源
import { gameTitle } from './games';
import { colors } from './theme';

// Navigation types
export type RootStackParamList = {
  Auth: undefined;
  MainTabs: undefined;
  Room: { code: string };
  Gomoku: { code: string };
  Reversi: { code: string };
  Battleship: { code: string };
  Dots: { code: string };
  Game2048: { code: string };
  Spider: { code: string };
  Minesweeper: { code: string };
  Ludo: { code: string };
  Xiangqi: { code: string };
  Luzhanqi: { code: string };
  ConnectFour: { code: string };
  Tetris: { code: string };
  Uno: { code: string };
  Stasis: { code: string };
  Admin: undefined;
  History: undefined;
};

export type TabParamList = {
  Games: undefined;
  Rooms: undefined;
  Social: undefined;
  Profile: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<TabParamList>();

// Tab icons using Ionicons. 键为英文路由名（route.name），只渲染图标；
// 中文标签由 Screen 的 title 经 tabBarLabel 渲染，此处不输出任何文字，避免重影。
function TabIcon({ label, focused }: { label: string; focused: boolean }) {
  const iconMap: Record<string, { name: keyof typeof Ionicons.glyphMap; filled: keyof typeof Ionicons.glyphMap }> = {
    Games: { name: 'game-controller-outline', filled: 'game-controller' },
    Rooms: { name: 'home-outline', filled: 'home' },
    Social: { name: 'chatbubbles-outline', filled: 'chatbubbles' },
    Profile: { name: 'person-outline', filled: 'person' },
  };
  const entry = iconMap[label];
  const iconName = focused ? (entry?.filled || 'ellipse') : (entry?.name || 'ellipse-outline');
  return (
    <View style={tabIconStyles.wrap}>
      <Ionicons
        name={iconName as any}
        size={22}
        color={focused ? colors.primary : colors.textMuted}
      />
    </View>
  );
}

const tabIconStyles = StyleSheet.create({
  wrap: { alignItems: 'center', justifyContent: 'center', paddingTop: 4 },
});

// Navigation ref for programmatic navigation from outside React components
let navigationRef: React.MutableRefObject<any> | null = null;
export function setNavigationRef(ref: React.MutableRefObject<any>): void {
  navigationRef = ref;
}

export function navigateToRoom(code: string): void {
  navigationRef?.current?.navigate('Room', { code });
}

export function navigateToGomoku(code: string): void {
  navigationRef?.current?.navigate('Gomoku', { code });
}

export function navigateToReversi(code: string): void {
  navigationRef?.current?.navigate('Reversi', { code });
}

export function navigateToMinesweeper(code: string): void {
  navigationRef?.current?.navigate('Minesweeper', { code });
}

export function navigateToBattleship(code: string): void {
  navigationRef?.current?.navigate('Battleship', { code });
}

export function navigateToDots(code: string): void {
  navigationRef?.current?.navigate('Dots', { code });
}

export function navigateToLudo(code: string): void {
  navigationRef?.current?.navigate('Ludo', { code });
}

export function navigateToGame2048(code: string): void {
  navigationRef?.current?.navigate('Game2048', { code });
}

export function navigateToXiangqi(code: string): void {
  navigationRef?.current?.navigate('Xiangqi', { code });
}

export function navigateToConnectFour(code: string): void {
  navigationRef?.current?.navigate('ConnectFour', { code });
}

export function navigateToTetris(code: string): void {
  navigationRef?.current?.navigate('Tetris', { code });
}

export function navigateToUno(code: string): void {
  navigationRef?.current?.navigate('Uno', { code });
}

export function navigateToAdmin(): void {
  navigationRef?.current?.navigate('Admin');
}

export function navigateToHistory(): void {
  navigationRef?.current?.navigate('History');
}

export function navigateToTabs(): void {
  navigationRef?.current?.navigate('MainTabs');
}

// Inner tabs navigator - needs access to navigation
function MainTabs() {
  const insets = useSafeAreaInsets();
  useExitBackGuard(useIsFocused());
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        // 原生端避开状态栏（Web 上 insets 为 0，无影响）
        sceneStyle: { paddingTop: insets.top },
        tabBarIcon: ({ focused }) => <TabIcon label={route.name} focused={focused} />,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.textMuted,
        tabBarLabelStyle: { fontSize: 12 },
        tabBarStyle: {
          borderTopColor: colors.border,
          backgroundColor: colors.card,
          paddingBottom: 4,
          height: 56,
        },
      })}
    >
      <Tab.Screen
        name="Games"
        component={GamesTabWrapper}
        options={{ title: '游戏' }}
      />
      <Tab.Screen
        name="Rooms"
        component={RoomsTabWrapper}
        options={{ title: '房间' }}
      />
      <Tab.Screen
        name="Social"
        component={SocialTab}
        options={{ title: '社交' }}
      />
      <Tab.Screen
        name="Profile"
        component={ProfileTabWrapper}
        options={{ title: '我的' }}
      />
    </Tab.Navigator>
  );
}

// Wrapper to pass navigation props to screens that need them
import { useNavigation } from '@react-navigation/native';

function GamesTabWrapper() {
  const nav = useNavigation<any>();
  return (
    <GamesTab
      onNavigateToRooms={() => nav.navigate('Rooms')}
      onNavigateToRoom={(code: string) => nav.navigate('Room', { code })}
      onNavigateToSpider={(code: string) => nav.navigate('Spider', { code })}
    />
  );
}

function RoomsTabWrapper() {
  const nav = useNavigation<any>();
  return (
    <RoomsTab onNavigateToRoom={(code: string) => nav.navigate('Room', { code })} />
  );
}

function ProfileTabWrapper() {
  const nav = useNavigation<any>();
  return (
    <ProfileTab
      onNavigateToAdmin={() => nav.navigate('Admin')}
      onNavigateToHistory={() => nav.navigate('History')}
    />
  );
}

// Room screen wrapper
function RoomScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <RoomScreen
      code={code}
      onLeave={() => navigation.goBack()}
      onGameStart={(c: string, game?: string) => {
        if (game === 'minesweeper') {
          navigation.navigate('Minesweeper', { code: c });
        } else if (game === 'reversi') {
          navigation.navigate('Reversi', { code: c });
        } else if (game === 'battleship') {
          navigation.navigate('Battleship', { code: c });
        } else if (game === 'dots') {
          navigation.navigate('Dots', { code: c });
        } else if (game === 'ludo') {
          navigation.navigate('Ludo', { code: c });
        } else if (game === 'spider') {
          navigation.navigate('Spider', { code: c });
        } else if (game === '2048') {
          navigation.navigate('Game2048', { code: c });
        } else if (game === 'xiangqi') {
          navigation.navigate('Xiangqi', { code: c });
        } else if (game === 'luzhanqi') {
          navigation.navigate('Luzhanqi', { code: c });
        } else if (game === 'connect4') {
          navigation.navigate('ConnectFour', { code: c });
        } else if (game === 'tetris') {
          navigation.navigate('Tetris', { code: c });
        } else if (game === 'uno') {
          navigation.navigate('Uno', { code: c });
        } else if (game === 'stasis') {
          navigation.navigate('Stasis', { code: c });
        } else {
          navigation.navigate('Gomoku', { code: c });
        }
      }}
    />
  );
}

// Gomoku screen wrapper
function GomokuScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <GomokuScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Reversi screen wrapper
function ReversiScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <ReversiScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Luzhanqi screen wrapper
function LuzhanqiScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <LuzhanqiScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Xiangqi screen wrapper
function XiangqiScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <XiangqiScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Connect Four screen wrapper
function ConnectFourScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <ConnectFourScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Tetris screen wrapper
function TetrisScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <TetrisScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Uno screen wrapper
function UnoScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <UnoScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Stasis screen wrapper
function StasisScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <StasisScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Battleship screen wrapper
function BattleshipScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <BattleshipScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Dots screen wrapper
function DotsScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <DotsScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

function SpiderScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  return <SpiderScreen code={route.params.code}
    onLeave={() => navigation.reset({ index: 0, routes: [{ name: 'MainTabs' }] })} />;
}

// Game2048 screen wrapper
function Game2048ScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <Game2048Screen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

// Mine screen wrapper
function MineScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <MineScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

function LudoScreenWrapper({ route, navigation }: { route: any; navigation: any }) {
  const { code } = route.params;
  return (
    <LudoScreen
      code={code}
      onLeave={() => navigation.goBack()}
    />
  );
}

export default function AppNavigation() {
  const { token, loading } = useAuth();

  if (loading) {
    return (
      <View style={loadingStyles.container}>
        <AppLogo size={72} />
        <Text style={loadingStyles.subtext}>GameHub</Text>
      </View>
    );
  }

  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      {!token ? (
        <Stack.Screen name="Auth" component={AuthScreen} />
      ) : (
        <>
          <Stack.Screen name="MainTabs" component={MainTabs} />
          <Stack.Screen
            name="Room"
            component={RoomScreenWrapper}
            options={{ headerShown: true, title: '房间', headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Gomoku"
            component={GomokuScreenWrapper}
            options={{ headerShown: true, title: gameTitle('gomoku'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Reversi"
            component={ReversiScreenWrapper}
            options={{ headerShown: true, title: gameTitle('reversi'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Battleship"
            component={BattleshipScreenWrapper}
            options={{ headerShown: true, title: gameTitle('battleship'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Dots"
            component={DotsScreenWrapper}
            options={{ headerShown: true, title: gameTitle('dots'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Game2048"
            component={Game2048ScreenWrapper}
            options={{ headerShown: true, title: gameTitle('2048'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Spider"
            component={SpiderScreenWrapper}
            options={{ headerShown: true, title: gameTitle('spider'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Minesweeper"
            component={MineScreenWrapper}
            options={{ headerShown: true, title: gameTitle('minesweeper'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Ludo"
            component={LudoScreenWrapper}
            options={{ headerShown: true, title: gameTitle('ludo'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Luzhanqi"
            component={LuzhanqiScreenWrapper}
            options={{ headerShown: true, title: gameTitle('luzhanqi'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Xiangqi"
            component={XiangqiScreenWrapper}
            options={{ headerShown: true, title: gameTitle('xiangqi'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="ConnectFour"
            component={ConnectFourScreenWrapper}
            options={{ headerShown: true, title: gameTitle('connect4'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Tetris"
            component={TetrisScreenWrapper}
            options={{ headerShown: true, title: gameTitle('tetris'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Uno"
            component={UnoScreenWrapper}
            options={{ headerShown: true, title: gameTitle('uno'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Stasis"
            component={StasisScreenWrapper}
            options={{ headerShown: true, title: gameTitle('stasis'), headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="Admin"
            component={AdminScreen}
            options={{ headerShown: true, title: '用户管理', headerStyle: { backgroundColor: colors.card } }}
          />
          <Stack.Screen
            name="History"
            component={HistoryScreen}
            options={{ headerShown: true, title: '历史战绩', headerStyle: { backgroundColor: colors.card } }}
          />
        </>
      )}
    </Stack.Navigator>
  );
}

const loadingStyles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  subtext: { color: colors.white, fontSize: 24, fontWeight: '800', marginTop: 8 },
});
