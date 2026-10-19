import { pushToast } from './toast';

// Unified notification - in-app toast (no alert/Alert.alert):
// non-blocking, auto-dismiss, consistent across web/native.
export function notify(title: string, message?: string): void {
  pushToast(title, message);
}

// Parse error detail from API response
export async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === 'string') return data.detail;
    if (typeof data.message === 'string') return data.message;
    return `请求失败 (${res.status})`;
  } catch {
    return `请求失败 (${res.status})`;
  }
}

// Format date string for display
export function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString('zh-CN');
  } catch {
    return iso;
  }
}
