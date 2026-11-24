/** Public UNO table: exposed discard and counts only, with no gameplay handlers. */
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { Room } from './api';
import { UnoCardView } from './UnoCardView';
import { UNO_COLOR_NAMES } from './uno';
import { colors, fontSize, radius, spacing, unoOnTable, unoOnTableMuted, unoTable } from './theme';

export interface PublicUno {
  game: 'uno';
  seat_uids: number[];
  hand_counts: Record<string, number>;
  discard_top: string | null;
  current_color: string;
  turn: number | null;
  next_seat?: number | null;
  pending_draw: number;
  out: number[];
  finished: boolean;
  winner?: number | null;
  winner_team?: number | null;
  draw_pile_count?: number;
}

export default function WatchUnoTable({ game, room }: { game: PublicUno; room: Room }) {
  const seatName = (seat: number) => {
    const player = room.players.find(p => p.id === game.seat_uids[seat]);
    return player?.nickname || player?.username || `座位 ${seat + 1}`;
  };
  const next = game.finished || game.turn == null ? null : game.next_seat;
  return <View style={styles.table} testID="watch-uno-table">
    <Text style={styles.title}>公共牌桌</Text>
    <Text style={styles.note}>只显示弃牌与手牌张数，不显示任何人的手牌</Text>
    <Text style={styles.status} testID="watch-uno-turn">
      {game.finished ? '本局已结束' : game.turn == null ? '等待下一回合' : `${seatName(game.turn)} 行动中`}
      {next != null && ` → 下家 ${seatName(next)}`}
    </Text>
    <View style={styles.discard}>
      {game.discard_top ? <UnoCardView card={game.discard_top} width={88} height={132}
        chosenColor={game.current_color} /> : <Text style={styles.note}>暂无弃牌</Text>}
    </View>
    <Text style={styles.status}>当前颜色：{UNO_COLOR_NAMES[game.current_color] || '待选择'}</Text>
    <Text style={styles.note}>牌堆 {game.draw_pile_count ?? 0} 张</Text>
    {game.pending_draw > 0 && <Text style={styles.status}>待罚 {game.pending_draw} 张</Text>}
    <View style={styles.counts}>
      {room.players.map(player => {
        const seat = game.seat_uids.indexOf(player.id);
        return <View key={player.id} style={styles.count} testID={`watch-uno-count-${player.id}`}>
          <Text style={styles.name}>{player.nickname || player.username}</Text>
          <Text style={styles.countText}>{game.out?.includes(seat) ? '已出局' : `${game.hand_counts[String(seat)] ?? 0} 张牌`}</Text>
        </View>;
      })}
    </View>
  </View>;
}

const styles = StyleSheet.create({
  table: { backgroundColor: unoTable, borderRadius: radius.lg, padding: spacing.md, gap: spacing.sm,
    alignItems: 'center', alignSelf: 'stretch' },
  title: { color: unoOnTable, fontSize: fontSize.xl, fontWeight: '700' },
  note: { color: unoOnTableMuted, fontSize: fontSize.sm, textAlign: 'center', lineHeight: 21 },
  status: { color: unoOnTable, fontSize: fontSize.md, fontWeight: '600', textAlign: 'center' },
  discard: { paddingVertical: spacing.sm },
  counts: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignSelf: 'stretch' },
  count: { flexGrow: 1, flexBasis: '40%', backgroundColor: colors.card, borderRadius: radius.md, padding: spacing.sm },
  countText: { color: colors.textSecondary, fontSize: fontSize.sm, textAlign: 'center' },
  name: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600', textAlign: 'center' },
});
