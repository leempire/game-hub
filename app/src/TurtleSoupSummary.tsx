import React from 'react';
import { Linking, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { colors, fontSize, spacing } from './theme';
import type { SoupSnapshot } from './turtleSoup';
import { soupReasons } from './turtleSoup';

export default function TurtleSoupSummary({ summary, testID }: { summary: SoupSnapshot; testID?: string }) {
  return <View testID={testID} style={styles.wrap}>
    <Text style={styles.title}>{summary.title}</Text>
    <Text style={styles.body}>{summary.surface}</Text>
    <Text style={styles.note}>{soupReasons[summary.reason] ?? '本局结束'} · {summary.request_count} 次提交</Text>
    {summary.solution ? <>
      <Text style={styles.title}>汤底</Text>
      <Text style={styles.body} selectable>{summary.solution}</Text>
    </> : <Text style={styles.note}>汤底仅供本局参赛者在个人战绩中回看。</Text>}
    {summary.source && <TouchableOpacity accessibilityRole="link" style={styles.source}
      onPress={() => void Linking.openURL(summary.source!.url)}>
      <Text style={styles.note}>题目来源：{summary.source.name}</Text>
    </TouchableOpacity>}
    {summary.source?.license_url && <TouchableOpacity accessibilityRole="link" style={styles.source}
      onPress={() => void Linking.openURL(summary.source!.license_url!)}>
      <Text style={styles.note}>许可：{summary.source.license}</Text>
    </TouchableOpacity>}
  </View>;
}

const styles = StyleSheet.create({
  wrap: { gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '800' },
  body: { color: colors.text, fontSize: fontSize.md, lineHeight: 24 },
  note: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 20 },
  source: { minHeight: 44, justifyContent: 'center' },
});
