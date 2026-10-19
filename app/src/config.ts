// 服务端地址：**构建期**由环境变量注入，仓库里不存任何具体地址。
//
// expo 在打包时把 `EXPO_PUBLIC_*` 字面量内联进 bundle（SDK 49+ 的行为），
// 所以这两个变量必须在**起 web dev server / 打 APK 之前**设好——之后再改进程
// 环境对已经打好的包没有影响。运维脚本会自己推导并导出：
//   scripts/start.sh   → 起 web 前导出（值取自 PUBLIC_BASE / IP / config.json）
//   bin/release-apk.sh → 打 APK 前导出，取不到地址直接报错退出
// 直接 `npx expo start`（不经脚本）时回落到 localhost，供本机开发用。
const DEFAULT_API_BASE = 'http://localhost:50541';

function trimSlash(url: string): string {
  return url.replace(/\/+$/, '');
}

export const API_BASE = trimSlash(
  process.env.EXPO_PUBLIC_API_BASE || DEFAULT_API_BASE,
);

// http → ws、https → wss
export const WS_BASE = trimSlash(
  process.env.EXPO_PUBLIC_WS_BASE || API_BASE.replace(/^http/, 'ws'),
);
