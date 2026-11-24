import React, { useState } from 'react';
import { Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { colors, fontSize, radius, spacing, unoInk, unoOnTable } from './theme';
import { cardLabel } from './uno';
import { UnoCardView, UNO_CARD_H, UNO_CARD_W } from './UnoCardView';

const OVERLAP = 22;
const TILT = 5;
const PRESS_LIFT = 10;

type PreviewSource = 'hover' | 'focus' | 'hold';

interface Props {
  hand: string[];
  effects?: Record<string, string>;
  playable: Set<string>;
  jumpable: Set<string>;
  isMyTurn: boolean;
  finished?: boolean;
  onPlay: (card: string) => void;
}

export default function UnoHand({ hand, effects, playable, jumpable, isMyTurn, finished, onPlay }: Props) {
  const [preview, setPreview] = useState<{ index: number; card: string; source: PreviewSource } | null>(null);
  const effect = preview && hand[preview.index] === preview.card ? effects?.[preview.card] : undefined;
  const mid = (hand.length - 1) / 2;

  return <>
    {effects && hand.length > 0 && <View pointerEvents="none" style={styles.hintSlot}>
      <Text accessibilityLiveRegion="polite" testID={effect ? 'u-hand-effect' : undefined}
        style={[styles.hintText, effect && styles.hintActive]}>
        {effect || (Platform.OS === 'web' ? '悬停或长按查看牌效' : '长按查看牌效')}
      </Text>
    </View>}
    <ScrollView horizontal style={styles.scroll} contentContainerStyle={styles.content}
      onScrollBeginDrag={() => setPreview(null)} showsHorizontalScrollIndicator={false}>
      {hand.map((card, index) => {
        const canPlay = isMyTurn && playable.has(card);
        const canJump = !isMyTurn && jumpable.has(card);
        const can = canPlay || canJump;
        const tilt = mid > 0 ? (index - mid) / mid : 0;
        const showEffect = (source: PreviewSource) => setPreview({ index, card, source });
        const hideEffect = (source: PreviewSource) => setPreview(current => (
          current?.index === index && current.card === card && current.source === source ? null : current
        ));
        return (
          <Pressable
            key={`${card}-${index}`}
            onHoverIn={() => showEffect('hover')}
            onHoverOut={() => hideEffect('hover')}
            onFocus={event => {
              // 触屏松手也会聚焦卡牌，只在键盘聚焦时重新显示提示。
              if (Platform.OS !== 'web' || (event.currentTarget as unknown as HTMLElement).matches(':focus-visible')) {
                showEffect('focus');
              }
            }}
            onBlur={() => hideEffect('focus')}
            onLongPress={() => showEffect('hold')}
            delayLongPress={400}
            onPressOut={() => hideEffect('hold')}
            onPress={() => {
              if (!can) return;
              setPreview(null);
              onPlay(card);
            }}
            accessibilityRole={can ? 'button' : 'image'}
            accessibilityState={{ disabled: !can }}
            accessibilityLabel={`${cardLabel(card)}${canPlay ? '（可出）' : canJump ? '（可以抢出）' : ''}`}
            accessibilityHint={effects?.[card]}
            testID={canPlay ? `u-play-${index}` : canJump ? `u-jump-${index}` : `u-card-${index}`}
            style={({ pressed }) => [styles.item, index > 0 && styles.overlap, {
              zIndex: pressed ? hand.length : index,
              opacity: pressed ? 0.85 : 1,
              transform: [
                { translateY: (pressed ? -PRESS_LIFT : 0) + Math.abs(tilt) * 6 },
                { rotate: `${tilt * TILT}deg` },
              ],
            }]}
          >
            <UnoCardView card={card} width={UNO_CARD_W} height={UNO_CARD_H} playable={canPlay} />
            {canJump && <View style={styles.jumpBadge} testID={`u-jumpbadge-${index}`}>
              <Text style={styles.jumpBadgeText}>抢出</Text>
            </View>}
          </Pressable>
        );
      })}
      {hand.length === 0 && !finished && <Text style={styles.empty}>（没有手牌）</Text>}
    </ScrollView>
  </>;
}

const styles = StyleSheet.create({
  hintSlot: { alignSelf: 'stretch', alignItems: 'center', marginTop: spacing.xs, minHeight: 24, paddingHorizontal: spacing.sm },
  hintText: { color: colors.textSecondary, fontSize: fontSize.xs, lineHeight: 20, textAlign: 'center', paddingHorizontal: spacing.sm },
  hintActive: { color: colors.text, backgroundColor: colors.surfaceMuted, borderRadius: radius.sm, fontWeight: '700' },
  scroll: { width: '100%', maxHeight: UNO_CARD_H + 40 },
  content: { flexGrow: 1, justifyContent: 'center', alignItems: 'flex-end', paddingHorizontal: spacing.md, paddingTop: 30, paddingBottom: 6 },
  item: { justifyContent: 'flex-end', position: 'relative' },
  overlap: { marginLeft: -OVERLAP },
  empty: { fontSize: fontSize.sm, color: colors.textMuted, alignSelf: 'center' },
  jumpBadge: {
    position: 'absolute', top: -6, left: 2, paddingHorizontal: 5, paddingVertical: 1,
    borderRadius: 6, backgroundColor: unoOnTable, borderWidth: 1, borderColor: unoInk,
  },
  jumpBadgeText: { fontSize: 10, fontWeight: '800', color: unoInk },
});
