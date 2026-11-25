import { Platform } from 'react-native';
import * as LegacyFS from 'expo-file-system/legacy';
import * as IntentLauncher from 'expo-intent-launcher';
import * as Application from 'expo-application';
import Constants from 'expo-constants';
import { API_BASE } from './config';
import { notify } from './utils';

export interface VersionInfo {
  version_code: number;
  version_name: string;
  download_url: string;
  has_update: boolean;
}

export interface DownloadProgress {
  /** 已下载字节（含断点续传前的部分） */
  written: number;
  /** 总字节；服务端没给 Content-Length 时为 -1 */
  total: number;
  /** 0–100；total 未知时为 -1 */
  percent: number;
}

export interface ApkDownloadHandle {
  /** 下载完成后 resolve 为本地 file:// 路径 */
  promise: Promise<string>;
  /** 取消下载；已下载的部分保留，下次从断点继续 */
  cancel: () => void;
}

// APK 有 80 MB 量级，移动网络下「下载中」可能持续几十秒到几分钟。
// 旧实现用 downloadAsync：没有进度、没有超时、断线只能整包重下 ——
// 一旦网络抖动，Promise 会长时间不落地，界面永远停在「下载中」。
// 现在改为可续传下载 + 进度回调 + 停滞看门狗 + 自动重试。
const DOWNLOAD_DIR_NAME = 'gamehub-update';
/** 多久没有新字节就判定为网络停滞（服务端一次字节都不发）。 */
const STALL_TIMEOUT_MS = 30_000;
const WATCHDOG_TICK_MS = 4_000;
const MAX_ATTEMPTS = 3;

export class DownloadCancelledError extends Error {
  constructor() {
    super('已取消下载');
    this.name = 'DownloadCancelledError';
  }
}

class DownloadInterruptedError extends Error {
  /** true = 本地缓存已不可用（Range 越界 / 大小不符），下次必须从 0 重下 */
  readonly restartFromZero: boolean;

  constructor(message: string, restartFromZero = false) {
    super(message);
    this.name = 'DownloadInterruptedError';
    this.restartFromZero = restartFromZero;
  }
}

/** 下载失败时给用户看的中文说明（不要把 native 的英文异常直接甩给用户）。 */
export function describeDownloadError(err: unknown): string {
  if (err instanceof DownloadCancelledError) return '已取消下载';
  const msg = (err as Error)?.message || '';
  if (/timeout|timed out|stall/i.test(msg)) {
    return '下载超时（30 秒没有收到新数据）。请换到更稳定的网络后重试，已下载的部分会继续使用。';
  }
  if (/network|connection|reset|econn|unreachable|socket|interrupt|incomplete/i.test(msg)) {
    return '网络中断，下载没有完成。请检查网络后重试，已下载的部分会继续使用。';
  }
  if (/space|storage|no space/i.test(msg)) {
    return '手机存储空间不足，无法保存更新包。清理空间后重试。';
  }
  return msg ? `下载失败：${msg}` : '下载失败，请稍后重试';
}

// Get current app version
export function getCurrentVersion(): { versionCode: number; versionName: string } {
  if (Platform.OS === 'web') {
    return { versionCode: 0, versionName: 'web' };
  }
  const vc = Constants.expoConfig?.android?.versionCode
    || (Constants as any).manifest?.android?.versionCode
    || 0;
  const vn = Constants.expoConfig?.version
    || (Constants as any).manifest?.version
    || '1.0.0';
  return { versionCode: vc, versionName: vn };
}

// Check server for latest version
export async function checkLatestVersion(): Promise<VersionInfo> {
  const res = await fetch(`${API_BASE}/api/app/version`);
  if (!res.ok) {
    throw new Error(`Failed to check version: ${res.status}`);
  }
  return res.json();
}

function apkFileUri(versionCode: number): string {
  // 文件名带版本号：新版本重新下载，不会把上一版的残留当成断点续传
  return `${LegacyFS.cacheDirectory}${DOWNLOAD_DIR_NAME}-${versionCode}.apk`;
}

async function downloadedBytes(fileUri: string): Promise<number> {
  const info = await LegacyFS.getInfoAsync(fileUri);
  return info.exists ? (info.size ?? 0) : 0;
}

/** 服务端文件大小（拿不到就返回 0，不影响下载，只影响续传前的校验）。 */
async function remoteBytes(url: string): Promise<number> {
  try {
    const res = await fetch(url, { method: 'HEAD' });
    const len = Number(res.headers.get('content-length') || 0);
    return Number.isFinite(len) && len > 0 ? len : 0;
  } catch {
    return 0;
  }
}

/**
 * 启动 APK 下载（Android）。可按 handle.cancel() 取消并保留已下载部分；
 * 网络中断 / 停滞会自动从断点续传，最多 3 次。
 */
export function downloadApk(
  downloadUrl: string,
  versionCode: number,
  onProgress?: (progress: DownloadProgress) => void,
): ApkDownloadHandle {
  let cancelled = false;
  let active: LegacyFS.DownloadResumable | null = null;

  const promise = (async () => {
    if (Platform.OS !== 'android') {
      throw new Error('此功能仅在 Android 应用内可用');
    }
    const fileUri = apkFileUri(versionCode);
    const expected = await remoteBytes(downloadUrl);
    let local = await downloadedBytes(fileUri);

    // 上次已经下完了（用户取消安装后再次点下载）：直接复用，不再发 Range 请求
    if (expected > 0 && local === expected) {
      onProgress?.({ written: local, total: expected, percent: 100 });
      return fileUri;
    }
    // 本地缓存比服务端还大 —— 服务端换过包，旧缓存不能当断点
    if (expected > 0 && local > expected) {
      await LegacyFS.deleteAsync(fileUri, { idempotent: true }).catch(() => {});
      local = 0;
    }

    let lastError: Error | null = null;
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      if (cancelled) throw new DownloadCancelledError();
      const offset = await downloadedBytes(fileUri);
      try {
        return await runDownloadAttempt(
          downloadUrl,
          fileUri,
          offset,
          onProgress,
          (task) => { active = task; },
          () => cancelled,
        );
      } catch (err) {
        lastError = err as Error;
        if (cancelled) throw new DownloadCancelledError();
        if (err instanceof DownloadInterruptedError && err.restartFromZero) {
          await LegacyFS.deleteAsync(fileUri, { idempotent: true }).catch(() => {});
        }
      }
    }

    throw new DownloadInterruptedError(
      lastError?.message
        ? `下载多次中断（${lastError.message}）。请换到更稳定的网络后重试。`
        : '下载多次中断，请换到更稳定的网络后重试。',
    );
  })();

  return {
    promise,
    cancel: () => {
      cancelled = true;
      const task = active;
      if (task) void task.pauseAsync().catch(() => {});
    },
  };
}

async function runDownloadAttempt(
  url: string,
  fileUri: string,
  offset: number,
  onProgress: ((progress: DownloadProgress) => void) | undefined,
  registerTask: (task: LegacyFS.DownloadResumable) => void,
  isCancelled: () => boolean,
): Promise<string> {
  let lastProgressAt = Date.now();
  let expectedTotal = 0;

  const task = LegacyFS.createDownloadResumable(
    url,
    fileUri,
    {},
    (data) => {
      lastProgressAt = Date.now();
      const total = data.totalBytesExpectedToWrite || -1;
      const written = data.totalBytesWritten || 0;
      expectedTotal = total;
      onProgress?.({
        written,
        total,
        percent: total > 0 ? Math.min(100, Math.floor((written * 100) / total)) : -1,
      });
    },
    offset > 0 ? String(offset) : undefined,
  );
  registerTask(task);

  // 看门狗：30 秒没有新字节就暂停当前请求，交给外层从断点重试。
  // 这样「网络半死」的情况不会再无限停在「下载中」。
  let stallTriggered = false;
  const watchdog = setInterval(() => {
    if (stallTriggered) return;
    if (Date.now() - lastProgressAt >= STALL_TIMEOUT_MS) {
      stallTriggered = true;
      void task.pauseAsync().catch(() => {});
    }
  }, WATCHDOG_TICK_MS);

  try {
    const result = offset > 0 ? await task.resumeAsync() : await task.downloadAsync();
    if (!result) {
      if (isCancelled()) throw new DownloadCancelledError();
      throw new DownloadInterruptedError(
        stallTriggered ? '下载停滞 30 秒' : '下载被中断',
      );
    }
    // 断点续传时服务端返回 206，完整下载返回 200；416 = 本地断点已越界
    if (result.status === 416) {
      throw new DownloadInterruptedError('断点已失效', true);
    }
    if (result.status !== 200 && result.status !== 206) {
      throw new DownloadInterruptedError(`服务器返回 ${result.status}`);
    }
    const size = await downloadedBytes(result.uri);
    if (size <= 0 || (expectedTotal > 0 && size < expectedTotal)) {
      throw new DownloadInterruptedError('更新包不完整', true);
    }
    return result.uri;
  } finally {
    clearInterval(watchdog);
  }
}

/** 拉起系统安装器；用户需在系统设置里允许「安装未知应用」。 */
export async function installApk(fileUri: string): Promise<void> {
  const contentUri = await LegacyFS.getContentUriAsync(fileUri);

  try {
    await IntentLauncher.startActivityAsync('android.intent.action.VIEW', {
      data: contentUri,
      type: 'application/vnd.android.package-archive',
      flags: 1, // FLAG_GRANT_READ_URI_PERMISSION
    });
  } catch (err) {
    const errMsg = (err as Error).message || '';
    if (errMsg.includes('REQUEST_INSTALL_PACKAGES') || errMsg.includes('unknown sources')) {
      await promptUnknownSources();
    } else {
      throw err;
    }
  }
}

// Open settings to allow unknown app sources
async function promptUnknownSources(): Promise<void> {
  const packageName = Application.applicationId || 'com.gamehub.app';
  try {
    await IntentLauncher.startActivityAsync(
      'android.settings.MANAGE_UNKNOWN_APP_SOURCES',
      { data: `package:${packageName}` }
    );
    notify('提示', '请允许安装未知来源应用后，再次点击"检查更新"');
  } catch {
    notify('提示', '请在系统设置中允许安装未知来源应用');
  }
}