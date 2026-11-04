/** Opaque replay links; anonymous reads intentionally do not send the login token. */
import { Platform } from 'react-native';
import { apiFetch, type GameRecord } from './api';
import { API_BASE } from './config';

export const fetchSharedReplay = (token: string) => apiFetch<GameRecord>(`/api/history/shared/${encodeURIComponent(token)}`);
export const revokeReplay = (token: string, recordId: number) => apiFetch<{ ok: boolean }>(`/api/history/${recordId}/share`, { method: 'DELETE' }, token);

function webBase(): string {
  if (Platform.OS === 'web' && typeof location !== 'undefined') return location.origin;
  if (process.env.EXPO_PUBLIC_WEB_BASE) return process.env.EXPO_PUBLIC_WEB_BASE.replace(/\/+$/, '');
  // The repository's default deployment uses adjacent API/web ports. Reverse
  // proxies and custom ports should set EXPO_PUBLIC_WEB_BASE when building APKs.
  const url = new URL(API_BASE);
  if (url.port === '50541') url.port = '50540';
  return url.origin;
}
export async function createReplayLink(token: string, recordId: number): Promise<string> {
  const result = await apiFetch<{ token: string; path: string }>(`/api/history/${recordId}/share`, { method: 'POST' }, token);
  const url = `${webBase()}${result.path}`;
  return url;
}
