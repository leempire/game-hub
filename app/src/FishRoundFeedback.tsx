import React, { useEffect, useRef, useState } from 'react';
import { Animated, Easing, Platform, StyleSheet, Text, View } from 'react-native';
import type { FishPlayer } from './fish';
import { fishColors as c } from './theme';

type Feedback = { text: string; tone: 'gain' | 'growth' | 'damage' | 'item' };
const priority = { gain: 0, item: 1, growth: 2, damage: 3 };

export default function FishRoundFeedback({ player, round, active }: {
  player?: FishPlayer; round: number; active: boolean;
}) {
  const memory = useRef<{ player?: FishPlayer; round: number }>({ round: -1 });
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [progress] = useState(() => new Animated.Value(1));
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const shown = useRef({ priority: -1, until: 0 });

  useEffect(() => {
    return () => {
      clearTimeout(timer.current);
      progress.stopAnimation();
    };
  }, [progress]);

  useEffect(() => {
    const old = memory.current;
    memory.current = { player, round };
    if (!active || !player || !old.player || old.player.id !== player.id || old.round > round) {
      clearTimeout(timer.current);
      shown.current = { priority: -1, until: 0 };
      progress.setValue(1);
      return;
    }
    const before = old.player;
    const points = player.score - before.score;
    let next: Feedback | undefined;
    if (player.lives < before.lives) next = { tone: 'damage', text: player.lives ? '失去一条命 · 已复活' : '命数耗尽 · 进入观战' };
    else if ((player.tier ?? 1) > (before.tier ?? 1)) next = { tone: 'growth', text: `长大了！体型 ${player.tier} 级` };
    else if (before.shield && !player.shield) next = { tone: 'item', text: '护盾挡住了一次吞食' };
    else if (!before.shield && player.shield) next = { tone: 'item', text: '获得护盾' };
    else if (!before.boost && player.boost) next = { tone: 'item', text: '获得海流加速' };
    else if (points > 0) next = { tone: 'gain', text: `+${points} 分` };
    const now = performance.now();
    if (!next || (now < shown.current.until && priority[next.tone] < shown.current.priority)) return;
    shown.current = { priority: priority[next.tone], until: now + 850 };

    clearTimeout(timer.current);
    progress.stopAnimation();
    const cue = next;
    // Batch snapshots that arrive in the same paint.
    timer.current = setTimeout(() => {
      setFeedback(cue);
      progress.setValue(0);
      Animated.timing(progress, {
        toValue: 1, duration: cue.tone === 'gain' ? 650 : 1250,
        easing: Easing.linear, useNativeDriver: Platform.OS !== 'web', isInteraction: false,
      }).start();
    }, 0);
  }, [active, player, progress, round]);

  if (!active || !feedback) return null;
  const tone = feedback.tone;
  const opacity = progress.interpolate({ inputRange: [0, .08, .63, 1], outputRange: [0, 1, 1, 0] });
  return <View pointerEvents="none" style={StyleSheet.absoluteFill}>
    {tone === 'damage' && <Animated.View style={[StyleSheet.absoluteFill, { opacity, backgroundColor: c.harmTint }]} />}
    <Animated.View style={[styles.toast, {
      opacity, transform: [{ translateY: progress.interpolate({ inputRange: [0, 1], outputRange: [6, -15] }) }],
    }]}>
      <Text style={[styles.text, { color: tone === 'damage' ? c.coral : tone === 'item' ? c.amber : c.green }]}>{feedback.text}</Text>
    </Animated.View>
  </View>;
}

const styles = StyleSheet.create({
  toast: { position: 'absolute', bottom: 92, alignSelf: 'center', paddingHorizontal: 15, paddingVertical: 7,
    borderRadius: 16, borderWidth: 1, borderColor: c.line, backgroundColor: c.glass },
  text: { fontWeight: '800', fontSize: 16 },
});
