import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { colors, fontSize, radius, spacing } from './theme';
import { undercoverRoleLabels, type UndercoverSnapshot, type UndercoverView } from './undercover';

export function UndercoverEliminations({ eliminations, name }: {
  eliminations: NonNullable<UndercoverSnapshot['eliminations']>;
  name: (id: number) => string;
}) {
  return <>
    {eliminations.map(({ round, user_id, role, reason }) =>
      <Text key={user_id} style={styles.detail}>
        {name(user_id)} · {undercoverRoleLabels[role]} · 第 {round} 轮{reason === 'vote' ? '被投出' : reason === 'disconnect' ? '断线超时' : '离场'}
      </Text>)}
  </>;
}

export function UndercoverTallies({ tallies, name }: {
  tallies: UndercoverView['tallies'];
  name: (id: number) => string;
}) {
  return <>
    {[...tallies].reverse().map(({ round, runoff, votes }) => (
      <View key={`${round}-${runoff}`} style={styles.tally}>
        <Text style={styles.label}>第 {round} 轮{runoff ? '重投' : ''}</Text>
        <Text style={styles.detail}>{votes.map(({ user_id, count }) => `${name(user_id)} ${count} 票`).join(' · ') || '无有效票'}</Text>
      </View>
    ))}
  </>;
}

export default function UndercoverSummary({ summary, name, testID }: {
  summary: UndercoverSnapshot;
  name: (id: number) => string;
  testID?: string;
}) {
  return <View style={styles.summary} testID={testID}>
    <Text style={styles.detail}>第 {summary.round} 轮结束</Text>
    {summary.word_pair && <View style={styles.words}>
      <View style={styles.wordTile}>
        <Text style={styles.detail}>平民词</Text><Text style={styles.word}>{summary.word_pair.civilian}</Text>
      </View>
      <View style={styles.wordTile}>
        <Text style={styles.detail}>卧底词</Text><Text style={styles.word}>{summary.word_pair.undercover}</Text>
      </View>
    </View>}
    {summary.roles?.map(({ user_id, role }) =>
      <Text key={user_id} style={styles.detail}>{name(user_id)} · {undercoverRoleLabels[role]}</Text>)}
    {!!summary.eliminations?.length && <View style={styles.eliminations}>
      <Text style={styles.label}>出局记录</Text>
      <UndercoverEliminations eliminations={summary.eliminations} name={name} />
    </View>}
    {summary.tallies && summary.tallies.length > 0 && <View style={styles.eliminations}>
      <Text style={styles.label}>公布票数</Text>
      <UndercoverTallies tallies={summary.tallies} name={name} />
    </View>}
  </View>;
}

const styles = StyleSheet.create({
  summary: { gap: spacing.xs },
  words: { flexDirection: 'row', gap: spacing.sm, marginVertical: spacing.xs },
  wordTile: { flex: 1, minWidth: 0, gap: spacing.xs, padding: spacing.sm,
    borderRadius: radius.md, backgroundColor: colors.surfaceMuted },
  word: { color: colors.text, fontSize: fontSize.xl, fontWeight: '800' },
  label: { color: colors.text, fontSize: fontSize.sm, fontWeight: '700' },
  detail: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 20 },
  eliminations: { gap: spacing.xs, paddingTop: spacing.sm },
  tally: { gap: 2, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: spacing.xs },
});
