/** Paint server clocks locally between authoritative updates; never adjudicate a timeout. */
import React, { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { colors, fontSize, radius, spacing } from './theme';

export interface ClockPayload {
  remaining_ms: Record<string, number>;
  turn: string;
  period_ms: number;
  period_left_ms: number;
  expired: string | boolean | null;
  paused: boolean;
}
export interface ObservedClock extends ClockPayload { receivedAt: number }
export interface ObservedCountdown { remaining_ms: number; receivedAt: number }

/** Only call on incoming messages, so the rendered component stays free of clock side effects. */
export function observeClock(clock: ClockPayload | null | undefined): ObservedClock | null {
  return clock ? { ...clock, receivedAt: Date.now() } : null;
}
export function observeCountdown(remaining: number): ObservedCountdown {
  return { remaining_ms: remaining, receivedAt: Date.now() };
}
export function clockValues(clock: ObservedClock, now: number) {
  const elapsed = clock.paused || clock.expired ? 0 : Math.max(0, now - clock.receivedAt);
  return Object.entries(clock.remaining_ms).map(([side, remaining]) => {
    const active = !clock.expired && !clock.paused && clock.turn === side;
    const main = Math.max(0, remaining - (active ? elapsed : 0));
    const inPeriod = main === 0 && clock.period_ms > 0;
    const period = clock.turn === side
      ? Math.max(0, clock.period_left_ms - (active ? Math.max(0, elapsed - remaining) : 0)) : clock.period_ms;
    return { side, active, inPeriod, milliseconds: inPeriod ? period : main };
  });
}
export function formatClock(milliseconds: number) {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
function useDisplayTime(enabled: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, [enabled]);
  return now;
}

export function GameClock({ clock, game }: { clock?: ObservedClock | null; game: string }) {
  const now = useDisplayTime(!!clock);
  if (!clock) return null;
  const names: Record<string, string> = game === 'xiangqi'
    ? { red: '红方', black: '黑方' } : { black: '黑棋', white: '白棋' };
  return <View style={styles.row} testID="game-clock">
    {clockValues(clock, now).map(value => <View key={value.side}
      style={[styles.clock, value.active && styles.active]}>
      <Text style={styles.text}>{names[value.side] || value.side} · {value.inPeriod ? '读秒 ' : ''}{formatClock(value.milliseconds)}</Text>
    </View>)}
    {!!clock.expired && <Text style={styles.note}>等待服务器确认超时结果</Text>}
  </View>;
}

export function SetupCountdown({ value }: { value?: ObservedCountdown | null }) {
  const now = useDisplayTime(!!value);
  if (!value) return null;
  const remaining = Math.max(0, value.remaining_ms - Math.max(0, now - value.receivedAt));
  return <Text style={styles.note} testID="luzhanqi-setup-countdown">
    布阵剩余 {formatClock(remaining)} · 超时自动确认当前阵型
  </Text>;
}

const styles = StyleSheet.create({
  row: { alignSelf: 'stretch', flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: spacing.xs,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs },
  clock: { minWidth: 100, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.sm,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.xs, backgroundColor: colors.card },
  active: { borderColor: colors.primary, borderWidth: 2, backgroundColor: colors.surfaceMuted },
  text: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600', fontVariant: ['tabular-nums'] },
  note: { color: colors.textSecondary, fontSize: fontSize.sm, textAlign: 'center' },
});
