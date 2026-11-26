import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import PlayingCardSuit from './PlayingCardSuit';
import { pokerStyles } from './PokerTable';
import { doudizhuColors as ink, radius, spacing } from './theme';
import { AppModal } from './ui';

export interface PokerPlayRecord<Play> {
  player_id: number;
  play: Play | null;
  deal?: number;
  level?: string;
}

interface DescribedPlay {
  label: string;
  cards: { rank: string; suit?: string; red: boolean; label: string }[];
}

export default function PokerPlayHistory<Play>({ game, records, name, describe, compact, result }: {
  game: 'guandan' | 'doudizhu'; records?: PokerPlayRecord<Play>[]; name: (uid: number) => string;
  describe: (play: Play, level?: string) => DescribedPlay; compact?: boolean; result?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rows = (id: string, expanded: boolean) => <ScrollView testID={id} accessibilityLabel="出牌历史"
    nestedScrollEnabled tabIndex={0} style={[styles.feed, expanded ? styles.expanded : compact && styles.feedCompact]}
    contentContainerStyle={[styles.feedContent, compact && !expanded && styles.feedContentCompact]}>
    {records?.length ? records.map((record, index) => {
      const play = record.play && describe(record.play, record.level);
      const label = play ? play.label : game === 'guandan' ? '过牌' : '不出';
      const detail = `${record.deal ? `第 ${record.deal} 副 · ` : ''}${name(record.player_id)}`;
      const dense = compact && !expanded;
      return <View key={index} style={[styles.row, dense && styles.rowCompact]}>
        <View style={styles.heading}>
          <Text style={[pokerStyles.muted, styles.player]} numberOfLines={expanded ? undefined : 1}>{detail}</Text>
          {(!play || dense) && <Text style={styles.action} numberOfLines={1}>{label}</Text>}
        </View>
        {play && <>
          {!dense && <Text style={styles.description}>{label}</Text>}
          <View style={styles.cards} accessibilityLabel={play.cards.map(card => card.label).join('、')}>
            {play.cards.map((card, cardIndex) => {
              const color = card.red ? ink.red : ink.black;
              return <View key={cardIndex} style={pokerStyles.compactCard}>
                <Text style={[pokerStyles.compactRank, { color }]}>{card.rank}</Text>
                {!!card.suit && <PlayingCardSuit suit={card.suit} color={color} size={9} />}
              </View>;
            })}
          </View>
        </>}
      </View>;
    }).reverse() : <Text style={pokerStyles.muted}>暂无出牌</Text>}
  </ScrollView>;
  return <View style={[styles.panel, compact && styles.panelCompact]}>
    {result ? <Text accessibilityRole="header" style={styles.title}>出牌历史</Text>
      : <Pressable testID={`${game}-history-open`} accessibilityRole="button" accessibilityLabel="查看全部出牌历史"
        onPress={() => setOpen(true)} style={styles.open}>
        <Text style={styles.title}>{compact ? '出牌历史' : '出牌历史 · 查看全部'}</Text>
      </Pressable>}
    {rows(`${game}-history-feed`, !!result)}
    {open && <AppModal visible onClose={() => setOpen(false)} testID={`${game}-history-modal`}>
      <Text accessibilityRole="header" style={pokerStyles.pickerTitle}>出牌历史</Text>
      {rows(`${game}-history-expanded`, true)}
      <Pressable testID={`${game}-history-close`} accessibilityRole="button" onPress={() => setOpen(false)} style={pokerStyles.pickerClose}>
        <Text style={pokerStyles.pickerCloseLabel}>关闭</Text>
      </Pressable>
    </AppModal>}
  </View>;
}

const styles = StyleSheet.create({
  panel: { alignSelf: 'center', width: '100%', maxWidth: 440, minWidth: 0, paddingHorizontal: spacing.xs,
    backgroundColor: ink.panel, borderRadius: radius.md, borderWidth: 1, borderColor: ink.line },
  panelCompact: { width: '40%', maxWidth: 176 },
  open: { minHeight: 36, alignItems: 'center', justifyContent: 'center' },
  title: { color: ink.light, fontSize: pokerStyles.muted.fontSize, fontWeight: '700', textAlign: 'center' },
  feed: { maxHeight: 116, flexGrow: 0, borderRadius: radius.sm, backgroundColor: ink.panel },
  feedCompact: { maxHeight: 36 },
  expanded: { maxHeight: 212 },
  feedContent: { padding: spacing.xs, gap: spacing.xs },
  feedContentCompact: { paddingVertical: 0, gap: 2 },
  row: { gap: 2, borderBottomWidth: 1, borderBottomColor: ink.line, paddingBottom: spacing.xs },
  rowCompact: { gap: 1, paddingBottom: 1 },
  heading: { flexDirection: 'row', gap: spacing.xs, alignItems: 'baseline' },
  player: { flex: 1, minWidth: 0, fontWeight: '700' },
  action: { ...pokerStyles.muted, maxWidth: '55%' },
  description: { ...pokerStyles.muted, color: ink.light },
  cards: { flexDirection: 'row', flexWrap: 'wrap', gap: 2 },
});
