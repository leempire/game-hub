import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';

/**
 * Web 键盘操作（2048 / 俄罗斯方块等需要方向键的游戏）。
 *
 * **必须是共享 helper，不能在屏幕里裸写 `window.addEventListener`**：
 * 原生 RN 的 `window === global` 且没有 `addEventListener`，裸写会在挂载时
 * TypeError 白屏（1.3.1 APP 五子棋事故）。这里先用 `Platform.OS` 挡掉原生，
 * 再碰 `window`，与 backGuard 的收口方式一致。
 *
 * 回调放在 ref 里：调用方通常传入依赖 WS 状态的函数，不希望每次渲染都重绑。
 */
export function useWebKeys(handler: (event: KeyboardEvent) => void): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const onKeyDown = (event: KeyboardEvent) => handlerRef.current(event);
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
