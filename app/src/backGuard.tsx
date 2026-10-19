// 返回操作统一定义（原生系统返回 + Web 浏览器返回），优先级：弹窗 > 页面确认 > 主Tab双击退出。
// 原生：BackHandler 单监听；页面级确认由各屏 beforeRemove 实现（原生/Web 统一）。
// Web：popstate 时读取"尚未来得及更新的旧路由"判断用户按键意图：
//   弹窗开 → 关弹窗并 forward() 取消本次导航；主Tab → 双击返回离开站点。
import { useEffect, useRef } from 'react';
import { BackHandler, Platform } from 'react-native';
import { pushToast } from './toast';

const IS_WEB = Platform.OS === 'web';
const EXIT_WINDOW_MS = 2000;
const EXIT_TOAST = '再按一次返回退出 GameHub';

let getRouteName: () => string | undefined = () => undefined;
// 根判定：getCurrentRoute 返回叶子路由，主Tab的叶子是四个页签之一
const ROOT_TABS = new Set(['MainTabs', 'Games', 'Rooms', 'Social', 'Profile']);
const atRootTabs = () => ROOT_TABS.has(getRouteName() || '');
let lastExitPrompt = 0;

// ---------- 弹窗注册表 ----------
interface ModalEntry { id: number; close: () => void; }
const modalStack: ModalEntry[] = [];
let modalSeq = 0;

function closeTopModal(): void {
  modalStack.pop()?.close();
}

// ---------- Web 浏览器返回 ----------
// 对局页返回不直接离开：拦截 popstate（本监听必须在 linking 之前注册，模块加载即挂），
// stopImmediatePropagation 让 linking 感知不到这次返回，forward() 恢复条目，
// 再派发自定义事件让屏幕自己弹确认框——避免 preventDefault 造成 history/路由失同步。
// ⚠️ **漏一个对局屏不会有任何报错**，只会表现为「点了返回没反应、闪一下还在原处」：
// 不在这个集合里的路由，popstate 不会派发 `gh:confirm-leave`，按返回就只是**导航回
// 房间页**，而房间页又会因为「房间在游戏中且自己仍在局中」把人弹回对局页 ——
// 玩家看到的就是闪一下。深空休眠（`Stasis`）就这么漏了一整轮。
// `test/visual_audit.py` 的 `audit_back_wiring` 拿 navigation.tsx 的路由表逐个对账，
// **新增对局屏时那条静态检查会直接报红**，别再靠人肉记。
const PROTECTED_SCREENS = new Set(['Gomoku', 'Minesweeper', 'Reversi', 'Battleship', 'Dots', 'Ludo', 'Game2048', 'Xiangqi', 'ConnectFour', 'Tetris', 'Uno', 'Luzhanqi', 'Stasis']);
let suppressPop = false;
let suppressTimer: ReturnType<typeof setTimeout> | null = null;
let webListenerInstalled = false;

// 标记一次"程序化"的 history 遍历：随后的 popstate 直接放行给 linking 处理。
// 400ms 安全网：若该调用没有产生 popstate（如已处于栈顶/栈底），自动复位，
// 避免误吞下一次用户真实返回。
function markProgrammaticPop(): void {
  suppressPop = true;
  if (suppressTimer) clearTimeout(suppressTimer);
  suppressTimer = setTimeout(() => { suppressPop = false; suppressTimer = null; }, 400);
}

function consumeSuppress(): void {
  suppressPop = false;
  if (suppressTimer) { clearTimeout(suppressTimer); suppressTimer = null; }
}

function installWebHistory(): void {
  if (webListenerInstalled) return;
  webListenerInstalled = true;

  // 包装 history 遍历方法：react-navigation 的程序化导航（goBack/Tab切换的 go(-1)）
  // 会先更新路由状态再调 history.go——此时 popstate 里读到的已是"新路由"，
  // 若不区分会把"离开房间回到主Tab"误判成"用户在主Tab按返回"而弹退出提示。
  // 用户真实返回（浏览器按键/手势）不经过这些方法，suppressPop 保持 false，照常处理。
  const origGo = window.history.go.bind(window.history);
  const origBack = window.history.back.bind(window.history);
  const origForward = window.history.forward.bind(window.history);
  window.history.go = (delta?: number) => { markProgrammaticPop(); origGo(delta); };
  window.history.back = () => { markProgrammaticPop(); origBack(); };
  window.history.forward = () => { markProgrammaticPop(); origForward(); };

  window.addEventListener('popstate', (e) => {
    if (suppressPop) {
      consumeSuppress();
      return;
    }
    // 走到这里的一定是用户真实返回；React 尚未重渲染，读到的是返回前的路由
    const prevRoute = getRouteName() || '';
    if (modalStack.length > 0) {
      e.stopImmediatePropagation();
      closeTopModal();
      window.history.forward();  // 取消本次导航，留在原页面（经包装自动置 suppressPop）
      return;
    }
    if (PROTECTED_SCREENS.has(prevRoute)) {
      e.stopImmediatePropagation();
      window.history.forward();
      window.dispatchEvent(new CustomEvent('gh:confirm-leave'));
      return;
    }
    if (ROOT_TABS.has(prevRoute)) {
      const now = Date.now();
      if (now - lastExitPrompt < EXIT_WINDOW_MS) {
        // 再退一步：跨文档导航离开站点（Web 的"退出"语义；无更前条目时落到空白页）
        window.history.back();
        return;
      }
      lastExitPrompt = now;
      pushToast('提示', EXIT_TOAST);
      e.stopImmediatePropagation();
      window.history.forward();
      return;
    }
    // 其余为正常路由返回（如 Room → 主Tab），不干预
  });
}

if (IS_WEB) installWebHistory();

// ---------- 原生返回 ----------
export function initBackGuard(routeNameGetter: () => string | undefined): void {
  getRouteName = routeNameGetter;
  if (IS_WEB) {
    installWebHistory();
    return;
  }
  BackHandler.addEventListener('hardwareBackPress', () => {
    if (modalStack.length > 0) {
      closeTopModal();
      return true;
    }
    if (atRootTabs()) {
      const now = Date.now();
      if (now - lastExitPrompt < EXIT_WINDOW_MS) {
        BackHandler.exitApp();
        return true;
      }
      lastExitPrompt = now;
      pushToast('提示', EXIT_TOAST);
      return true;
    }
    return false;  // 交给导航：各页面 beforeRemove 弹确认框
  });
}

// 主Tab守卫条目：浏览器从主Tab返回是跨文档导航（上一条是 about:blank/外部页），
// popstate 不触发、无法拦截。因此在 Tab 条目之上垫一个同文档守卫条目，把"返回"
// 接住在本应用内。延时 push 是为了排在 linking 导航提交的 replaceState 之后。
export function useExitBackGuard(focused: boolean): void {
  const focusedRef = useRef(focused);
  focusedRef.current = focused;
  useEffect(() => {
    if (!IS_WEB || !focused) return;
    const t = setTimeout(() => {
      if (focusedRef.current && (window.history.state as any)?.__gh !== 'guard') {
        window.history.pushState({ __gh: 'guard' }, '');
      }
    }, 0);
    return () => clearTimeout(t);
  }, [focused]);
}

// 对局页注册"Web 浏览器返回→弹确认框"事件。
// 必须在原生端返回 no-op：RN 里 window === global 且没有 addEventListener，
// 直接调用会 TypeError → 整个页面白屏（v1.3.1 APP 五子棋白屏事故根因）。
export function onConfirmLeave(handler: () => void): () => void {
  if (!IS_WEB) return () => {};
  window.addEventListener('gh:confirm-leave', handler);
  return () => window.removeEventListener('gh:confirm-leave', handler);
}

// 网络恢复（浏览器 `online` 事件）：socket.ts 借它立刻重连。原生端 no-op。
export function onWebOnline(handler: () => void): () => void {
  if (!IS_WEB) return () => {};
  window.addEventListener('online', handler);
  return () => window.removeEventListener('online', handler);
}

// 网页端的键盘操作（俄罗斯方块、2048 这类「按方向」的对局屏共用）。原生端没有
// 物理键盘，什么也不做。处理器返回 true 表示「这个键我用了」→ preventDefault，
// 免得方向键 / 空格顺带把页面滚走。输入框里的按键、带 Ctrl/Alt/Meta 的组合键
// 一律不管（不抢浏览器快捷键）。单字符键统一成小写传进来。
export function useWebKeys(
  handler: (key: string, e: KeyboardEvent) => boolean | void,
  enabled = true,
): void {
  const ref = useRef(handler);   // 处理器每次渲染都是新闭包；监听只挂一次
  ref.current = handler;
  useEffect(() => {
    if (!IS_WEB || !enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.altKey || e.metaKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (ref.current(key, e)) e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled]);
}

// 弹窗打开期间，返回键优先关闭弹窗
export function useModalBackGuard(visible: boolean, close: () => void): void {
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    if (!visible) return;
    const id = ++modalSeq;
    modalStack.push({ id, close: () => closeRef.current() });
    return () => {
      const idx = modalStack.findIndex((m) => m.id === id);
      if (idx >= 0) modalStack.splice(idx, 1);
    };
  }, [visible]);
}
