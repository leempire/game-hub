import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { colors, playerColors, withAlpha, lastMoveMark, lastMoveMarkEdge, radius } from './theme';
import { UtttColor, UTTT_BOARD_NAMES, utttSymbol } from './uttt';

export interface UtttBoardProps {
  boards: number[][];
  local_results: number[];
  size?: number;
  activeBoard?: number | null;
  focus?: number;
  pending?: { board: number; cell: number; color?: UtttColor } | null;
  nextBoard?: number | null;
  lastMove?: { board: number; cell: number } | null;
  eligibleBoards?: number[];
  onSelect?: (board: number) => void;
}

export default function UtttBoard({
  boards, local_results, size = 246, activeBoard, focus, pending, nextBoard, lastMove,
  eligibleBoards = [], onSelect,
}: UtttBoardProps) {
  const mini = (size - 8) / 3;
  const cell = (mini - 10) / 3;
  return (
    <View testID="uttt-overview" style={{ width: size, height: size, gap: 4 }}>
      {[0, 1, 2].map(row => (
        <View key={row} style={styles.boardRow}>
          {[0, 1, 2].map(col => {
            const board = row * 3 + col;
            const result = local_results[board];
            const owner = result === 3 ? '和' : utttSymbol(result);
            const selected = focus === board;
            const destination = nextBoard === board;
            const label = `${UTTT_BOARD_NAMES[board]}小盘，${owner ? `${owner}${result === 3 ? '棋封盘' : ' 已赢下'}` : eligibleBoards.includes(board) ? '可以落子' : '查看盘面'}`;
            return (
              <TouchableOpacity key={board} testID={`uttt-board-${board}`} activeOpacity={0.7}
                accessibilityRole={onSelect ? 'button' : undefined} accessibilityLabel={label}
                accessibilityState={{ selected }} disabled={!onSelect} onPress={() => onSelect?.(board)}
                style={[styles.mini, { width: mini, height: mini },
                  result > 0 && styles.closed, selected && styles.focus,
                  activeBoard === board && { borderColor: colors.primary, borderWidth: 2 },
                  destination && { borderColor: colors.warning, borderStyle: 'dashed', borderWidth: 2 }]}>
                {[0, 1, 2].map(r => (
                  <View key={r} style={styles.row}>
                    {[0, 1, 2].map(c => {
                      const index = r * 3 + c;
                      const preview = pending?.board === board && pending.cell === index;
                      const value = boards[board]?.[index] || (preview ? pending.color || 'black' : 0);
                      return (
                        <View key={c} style={[styles.cell, { width: cell, height: cell },
                          c < 2 && styles.rightLine, r < 2 && styles.bottomLine]}>
                          <Text style={{ fontSize: Math.max(10, cell * 0.75), fontWeight: '700',
                            opacity: preview ? 0.5 : 1, color: value === 1 || value === 'black' ? playerColors.blue : playerColors.red }}>
                            {utttSymbol(value)}
                          </Text>
                          {lastMove?.board === board && lastMove.cell === index && <View style={styles.lastMove} />}
                        </View>
                      );
                    })}
                  </View>
                ))}
                {!!owner && <View pointerEvents="none" style={styles.owner}>
                  <Text style={{ fontSize: mini * 0.6, fontWeight: '800',
                    color: result === 1 ? playerColors.blue : result === 2 ? playerColors.red : colors.textSecondary }}>
                    {owner}
                  </Text>
                </View>}
                {destination && <View pointerEvents="none" style={styles.destination}><Text style={styles.destinationText}>下一盘</Text></View>}
              </TouchableOpacity>
            );
          })}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: 0 },
  boardRow: { flexDirection: 'row', gap: 4 },
  mini: { padding: 3, borderWidth: 2, borderColor: colors.borderStrong, borderRadius: radius.sm, backgroundColor: colors.card, justifyContent: 'center', alignItems: 'center' },
  closed: { backgroundColor: colors.bg },
  focus: { backgroundColor: withAlpha(colors.primary, 0.1), borderColor: colors.primary },
  cell: { justifyContent: 'center', alignItems: 'center' },
  rightLine: { borderRightWidth: 1, borderRightColor: colors.border },
  bottomLine: { borderBottomWidth: 1, borderBottomColor: colors.border },
  owner: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, justifyContent: 'center', alignItems: 'center', backgroundColor: withAlpha(colors.card, 0.82), borderRadius: radius.sm },
  lastMove: { position: 'absolute', right: 1, top: 1, width: 6, height: 6, borderRadius: 3, backgroundColor: lastMoveMark, borderWidth: 1, borderColor: lastMoveMarkEdge },
  destination: { position: 'absolute', bottom: 0, backgroundColor: colors.warning, paddingHorizontal: 3, borderRadius: 2 },
  destinationText: { color: colors.white, fontSize: 10, fontWeight: '700' },
});
