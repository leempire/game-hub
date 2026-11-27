import React, { useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { useWebKeys } from './backGuard';
import { CONCENTRATION_COLUMNS, concentrationFocus, concentrationPosition, concentrationSuits } from './concentration';
import type { ConcentrationSlot } from './concentration';
import PlayingCardSuit from './PlayingCardSuit';
import { colors, fontSize, radius, spacing, spiderTable } from './theme';

interface Props {
  slots: ConcentrationSlot[];
  height: number;
  connected: boolean;
  canFlip: boolean;
  pending: number | null;
  keyboardEnabled: boolean;
  onFlip: (index: number) => void;
}

const web = Platform.OS === 'web';

export default function ConcentrationBoard({ slots, height, connected, canFlip, pending, keyboardEnabled, onFlip }: Props) {
  const [tabSlot, setTabSlot] = useState(0);
  const [focusedSlot, setFocusedSlot] = useState<number | null>(null);
  const cards = useRef<(React.ComponentRef<typeof Pressable> | null)[]>([]);

  useWebKeys(key => {
    if (focusedSlot === null) return;
    const next = concentrationFocus(focusedSlot, key, slots.length);
    if (next === undefined) return;
    if (next !== focusedSlot) cards.current[next]?.focus();
    return true;
  }, keyboardEnabled);

  return <View testID="concentration-board" style={styles.table}>
    {Array.from({ length: Math.ceil(slots.length / CONCENTRATION_COLUMNS) }, (_, row) => {
      const start = row * CONCENTRATION_COLUMNS;
      return <View key={row} style={styles.row}>
        {slots.slice(start, start + CONCENTRATION_COLUMNS).map((slot, col) => {
          const index = start + col;
          const revealed = connected && slot.state === 'revealed' ? slot : null;
          const collected = slot.state === 'collected';
          const suit = revealed ? concentrationSuits[revealed.suit] : null;
          const ink = suit?.red ? colors.danger : colors.text;
          const available = canFlip && slot.state === 'hidden';
          const label = revealed && suit ? `${suit.name} ${revealed.rank}` : collected ? '空位' : '暗牌';
          // Web's native disabled button drops focus when a card turns over. Give the wrapper the disabled state.
          return <View key={index} style={styles.slot} aria-disabled={web && !available ? true : undefined}>
            <Pressable ref={element => { cards.current[index] = element; }} testID={`concentration-card-${index}`}
              accessibilityRole="button" accessibilityLabel={`${concentrationPosition(index)}，${label}`}
              accessibilityState={{ disabled: !available }} disabled={!web && !available}
              onPress={() => { if (available) onFlip(index); }} tabIndex={web ? index === tabSlot ? 0 : -1 : undefined}
              onFocus={() => { setTabSlot(index); setFocusedSlot(index); }} onBlur={() => setFocusedSlot(null)}
              style={({ pressed }) => [styles.card, { height },
                collected ? styles.collected : revealed ? styles.face : styles.back,
                (pending === index || (available && pressed)) && styles.pressed,
                !available && styles.inactive,
                focusedSlot === index && [styles.focus, revealed && styles.faceFocus]]}>
              {revealed && suit ? <View style={styles.faceContents}>
                <Text maxFontSizeMultiplier={1.6} style={[styles.rank, { color: ink }]}>{revealed.rank}</Text>
                <PlayingCardSuit suit={suit.symbol} color={ink} size={21} />
              </View> : collected ? <View style={styles.collectedDot} /> : <View style={styles.backFrame}>
                <View style={styles.diamond} />
              </View>}
            </Pressable>
          </View>;
        })}
      </View>;
    })}
  </View>;
}

const styles = StyleSheet.create({
  table: { width: '100%', padding: spacing.sm, gap: radius.sm, backgroundColor: spiderTable, borderRadius: radius.lg },
  row: { flexDirection: 'row', gap: radius.sm },
  slot: { flex: 1, minWidth: 0 },
  card: { borderRadius: radius.md, borderWidth: 1, justifyContent: 'center', alignItems: 'center' },
  back: { backgroundColor: colors.primaryDark, borderColor: colors.primaryLight },
  backFrame: { ...StyleSheet.absoluteFill, margin: spacing.xs, borderRadius: radius.sm, borderWidth: 1,
    borderColor: colors.primaryLight, alignItems: 'center', justifyContent: 'center' },
  diamond: { width: 12, height: 12, borderRadius: 2, borderWidth: 2, borderColor: colors.white,
    transform: [{ rotate: '45deg' }] },
  face: { backgroundColor: colors.card, borderColor: colors.card },
  faceContents: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center',
    columnGap: spacing.xs },
  rank: { fontSize: fontSize.xxl, fontWeight: '800' },
  collected: { borderColor: colors.borderStrong, borderStyle: 'dashed' },
  collectedDot: { width: 5, height: 5, borderRadius: radius.full, backgroundColor: colors.borderStrong },
  pressed: { backgroundColor: colors.primary },
  inactive: { cursor: 'auto' },
  focus: { outlineColor: colors.white, outlineOffset: -5, outlineWidth: 2, outlineStyle: 'solid' },
  faceFocus: { outlineColor: colors.primaryDark },
});
