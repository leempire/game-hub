// 断线重连倒计时（各对局屏共用）。
//
// 秒数**由服务端下发**，客户端不写死 120：
//   - `player_disconnected` 消息带 `timeout`（本次断线的总时长）
//   - 房间数据里每个玩家带 `reconnect_left`（重连后拿到的是**剩余**值而不是总时长，
//     所以刷新页面/换设备回来看到的倒计时也是准的）
// 测试会把服务端超时调成 5 秒，写死就会和实际判负时间对不上。
import { useEffect, useState } from 'react';

/** 服务端没给值时的兜底，与 room_manager 的默认 _disconnect_timeout 一致。 */
export const DEFAULT_DISCONNECT_SECONDS = 120;

/**
 * 断线倒计时。`active` 为真时从 `seconds` 开始每秒递减到 0，返回剩余秒数。
 *
 * `seconds` 变化（服务端给了新的权威剩余值）会重开倒计时——这是有意为之：
 * 中途重连/重发 sync 时以服务端为准，本地计时不会越跑越偏。
 */
export function useDisconnectCountdown(
  active: boolean,
  seconds?: number | null,
): number {
  const start = seconds ?? DEFAULT_DISCONNECT_SECONDS;
  const [left, setLeft] = useState(start);
  useEffect(() => {
    if (!active) return;
    setLeft(start);
    const timer = setInterval(() => setLeft((v) => (v > 0 ? v - 1 : 0)), 1000);
    return () => clearInterval(timer);
  }, [active, start]);
  return left;
}
