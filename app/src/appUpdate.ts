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

// Download and install APK
export async function downloadAndInstallApk(downloadUrl: string): Promise<void> {
  if (Platform.OS !== 'android') {
    notify('提示', '此功能仅在 Android 应用内可用');
    return;
  }

  notify('下载中', '正在下载更新...');

  // Download to cache directory (no storage permission needed)
  const fileUri = LegacyFS.cacheDirectory + 'gamehub-update.apk';
  const result = await LegacyFS.downloadAsync(downloadUrl, fileUri);

  if (!result || result.status !== 200) {
    throw new Error(`Download failed: ${result?.status || 'unknown'}`);
  }

  // Get content URI for the downloaded file
  const contentUri = await LegacyFS.getContentUriAsync(result.uri);

  // Launch installer
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
