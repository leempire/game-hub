/** Derive public identity labels only; never infer moves, winners or private information. */
import type { Room } from './api';
import type { ShellPlayer } from './GameShell';
import { colors, ludoColors, playerColors, xiangqiBlack, xiangqiRed } from './theme';

interface IdentityState {
  your_color?: string | null;
  next?: string | number;
  current_seat?: number;
  turn?: number | null;
  winner?: string | number | null;
  finished?: boolean;
  phase?: string;
  seat_colors?: string[];
  seat_uids?: number[];
  seat_teams?: (number | null)[];
  players?: Record<string, unknown>;
}

const seatColors = ['blue', 'red', 'green', 'yellow'];

export function shellPlayers(room: Pick<Room, 'game' | 'players'> | null, userId: number | undefined,
  state: IdentityState | null): ShellPlayer[] {
  if (!room) return [];
  const ended = !!state?.finished || (state?.winner != null && state.winner !== '');
  const turnSeat = state?.current_seat ?? state?.turn;
  const pairs = room.players.length === 2;
  const mine = state?.your_color;
  return room.players.map((player, seat) => {
    const payloadSeat = state?.seat_uids?.indexOf(player.id);
    const currentSeat = payloadSeat != null && payloadSeat >= 0 ? payloadSeat : seat;
    const isMe = player.id === userId;
    let identity = `座位 ${seat + 1}`;
    let color = playerColors[seatColors[seat % seatColors.length]];
    let active = !ended && currentSeat === turnSeat;
    if (pairs && mine && ['gomoku', 'reversi', 'battleship', 'dots', 'connect4', 'xiangqi', 'luzhanqi', 'uttt'].includes(room.game)) {
      const first = ['xiangqi', 'luzhanqi'].includes(room.game) ? 'red' : room.game === 'battleship' ? 'blue' : 'black';
      const second = first === 'red' ? 'black' : first === 'blue' ? 'red' : 'white';
      const side = isMe ? mine : mine === first ? second : first;
      active = !ended && (room.game === 'dots' ? (side === 'black' ? 1 : 2) === state?.next : side === state?.next);
      if (room.game === 'uttt') {
        identity = side === 'black' ? 'X' : 'O';
        color = side === 'black' ? playerColors.blue : playerColors.red;
      } else if (room.game === 'dots' || room.game === 'battleship') {
        identity = side === first ? '蓝方' : '红方';
        color = side === first ? playerColors.blue : playerColors.red;
      } else if (first === 'red') {
        identity = side === 'red' ? '红方' : '黑方';
        color = side === 'red' ? xiangqiRed : xiangqiBlack;
      } else {
        identity = side === 'black' ? '黑棋' : '白棋';
        color = side === 'black' ? colors.stoneBlack : colors.borderStrong;
      }
      if (state?.phase === 'placing' || state?.phase === 'setup') active = false;
    } else if (room.game === 'ludo') {
      const entry = state?.players?.[String(currentSeat)];
      const key = entry && typeof entry === 'object' && 'color' in entry ? entry.color : null;
      if (typeof key === 'string') {
        identity = `${({ blue: '蓝', red: '红', green: '绿', yellow: '黄' } as Record<string, string>)[key] || key}方`;
        color = ludoColors[key] || color;
      }
    } else if (room.game === 'uno' && state?.seat_colors) {
      const key = state.seat_colors[currentSeat];
      color = playerColors[key] || color;
    }
    const team = state?.seat_teams?.[currentSeat];
    const difficulty = player.is_bot ? ({ easy: '简单', normal: '普通', hard: '困难' } as Record<string, string>)[player.ai_difficulty || 'normal'] : undefined;
    return { id: player.id, name: player.nickname || player.username || `玩家 ${seat + 1}`, isMe,
      isBot: player.is_bot, connected: player.connected, identity, color, active,
      detail: [difficulty, team != null ? `第 ${team + 1} 队` : null].filter(Boolean).join(' · ') || undefined };
  });
}
