/** The same station art as gameplay, with every interaction layer disabled. */
import React, { useMemo } from 'react';
import { StyleSheet, View, useWindowDimensions } from 'react-native';
import { BuildingLayer, DoorLayer, EntityView, HeroView, StaticMap } from './StasisBoard';
import { PodSprite } from './stasisArt';
import { buildMap, TILE } from './stasisMap';
import type { StasisPayload } from './stasisTypes';
import type { Room } from './api';
import { stasisSeatColors, stasisSpace } from './theme';
const noop = () => {};
const shakes = { get: () => undefined };
export type PublicStasis = Omit<StasisPayload, 'your_seat' | 'your_uid'>;
export default function StasisWatchMap({ game, room }: { game: PublicStasis; room: Room }) {
  const { width } = useWindowDimensions();
  const map = useMemo(() => buildMap(game.map), [game.map]);
  const scale = Math.min(1, Math.min(width - 64, 640) / map.mapW);
  const buildings: Record<string, { kind: string; level: number; owner: boolean }> = {};
  for (const info of Object.values(game.map.rooms)) for (const building of info.buildings)
    buildings[building.cell] = { kind: building.kind, level: building.level, owner: false };
  return <View accessibilityLabel="只读全站地图" style={{ width: map.mapW * scale, height: map.mapH * scale, alignSelf: 'center' }}>
    <View pointerEvents="none" style={[styles.map, { width: map.mapW, height: map.mapH,
      transform: [{ scale }], transformOrigin: 'top left' }]} testID="watch-stasis-map">
      <StaticMap map={map} />
      <DoorLayer map={map} rooms={game.map.rooms} gates={game.map.gates} doorHp={{}}
        targeted={game.entity.target_door} shakes={shakes} onDoorPress={noop} onGatePress={noop} />
      <BuildingLayer buildings={buildings} onPress={noop} />
      {Object.entries(game.map.rooms).map(([id, info]) => <View key={id} style={{ position: 'absolute',
        left: info.bed[0] * TILE, top: info.bed[1] * TILE, width: TILE, height: TILE }}>
        <PodSprite size={TILE} lit={info.owner != null} occupied={!!game.players[String(info.owner)]?.lying} />
      </View>)}
      {Object.entries(game.players).map(([uid, player]) => player.alive && <View key={uid} style={{ position: 'absolute',
        left: player.pos[0] * TILE - 15, top: player.pos[1] * TILE - 15 }}>
        <HeroView color={stasisSeatColors[game.seat_colors[player.seat]] || stasisSeatColors.blue}
          name={room.players.find(person => person.id === Number(uid))?.nickname || `玩家 ${player.seat + 1}`}
          mine={false} lying={!!player.lying} />
      </View>)}
      <View style={{ position: 'absolute', left: game.entity.pos[0] * TILE - 22, top: game.entity.pos[1] * TILE - 22 }}>
        <EntityView mood={game.entity.state} level={game.entity.level} hp={game.entity.hp} maxHp={game.entity.max_hp} />
      </View>
    </View>
  </View>;
}
const styles = StyleSheet.create({ map: { position: 'absolute', left: 0, top: 0, backgroundColor: stasisSpace, overflow: 'hidden' } });
