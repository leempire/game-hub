import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { createAudioPlayer } from 'expo-audio';
import { Asset } from 'expo-asset';
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { AbyssState } from './abyss';
import { onWebUserInteraction } from './backGuard';
import { abyssCue, createAbyssSounds } from './abyssSound';
import { abyssColors as c } from './theme';
import { AppModal } from './ui';

const soundtrack = require('../assets/abyss/recovery-station.mp3');
const musicKey = 'abyss.audio.music';
const effectsKey = 'abyss.audio.effects';
const musicLevelKey = 'abyss.audio.music-level';
const musicLevels = { quiet: .25, standard: .55, loud: .8 };
type MusicLevel = keyof typeof musicLevels;
const musicLevelNames: Record<MusicLevel, string> = { quiet: '低', standard: '中', loud: '高' };
function warningGain(gain: number) {
  return (gain / musicLevels.standard) * .28;
}

function recoveringGain(gain: number, progress: number) {
  const lowered = warningGain(gain);
  return progress === 1 ? gain : lowered + (gain - lowered) * progress;
}
const musicFadeMs = 240;

function createMusicPlayer() {
  if (Platform.OS !== 'web') return createAudioPlayer(soundtrack);
  const media = new Audio(Asset.fromModule(soundtrack).uri);
  return {
    get playing() { return !media.paused; },
    set loop(value: boolean) { media.loop = value; },
    set volume(value: number) { media.volume = value; },
    play() { void media.play().catch(() => {}); },
    pause() { media.pause(); },
    remove() {
      media.pause();
      media.removeAttribute('src');
      media.load();
    },
  };
}

export default function AbyssAudio({
  game, uid, live, inPlay, visible, onVisibilityChange, vibration, onToggleVibration,
}: {
  game: AbyssState | null;
  uid?: number;
  live: boolean;
  inPlay: boolean;
  visible: boolean;
  onVisibilityChange: (visible: boolean) => void;
  vibration: boolean | null;
  onToggleVibration: () => void;
}) {
  const musicPlayer = useRef<ReturnType<typeof createMusicPlayer> | null>(null);
  const [music, setMusic] = useState<boolean | null>(null);
  const [musicLevel, setMusicLevel] = useState<MusicLevel>('standard');
  const musicGain = useRef(musicLevels.standard);
  const [effects, setEffects] = useState<boolean | null>(null);
  const [error, setError] = useState(false);
  const sounds = useRef<ReturnType<typeof createAbyssSounds> | null>(null);
  const previous = useRef<AbyssState | null>(null);
  const lastPickupAt = useRef(-Infinity);
  const lastHitAt = useRef(-Infinity);
  const lastFinishAt = useRef(-Infinity);
  const lastAlertAt = useRef(-Infinity);
  const musicRestoreTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const musicFadeProgress = useRef(1);

  useEffect(() => {
    let mounted = true;
    void Promise.all([
      AsyncStorage.getItem(musicKey).catch(() => null),
      AsyncStorage.getItem(musicLevelKey).catch(() => null),
    ]).then(([enabled, level]) => {
      if (!mounted) return;
      if (level && Object.hasOwn(musicLevels, level)) {
        const saved = level as MusicLevel;
        musicGain.current = musicLevels[saved];
        setMusicLevel(saved);
      }
      // 默认开启：只有用户明确关过（存了 off）才保持关闭。旧版本没存过值，
      // 或者第一次进游戏，都从开启开始。
      setMusic(enabled !== 'off');
    });
    void AsyncStorage.getItem(effectsKey).catch(() => null).then(value => {
      if (mounted) setEffects(value !== 'off');
    });
    return () => { mounted = false; };
  }, []);

  const playMusic = useCallback(() => {
    const player = musicPlayer.current ??= createMusicPlayer();
    player.loop = true;
    if (musicRestoreTimer.current === undefined) player.volume = musicGain.current;
    player.play();
  }, []);

  const restoreMusic = useCallback(() => {
    clearTimeout(musicRestoreTimer.current);
    musicRestoreTimer.current = undefined;
    musicFadeProgress.current = 1;
    if (musicPlayer.current) musicPlayer.current.volume = musicGain.current;
  }, []);

  const duckMusic = useCallback(() => {
    const player = musicPlayer.current;
    if (!player?.playing) return;
    musicFadeProgress.current = 0;
    player.volume = warningGain(musicGain.current);
    clearTimeout(musicRestoreTimer.current);
    musicRestoreTimer.current = setTimeout(() => {
      const start = performance.now();
      const fade = () => {
        const progress = Math.min(1, (performance.now() - start) / musicFadeMs);
        musicFadeProgress.current = progress;
        player.volume = recoveringGain(musicGain.current, progress);
        musicRestoreTimer.current = progress < 1 ? setTimeout(fade, 30) : undefined;
      };
      fade();
    }, 650);
  }, []);

  const unlockEffects = useCallback(() => {
    sounds.current ??= createAbyssSounds();
    sounds.current.unlock();
  }, []);

  useEffect(() => {
    if (!inPlay || !effects) restoreMusic();
    if (inPlay && music && (Platform.OS !== 'web' || musicPlayer.current)) {
      if (!musicPlayer.current?.playing) playMusic();
    } else if (!inPlay || !music) {
      musicPlayer.current?.pause();
    }

    if (live && effects && (Platform.OS !== 'web' || sounds.current)) {
      try { unlockEffects(); } catch { /* The switch can retry on user input. */ }
    } else if (!live || !effects) {
      sounds.current?.stop();
    }

    if (Platform.OS !== 'web' || !inPlay || (!music && !effects)) return;
    // Restored preferences still need an interaction to start audio in a new document.
    function activate(event: Event) {
      if (!event.isTrusted) return;
      if (music && !musicPlayer.current?.playing) playMusic();
      if (effects) {
        try { unlockEffects(); } catch { setError(true); }
      }
    }
    return onWebUserInteraction(activate);
  }, [effects, inPlay, live, music, playMusic, restoreMusic, unlockEffects]);

  useEffect(() => {
    const before = previous.current;
    previous.current = live ? game : null;
    if (!live || !effects) return;
    const cue = before && game && abyssCue(before, game, uid);
    if (!cue) return;
    const now = performance.now();
    if (cue === 'pickup' && (now - lastPickupAt.current < 500 || now - lastAlertAt.current < 600)) return;
    if (cue === 'finish' && (now - lastFinishAt.current < 500 || now - lastAlertAt.current < 320)) return;
    if (cue === 'hit' && (now - lastHitAt.current < 240 || now - lastAlertAt.current < 320)) return;
    if (!sounds.current?.play(cue)) return;
    if (cue === 'pickup') lastPickupAt.current = now;
    else if (cue === 'hit' || cue === 'finish') {
      lastHitAt.current = now;
      if (cue === 'finish') lastFinishAt.current = now;
    } else lastAlertAt.current = now;
    if (cue === 'warden' || cue === 'warning') duckMusic();
  }, [duckMusic, effects, game, live, uid]);

  useEffect(() => () => {
    clearTimeout(musicRestoreTimer.current);
    musicPlayer.current?.remove();
    sounds.current?.dispose();
  }, []);

  function toggleMusic() {
    const next = !music;
    if (next && inPlay) playMusic();
    else {
      musicPlayer.current?.pause();
      restoreMusic();
    }
    setMusic(next);
    void AsyncStorage.setItem(musicKey, next ? 'on' : 'off').catch(() => {});
  }

  function chooseMusicLevel(level: MusicLevel) {
    musicGain.current = musicLevels[level];
    if (musicPlayer.current) {
      musicPlayer.current.volume = recoveringGain(musicGain.current, musicFadeProgress.current);
    }
    setMusicLevel(level);
    void AsyncStorage.setItem(musicLevelKey, level).catch(() => {});
  }

  function toggleEffects() {
    const next = !effects;
    try {
      if (next) unlockEffects();
      else sounds.current?.stop();
      setError(false);
      setEffects(next);
      void AsyncStorage.setItem(effectsKey, next ? 'on' : 'off').catch(() => {});
    } catch {
      setError(true);
    }
  }

  return <>
    <Pressable testID="abyss-audio" style={styles.button} accessibilityRole="button" disabled={!!game?.choice_seconds} onPress={() => onVisibilityChange(true)}>
      <Text style={styles.text}>{Platform.OS === 'web' ? '声音' : '反馈'}</Text>
    </Pressable>
    <AppModal visible={visible} onClose={() => onVisibilityChange(false)} cardStyle={styles.sheet} testID="abyss-audio-modal">
      <Text style={styles.title}>{Platform.OS === 'web' ? '声音' : '声音与震动'}</Text>
      <Pressable testID="abyss-music" style={styles.option} accessibilityRole="switch" accessibilityLabel="背景音乐"
        aria-checked={!!music} disabled={!inPlay || music === null} onPress={toggleMusic}>
        <Text style={styles.text}>背景音乐</Text>
        <Text style={[styles.text, music && styles.enabled]}>{music ? '开' : '关'}</Text>
      </Pressable>
      {!!music && <View style={styles.volumeRow}>
        <Text style={styles.text}>配乐音量</Text>
        {(Object.keys(musicLevels) as MusicLevel[]).map(level => <Pressable key={level}
          testID={`abyss-music-level-${level}`} accessibilityRole="button"
          accessibilityLabel={`配乐音量 ${musicLevelNames[level]}`} accessibilityState={{ selected: level === musicLevel }}
          onPress={() => chooseMusicLevel(level)} style={[styles.volumeChoice, level === musicLevel && styles.selectedVolume]}>
          <Text style={styles.text}>{musicLevelNames[level]}</Text>
        </Pressable>)}
      </View>}
      <Pressable testID="abyss-sfx" style={styles.option} accessibilityRole="switch" accessibilityLabel="战斗音效"
        aria-checked={!!effects} disabled={effects === null} onPress={toggleEffects}>
        <Text style={styles.text}>战斗音效</Text>
        <Text style={[styles.text, effects && styles.enabled]}>{effects ? '开' : '关'}</Text>
      </Pressable>
      {Platform.OS !== 'web' && <Pressable testID="abyss-vibration" style={styles.option}
        accessibilityRole="switch" accessibilityLabel="战斗震动" aria-checked={!!vibration}
        disabled={vibration === null} onPress={onToggleVibration}>
        <Text style={styles.text}>战斗震动</Text>
        <Text style={[styles.text, vibration && styles.enabled]}>{vibration ? '开' : '关'}</Text>
      </Pressable>}
      {error && <Text accessibilityRole="alert" style={styles.text}>当前设备无法开启战斗音效</Text>}
      <Pressable testID="abyss-audio-close" style={styles.button} accessibilityRole="button" onPress={() => onVisibilityChange(false)}>
        <Text style={styles.enabled}>关闭</Text>
      </Pressable>
    </AppModal>
  </>;
}

const styles = StyleSheet.create({
  button: { minHeight: 44, minWidth: 56, alignItems: 'center', justifyContent: 'center' },
  sheet: { backgroundColor: c.panel, borderColor: c.edge, borderWidth: 1 },
  option: { minHeight: 44, paddingHorizontal: 12, borderRadius: 8, backgroundColor: c.deep,
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  volumeRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 6 },
  volumeChoice: { minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center',
    borderRadius: 6, borderWidth: 1, borderColor: c.edge, backgroundColor: c.deep },
  selectedVolume: { borderColor: c.signal, backgroundColor: c.edge },
  title: { color: c.text, fontSize: 21, fontWeight: '800' },
  text: { color: c.text, fontSize: 15, fontWeight: '600' },
  enabled: { color: c.signal, fontSize: 15, fontWeight: '700' },
});
