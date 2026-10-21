import { Platform } from 'react-native';
import * as Linking from 'expo-linking';

/**
 * 深链兜底：未登录时打开 `/room/<code>`，登录完成后仍要回到那个房间。
 *
 * 为什么需要单独一份：
 * - NavigationContainer 的 linking 在「只有 Auth 屏」时没有目标路由可落，
 *   登录后 URL 已经不在原地了；
 * - 所以必须在 **NavigationContainer 挂载之前** 把初始 URL 记下来，
 *   等 token 出现后再主动导航一次。
 *
 * 只处理「一屏 + 一个房间号」的路径；纯首页 / 房间列表等无需恢复。
 */

export interface PendingRoute {
  screen: string;
  params: Record<string, string>;
}

/** 路径第一段 → 路由名。顺序无关，但必须与 `navigation.tsx` 的 Stack.Screen 对应。 */
const ROUTE_BY_PATH: Record<string, string> = {
  room: 'Room',
  gomoku: 'Gomoku',
  reversi: 'Reversi',
  battleship: 'Battleship',
  dots: 'Dots',
  '2048': 'Game2048',
  minesweeper: 'Minesweeper',
  ludo: 'Ludo',
  luzhanqi: 'Luzhanqi',
  xiangqi: 'Xiangqi',
  connect4: 'ConnectFour',
  tetris: 'Tetris',
  uno: 'Uno',
  stasis: 'Stasis',
};

function parseUrl(url: string | null | undefined): PendingRoute | null {
  if (!url) return null;
  let segments: string[] = [];

  try {
    const u = new URL(url, 'http://local');
    const isHttp = u.protocol === 'http:' || u.protocol === 'https:';
    const parts = u.pathname.split('/').filter(Boolean).map(decodeURIComponent);
    // expo dev client / 转发链接的路径形如 `/--/room/ABC`：
    // `--` 之前是设备与端口，不是路由段。
    const sep = parts.indexOf('--');
    if (sep >= 0) {
      segments = parts.slice(sep + 1);
    } else if (isHttp) {
      segments = parts;
    } else {
      // 自定义 scheme（gamehub://room/ABC）：host 才是第一段
      segments = [u.host, ...parts].filter(Boolean);
    }
  } catch {
    segments = url.split('/').filter(Boolean).map(decodeURIComponent);
  }

  if (segments.length !== 2) return null;

  const screen = ROUTE_BY_PATH[segments[0]];
  if (!screen) return null;
  return { screen, params: { code: segments[1].toUpperCase() } };
}

/**
 * 必须在模块加载时调用（App 导入即执行），早于 react-navigation 改写 URL。
 * Web 同步读 `location.pathname`；原生读 `Linking.getInitialURL()`。
 */
function capture(): Promise<PendingRoute | null> {
  if (Platform.OS === 'web') {
    try {
      return Promise.resolve(parseUrl(window.location.pathname));
    } catch {
      return Promise.resolve(null);
    }
  }
  return Linking.getInitialURL()
    .then(parseUrl)
    .catch(() => null);
}

const captured: Promise<PendingRoute | null> = capture();
let consumed = false;

export function takePendingRoute(): Promise<PendingRoute | null> {
  // 只恢复一次：登出再登录不应该又跳回那条旧链接。
  if (consumed) return Promise.resolve(null);
  consumed = true;
  return captured;
}
