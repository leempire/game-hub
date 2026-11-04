/** Anonymous, read-only final replay. No room socket or gameplay actions are mounted. */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity } from 'react-native';
import { BoardSnapshot } from './HistoryScreen';
import { fetchSharedReplay } from '../sharing';
import type { GameRecord } from '../api';
import { gameTitle } from '../games';
import { Card } from '../ui';
import { colors, fontSize, spacing } from '../theme';

export default function SharedReplayScreen({ route }: { route: { params: { token: string } } }) {
  const token = route.params.token;
  const [response, setResponse] = useState<{ key: string; record?: GameRecord; error?: string } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = `${token}:${attempt}`;
  const record = response?.key === key ? response.record : null;
  const error = response?.key === key ? response.error : null;
  useEffect(() => {
    let current = true;
    void fetchSharedReplay(token).then(value => { if (current) setResponse({ key, record: value }); })
      .catch(cause => { if (current) setResponse({ key, error: (cause as Error).message }); });
    return () => { current = false; };
  }, [token, key]);
  return <ScrollView contentContainerStyle={styles.page} testID="shared-replay">
    {!record && !error && <ActivityIndicator color={colors.primary} accessibilityLabel="加载回放" />}
    {error && <Card><Text accessibilityRole="alert" style={styles.error}>{error}</Text>
      <TouchableOpacity style={styles.retry} onPress={() => setAttempt(value => value + 1)}>
        <Text style={styles.hint}>重新加载</Text>
      </TouchableOpacity></Card>}
    {record && <Card style={styles.card}>
      <Text style={styles.title}>{gameTitle(record.game)} · 终局回放</Text>
      <Text style={styles.hint}>{record.players.map(player => player.nickname || player.username).join(' / ')}</Text>
      <Text style={styles.hint}>{record.ended_at} · 只读分享</Text>
      <BoardSnapshot rec={record} />
      <Text style={styles.hint}>分享不包含 UNO 剩余手牌、抽牌堆或房间邀请。此页面不能加入或操作对局。</Text>
    </Card>}
  </ScrollView>;
}
const styles = StyleSheet.create({
  page: { flexGrow: 1, backgroundColor: colors.bg, padding: spacing.md, gap: spacing.sm },
  card: { gap: spacing.sm, width: '100%', alignSelf: 'center', maxWidth: 720 },
  title: { color: colors.text, fontSize: fontSize.xl, fontWeight: '700' },
  hint: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 21 },
  error: { color: colors.danger, fontSize: fontSize.md },
  retry: { minHeight: 48, justifyContent: 'center', alignItems: 'center' },
});
