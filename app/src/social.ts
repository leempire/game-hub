/** Teaching, read-only spectators and fixed phrases; no free-form chat transport. */
import { apiFetch, type Room } from './api';
export interface QuickPhrase { id: string; text: string }
export interface QuickMessage { type: 'quick_chat'; id: string; user_id: number; name: string; text: string }
export interface WatchGrant { grant: string; ws_path: string; game: string }
export interface WatchState { read_only: true; room: Room; game: Record<string, unknown> | null }
export const createPractice = (token: string, game: string) => apiFetch<{ code: string; room: Room; reused: boolean }>(
  `/api/practice/${encodeURIComponent(game)}`, { method: 'POST' }, token);
export const requestWatch = (token: string, code: string, password?: string) => apiFetch<WatchGrant>(
  `/api/rooms/${encodeURIComponent(code)}/watch`, { method: 'POST', body: JSON.stringify({ password: password || null }) }, token);
export const sayPhrase = (token: string, code: string, id: string) => apiFetch<{ ok: boolean; message: QuickMessage; cooldown_seconds: number }>(
  `/api/rooms/${encodeURIComponent(code)}/quick-chat`, { method: 'POST', body: JSON.stringify({ id }) }, token);
