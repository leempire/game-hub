import { Platform } from 'react-native';
import { Asset } from 'expo-asset';
import { createAudioPlayer } from 'expo-audio';
import type { AbyssState } from './abyss';

const cues = {
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
  if ((me.xp_collected ?? 0) > (before.xp_collected ?? 0)) return 'pickup';
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
  const loading = new AbortController();
  let pickup: AudioBufferSourceNode | undefined;
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
      // Keep an active pickup from masking a new combat cue.
      if (cue !== 'pickup' && pickup) {
        pickup.stop();
        playing.delete(pickup);
        pickup = undefined;
      }
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      source.onended = () => {
        if (pickup === source) pickup = undefined;
        playing.delete(source);
        source.disconnect();
      };
      playing.add(source);
      if (cue === 'pickup') pickup = source;
      source.start();
      return true;
    },
    stop() {
      for (const source of playing) source.stop();
      playing.clear();
      pickup = undefined;
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
  let generation = 0;
  let pickupGeneration = 0;
  return {
    unlock() {},
    play(cue) {
      const player = players.get(cue);
      if (!player?.isLoaded) return false;
      const current = generation;
      const pickupRequest = pickupGeneration;
      void player.seekTo(0).then(() => {
        if (current !== generation) return;
        if (cue === 'pickup') {
          if (pickupRequest !== pickupGeneration) return;
        } else {
          pickupGeneration++;
          players.get('pickup')?.pause();
        }
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
