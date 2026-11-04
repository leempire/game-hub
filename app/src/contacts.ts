/** Account contacts and durable direct-message API. Gameplay social.ts stays separate. */
import { apiFetch } from './api';

export interface ContactProfile { id: number; username: string; nickname: string | null }
export interface MessagePreview { id: number; sender_id: number; body: string; created_at: string }
export interface DirectMessage extends MessagePreview { client_id: string | null }
export interface ContactFriend extends ContactProfile { unread_count: number; last_message: MessagePreview | null }
export interface ContactRequest extends ContactProfile { created_at: string }
export interface SocialSnapshot {
  friends: ContactFriend[]; incoming: ContactRequest[]; outgoing: ContactRequest[];
  blocked: ContactProfile[]; unread_total: number;
}
export interface ContactSearch extends ContactProfile { relation: 'none' | 'friend' | 'incoming' | 'outgoing' }
export interface ChatPage { peer: ContactProfile; items: DirectMessage[]; has_more: boolean; peer_read_through: number }

async function request<T>(token: string, path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try { return await apiFetch<T>(`/api/social${path}`, { ...options, signal: controller.signal }, token); }
  catch (error) {
    if (controller.signal.aborted) throw new Error('连接超时，请检查网络后重试');
    throw error;
  } finally { clearTimeout(timer); }
}
export const displayContact = (person: ContactProfile) => person.nickname || person.username;
export const getSocialSummary = (token: string) => request<{ unread_total: number; incoming_total: number }>(token, '/summary');
export const getSocial = (token: string) => request<SocialSnapshot>(token, '');
export const searchContacts = (token: string, username: string) =>
  request<{ items: ContactSearch[] }>(token, `/users?q=${encodeURIComponent(username.trim())}`);
export const requestFriend = (token: string, userId: number) => request<{ status: string }>(token, '/requests', {
  method: 'POST', body: JSON.stringify({ user_id: userId }),
});
export const respondFriend = (token: string, userId: number, action: 'accept' | 'decline' | 'cancel') =>
  request<{ ok: boolean }>(token, `/requests/${userId}/${action}`, { method: 'POST' });
export const removeFriend = (token: string, userId: number) =>
  request<{ ok: boolean }>(token, `/friends/${userId}`, { method: 'DELETE' });
export const blockContact = (token: string, userId: number) =>
  request<{ ok: boolean }>(token, `/blocks/${userId}`, { method: 'POST' });
export const unblockContact = (token: string, userId: number) =>
  request<{ ok: boolean }>(token, `/blocks/${userId}`, { method: 'DELETE' });
export function getChat(token: string, userId: number, cursor: { before?: number; after?: number; limit?: number } = {}) {
  const query = Object.entries(cursor).filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}=${value}`).join('&');
  return request<ChatPage>(token, `/chats/${userId}${query ? `?${query}` : ''}`);
}
export const sendMessage = (token: string, userId: number, body: string, clientId: string) =>
  request<DirectMessage>(token, `/chats/${userId}`, { method: 'POST', body: JSON.stringify({ body, client_id: clientId }) });
export const markRead = (token: string, userId: number, throughId: number) =>
  request<{ through_id: number }>(token, `/chats/${userId}/read`, { method: 'POST', body: JSON.stringify({ through_id: throughId }) });
