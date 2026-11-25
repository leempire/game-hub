import { Platform } from 'react-native';
import { Asset } from 'expo-asset';
import { createAudioPlayer } from 'expo-audio';
import type { AbyssState } from './abyss';

const cues = {
  finish: require('../assets/abyss/sfx/finish.wav'),
  hit: require('../assets/abyss/sfx/hit.mp3'),
  pickup: require('../assets/abyss/sfx/pickup.mp3'),
  damage: require('../assets/abyss/sfx/damage.mp3'),
  rescue: require('../assets/abyss/sfx/rescue.mp3'),
  upgrade: require('../assets/abyss/sfx/upgrade.mp3'),
  chest: require('../assets/abyss/sfx/chest.mp3'),
  warden: require('../assets/abyss/sfx/warden.mp3'),
  warning: require('../assets/abyss/sfx/warning.mp3'),
  win: require('../assets/abyss/sfx/win.mp3'),
  loss: require('../assets/abyss/sfx/loss.mp3'),
};
type Cue = keyof typeof cues;
type QuietCue = 'pickup' | 'hit' | 'finish';
const isQuietCue = (cue: Cue): cue is QuietCue => cue === 'pickup' || cue === 'hit' || cue === 'finish';

export function abyssCue(previous: AbyssState, game: AbyssState, uid?: number): Cue | undefined {
  if (previous.result || game.frame <= previous.frame) return;
  if (game.result === 'won') return 'win';
  if (game.result === 'lost' || game.result === 'timeout') return 'loss';
  if (game.frame - previous.frame > 20) return;
  if (!previous.boss && game.boss) return 'warden';
  const attack = game.warden_attack;
  if (attack && !attack.impact && attack.id !== previous.warden_attack?.id) return 'warning';
  if (game.chest_events.some(event => event.player === uid && event.frame > previous.frame)) return 'chest';
  const before = previous.players.find(player => player.id === uid);
  const me = game.players.find(player => player.id === uid);
  if (!before || !me) return;
  if (!before.hp && me.hp) return 'rescue';
  if (me.choices.length && (game.level > previous.level || !before.choices.length)) return 'upgrade';
  if (me.hp < before.hp) return 'damage';
  if (me.hp > 0 && me.kills > before.kills) return 'finish';
  if ((me.xp_collected ?? 0) > (before.xp_collected ?? 0)) return 'pickup';
  if (me.hp > 0 && before.damage_dealt !== undefined && (me.damage_dealt ?? 0) > before.damage_dealt) return 'hit';
}

interface AbyssSounds {
  unlock(): void;
  play(cue: Cue): boolean;
  stop(): void;
  dispose(): void;
}

function createWebSounds(): AbyssSounds {
  const context = new AudioContext();
  const buffers = new Map<Cue, AudioBuffer>();
  const playing = new Set<AudioBufferSourceNode>();
  const alerts = new Set<AudioBufferSourceNode>();
  const loading = new AbortController();
  const quietSources = new Map<QuietCue, AudioBufferSourceNode>();
  for (const cue of Object.keys(cues) as Cue[]) {
    void fetch(Asset.fromModule(cues[cue]).uri, { signal: loading.signal })
      .then(response => {
        if (!response.ok) throw new Error('Sound asset unavailable');
        return response.arrayBuffer();
      })
      .then(bytes => context.decodeAudioData(bytes))
      .then(buffer => { buffers.set(cue, buffer); })
      .catch(() => { /* A missing clip should never interrupt a match. */ });
  }

  return {
    unlock() {
      if (context.state !== 'running' && context.state !== 'closed') void context.resume().catch(() => {});
    },
    play(cue) {
      const buffer = buffers.get(cue);
      if (!buffer || context.state !== 'running') return false;
      if (isQuietCue(cue) && alerts.size) return false;
      // Let a warning or damage cue cut through ongoing pickup and hit sounds.
      if (!isQuietCue(cue)) {
        for (const source of quietSources.values()) {
          source.stop();
          playing.delete(source);
        }
        quietSources.clear();
      } else {
        const interrupted: QuietCue[] = cue === 'finish' ? ['hit', 'finish'] : [cue];
        for (const quiet of interrupted) {
          const previous = quietSources.get(quiet);
          if (!previous) continue;
          quietSources.delete(quiet);
          previous.stop();
          playing.delete(previous);
        }
      }
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      source.onended = () => {
        if (isQuietCue(cue) && quietSources.get(cue) === source) quietSources.delete(cue);
        playing.delete(source);
        alerts.delete(source);
        source.disconnect();
      };
      playing.add(source);
      if (isQuietCue(cue)) quietSources.set(cue, source);
      else alerts.add(source);
      source.start();
      return true;
    },
    stop() {
      for (const source of playing) source.stop();
      playing.clear();
      alerts.clear();
      quietSources.clear();
      void context.suspend().catch(() => {});
    },
    dispose() {
      loading.abort();
      void context.close().catch(() => {});
    },
  };
}

function createNativeSounds(): AbyssSounds {
  const players = new Map((Object.keys(cues) as Cue[]).map(cue => [cue, createAudioPlayer(cues[cue])]));
  const alertPlayers = [...players].filter(([cue]) => !isQuietCue(cue)).map(([, player]) => player);
  let generation = 0;
  let quietGeneration = 0;
  return {
    unlock() {},
    play(cue) {
      const player = players.get(cue);
      if (!player?.isLoaded || (isQuietCue(cue) && alertPlayers.some(alert => alert.playing))) return false;
      const current = generation;
      if (!isQuietCue(cue)) {
        quietGeneration++;
        players.get('pickup')?.pause();
        players.get('hit')?.pause();
        players.get('finish')?.pause();
      } else if (cue === 'finish') {
        quietGeneration++;
        players.get('hit')?.pause();
      }
      const quietRequest = quietGeneration;
      void player.seekTo(0).then(() => {
        if (current !== generation) return;
        if (isQuietCue(cue) && quietRequest !== quietGeneration) return;
        player.play();
      }).catch(() => {});
      return true;
    },
    stop() {
      generation++;
      for (const player of players.values()) player.pause();
    },
    dispose() {
      generation++;
      for (const player of players.values()) player.remove();
    },
  };
}

export function createAbyssSounds(): AbyssSounds {
  return Platform.OS === 'web' ? createWebSounds() : createNativeSounds();
}
