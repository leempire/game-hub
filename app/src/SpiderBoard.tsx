import React from 'react';
import { View, Text, TouchableOpacity, ScrollView, StyleSheet } from 'react-native';
import Svg, { Path } from 'react-native-svg';
import { colors, spiderTable } from './theme';
import { SpiderState, spiderRank } from './spider';

export function Spade({ size = 18, color = colors.text }: { size?: number; color?: string }) {
  return <Svg width={size} height={size} viewBox="0 0 24 24">
    <Path d="M12 2C9 6 3 8 3 13a5 5 0 0 0 8 4c0 2-1 3-3 4h8c-2-1-3-2-3-4a5 5 0 0 0 8-4c0-5-6-7-9-11Z" fill={color} />
  </Svg>;
}

interface Props {
  game: SpiderState;
  selected?: { source: number; index: number } | null;
  onPick?: (source: number, index: number) => void;
}

/** 明牌露出 44px，暗牌压缩；手机滚动看十列，避免缩成无法点击的小牌。 */
export default function SpiderBoard({ game, selected, onPick }: Props) {
  const targets = selected ? game.legal_moves.filter(m =>
    m.source === selected.source && m.index === selected.index).map(m => m.target) : [];
  return <ScrollView horizontal nestedScrollEnabled style={styles.scroller}
    contentContainerStyle={styles.table} testID="spider-board">
    {game.columns.map((column, source) => {
      let top = 0;
      const positions = column.map(rank => { const at = top; top += rank === null ? 12 : 44; return at; });
      const height = column.length ? positions[positions.length - 1] + 78 : 110;
      return <View key={source} style={[styles.column, { height: height + 44 }]}>
        <TouchableOpacity accessibilityRole={onPick ? 'button' : undefined}
          accessibilityLabel={`第${source + 1}列${targets.includes(source) ? '，可放置' : ''}`}
          onPress={() => onPick?.(source, column.length - 1)} style={styles.columnLabel}
          testID={`spider-column-${source}`}>
          <Text style={styles.columnText}>{source + 1}{targets.includes(source) ? ' 可放' : ''}</Text>
        </TouchableOpacity>
        <View style={[styles.slot, targets.includes(source) && styles.target, { height }]}>
          {!column.length && <TouchableOpacity style={styles.empty} onPress={() => onPick?.(source, 0)}
            accessibilityRole={onPick ? 'button' : undefined} accessibilityLabel={`第${source + 1}列空位`}
            testID={`spider-empty-${source}`}><Spade color={colors.white} /></TouchableOpacity>}
          {column.map((rank, index) => rank === null ?
            <View key={index} style={[styles.back, { top: positions[index] }]} /> :
            <TouchableOpacity key={index} onPress={() => onPick?.(source, index)}
              testID={`spider-card-${source}-${index}`} accessibilityRole={onPick ? 'button' : undefined}
              accessibilityLabel={`第${source + 1}列，第${index + 1}张，黑桃${spiderRank(rank)}`}
              accessibilityState={{ selected: selected?.source === source && index >= selected.index }}
              style={[styles.card, { top: positions[index] },
                selected?.source === source && index >= selected.index && styles.selected]}>
              <View style={styles.rankRow}><Text style={styles.rank}>{spiderRank(rank)}</Text><Spade size={16} /></View>
              <View style={styles.suit}><Spade size={23} /></View>
            </TouchableOpacity>)}
        </View>
      </View>;
    })}
  </ScrollView>;
}

const styles = StyleSheet.create({
  scroller: { flexGrow: 0, backgroundColor: spiderTable },
  table: { padding: 12, gap: 8, alignItems: 'flex-start' },
  column: { width: 54 },
  columnLabel: { height: 44, justifyContent: 'center', alignItems: 'center' },
  columnText: { color: colors.white, fontSize: 12, fontWeight: '700' },
  slot: { width: 54, borderRadius: 7, borderWidth: 1, borderColor: colors.borderStrong },
  target: { borderWidth: 3, borderColor: colors.white },
  empty: { width: 50, minHeight: 96, justifyContent: 'center', alignItems: 'center' },
  card: { position: 'absolute', left: -1, width: 54, height: 78, borderRadius: 6,
    borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.white },
  selected: { borderWidth: 3, borderColor: colors.primary, backgroundColor: colors.bg },
  rankRow: { minHeight: 42, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-evenly' },
  rank: { fontSize: 23, fontWeight: '800', color: colors.text },
  suit: { alignItems: 'center' },
  back: { position: 'absolute', left: -1, width: 54, height: 78, borderRadius: 6,
    backgroundColor: colors.primaryDark, borderWidth: 1, borderColor: colors.white },
});
