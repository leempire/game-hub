import { useEffect, useRef, useState } from 'react';
import { Platform } from 'react-native';
import { createAudioPlayer } from 'expo-audio';
import { Asset } from 'expo-asset';
import type { FishState } from './fish';

const sources = {
  music: require('../assets/fish/reef-loop.mp3'),
  eat: require('../assets/fish/eat.mp3'),
  growth: require('../assets/fish/growth.mp3'),
  hit: require('../assets/fish/hit.mp3'),
  item: require('../assets/fish/item.mp3'),
  event: require('../assets/fish/event.mp3'),
  finish: require('../assets/fish/finish.mp3'),
};
type Cue = keyof typeof sources;

function player(cue: Cue) {
  const loop = cue === 'music';
  if (Platform.OS === 'web') {
    const media = new Audio(Asset.fromModule(sources[cue]).uri);
    media.volume = loop ? .36 : .58;
    media.loop = loop;
    return {
      play: () => {
        if (!loop) media.currentTime = 0;
        void media.play().catch(() => {});
      },
      pause: () => media.pause(),
      dispose: () => { media.pause(); media.removeAttribute('src'); media.load(); },
    };
  }
  const audio = createAudioPlayer(sources[cue]);
  audio.volume = loop ? .36 : .58;
  audio.loop = loop;
  return {
    play: () => {
      if (loop) audio.play();
      else void audio.seekTo(0).then(() => audio.play()).catch(() => {});
    },
    pause: () => audio.pause(),
    dispose: () => audio.remove(),
  };
}

export function useFishAudio(game: FishState | null, uid: number | undefined, foreground: boolean) {
  const [enabled, setEnabled] = useState(false);
  const bank = useRef<Partial<Record<Cue, ReturnType<typeof player>>>>({});
  const previous = useRef<FishState | null>(null);
  const inRound = !!game && !game.finished;
  const lastEat = useRef(0);

  useEffect(() => {
    const players = bank.current;
    return () => Object.values(players).forEach(audio => audio.dispose());
  }, []);

  function toggle() {
    if (enabled) {
      Object.values(bank.current).forEach(audio => audio.pause());
    } else {
      for (const cue of Object.keys(sources) as Cue[]) bank.current[cue] ??= player(cue);
      // Browser autoplay requires the first play call in the tap handler.
      if (foreground && inRound) bank.current.music?.play();
    }
    setEnabled(!enabled);
  }

  useEffect(() => {
    const players = bank.current;
    if (!enabled || !foreground) {
      Object.values(players).forEach(audio => audio.pause());
      return;
    }
    if (inRound) players.music?.play();
    else players.music?.pause();
  }, [enabled, foreground, inRound]);

  useEffect(() => {
    const old = previous.current;
    previous.current = game;
    if (!enabled || !foreground || !old || !game || old.finished || game.frame <= old.frame) return;
    const me = game.players.find(p => p.id === uid);
    const before = old.players.find(p => p.id === uid);
    const audio = bank.current;
    if (game.finished) audio.finish?.play();
    else if (me && before && (me.lives < before.lives || (!me.shield && before.shield))) audio.hit?.play();
    else if (me && before && ((me.shield && !before.shield) || (me.boost && !before.boost))) audio.item?.play();
    else if (me && before && (me.tier ?? 1) > (before.tier ?? 1)) audio.growth?.play();
    else if (game.event && old.event !== game.event) audio.event?.play();
    else if (me && before && me.score > before.score && performance.now() - lastEat.current > 140) {
      lastEat.current = performance.now();
      audio.eat?.play();
    }
  }, [enabled, foreground, game, uid]);

  return { enabled, toggle };
}
