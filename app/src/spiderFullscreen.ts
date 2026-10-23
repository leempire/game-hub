import { Platform } from 'react-native';

let session = 0;

/** 浏览器限制全屏或方向锁时，牌桌仍可使用组件里的横屏布局。 */
export function requestSpiderFullscreen(onExit: () => void): () => void {
  if (Platform.OS !== 'web' || !document.documentElement.requestFullscreen) return () => {};
  const id = ++session;
  const element = document.documentElement;
  let stopped = false;
  let entered = false;
  let locked = false;
  const release = () => {
    if (id !== session) return;
    if (locked) { window.screen.orientation?.unlock(); locked = false; }
    if (entered && document.fullscreenElement === element) void document.exitFullscreen().catch(() => {});
  };
  const changed = () => {
    if (entered && document.fullscreenElement !== element) onExit();
  };
  document.addEventListener('fullscreenchange', changed);
  try {
    void element.requestFullscreen().then(async () => {
      entered = true;
      if (stopped) { release(); return; }
      try {
        if (window.screen.orientation?.lock) {
          await window.screen.orientation.lock('landscape');
          locked = true;
          if (stopped) release();
        }
      } catch { /* 不支持方向锁时保留横屏布局。 */ }
    }).catch(() => {}); // 拒绝系统全屏时仍然铺满可用页面。
  } catch { /* 部分浏览器同步拒绝全屏请求。 */ }
  return () => {
    stopped = true;
    document.removeEventListener('fullscreenchange', changed);
    release();
  };
}
