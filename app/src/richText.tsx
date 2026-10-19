import React from 'react';
import { Text, TextStyle } from 'react-native';

/**
 * 把 `**强调**` 渲染成粗体段。
 *
 * 规则文案里一直写着 `**…**`（UNO、飞行棋、深空休眠、房间设置里的房规说明都有），
 * 但文字是整段丢进一个 `<Text>` 的，**星号原样显示**给玩家看 —— 一份规则里冒出
 * 七八个 `**` 很难看，也让人怀疑是不是加载坏了。这里按 `**` 切段，奇数段加粗。
 *
 * 不做完整的 Markdown（不需要，这些文案只有这一种标记）；单个 `*` 不处理。
 *
 * 抽成独立模块是因为**两个地方要用**（帮助弹窗 `help.tsx` 与房间设置抽屉
 * `roomSettings.tsx`），而让「设置抽屉」去 import「帮助模块」只为拿一个渲染函数，
 * 是一处说不清的耦合。`boldStyle` 由调用方给 —— 两处的正文样式本来就不一样。
 */
export function boldSegments(text: string, boldStyle?: TextStyle): React.ReactNode {
  const parts = text.split('**');
  if (parts.length === 1) return text;
  return parts.map((seg, i) => (
    // eslint-disable-next-line react/no-array-index-key
    <Text key={i} style={i % 2 === 1 ? boldStyle : undefined}>{seg}</Text>
  ));
}
