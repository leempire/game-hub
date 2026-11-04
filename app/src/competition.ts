/** Competition transport; scores and challenge completion are server-authoritative. */
import { apiFetch, type Room } from './api';
export interface DailyGame {
  game: string; title: string; completed: boolean; streak: number;
  best_value: number | null; room_code?: string;
}
export interface DailyStatus { date: string; games: DailyGame[] }
export interface LeaderboardEntry {
  rank: number; user_id: number; username: string; nickname: string | null;
  value: number; record_id?: number;
}
export interface Leaderboard {
  game: string; metric: string; window: string; rules_label: string;
  rules_key: string; scopes: { key: string; label: string }[];
  challenge_date?: string | null; items: LeaderboardEntry[]; own: LeaderboardEntry | null;
}
export const todayChallenges = (token: string) => apiFetch<DailyStatus>('/api/challenges/today', {}, token);
export const startChallenge = (token: string, game: string) => apiFetch<Room>(`/api/challenges/${encodeURIComponent(game)}/start`, { method: 'POST' }, token);
export function leaderboard(token: string, game: string, metric: string, window: string,
  difficulty: string, date?: string, rulesKey?: string) {
  const query = new URLSearchParams({ metric, window, difficulty });
  if (date) query.set('challenge_date', date);
  if (rulesKey) query.set('rules_key', rulesKey);
  return apiFetch<Leaderboard>(`/api/leaderboards/${encodeURIComponent(game)}?${query}`, {}, token);
}
