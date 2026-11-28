export interface SoupRequest {
  id: string;
  user_id: number;
  kind: 'ask' | 'guess';
  text: string;
  status: 'pending' | 'answered' | 'failed';
  answer: '是' | '否' | '不确定' | null;
  correct: boolean | null;
  error: string | null;
  hint?: string;
}

export interface SoupView {
  game: 'turtle_soup';
  match_id: string;
  revision: number;
  phase: 'playing' | 'finished';
  title: string;
  surface: string;
  player_ids: number[];
  active: number[];
  requests: SoupRequest[];
  busy: boolean;
  ai_enabled: boolean;
  can_submit: boolean;
  can_reveal: boolean;
  request_limit: number;
  question_max_length: number;
  guess_max_length: number;
}

export interface SoupSource {
  name: string;
  url: string;
  license?: string;
  license_url?: string;
}

export interface SoupSnapshot {
  game: 'turtle_soup';
  title: string;
  surface: string;
  solution?: string;
  source?: SoupSource;
  winner: string;
  reason: string;
  solved_by?: number | null;
  request_count: number;
  player_ids?: number[];
  forfeited?: number[];
}

export interface SoupResult {
  type: 'game_over';
  game: 'turtle_soup';
  match_id: string;
  winner: string;
  reason: string;
  summary: SoupSnapshot;
}

export const soupReasons: Record<string, string> = {
  solved: '已还原核心真相', revealed: '房主结束并揭晓', limit: '已达到本局提交次数上限',
  payer_left: '支付人已离开，本局中止',
};
