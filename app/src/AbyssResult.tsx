import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { abyssClock, abyssMapNames, abyssPassives, abyssWeapons, type AbyssSummary } from './abyss';
import { abyssColors as c } from './theme';

export default function AbyssResult({ game, uid }: { game: AbyssSummary; uid?: number }) {
  const me = game.players.find(player => player.id === uid);
  const isTeam = game.players.length > 1;
  const outcomeDetail = game.result === 'lost'
    ? (isTeam ? '所有潜水员均已失压，回收任务结束。' : '潜水员失压，回收任务结束。')
    : game.result === 'timeout' ? '作战时间已到，断缆守卫仍未击败。' : null;
  const map = game.map_size && Object.hasOwn(abyssMapNames, game.map_size) ? abyssMapNames[game.map_size] : undefined;
  const metrics = [
    ['作战时间', game.seconds === undefined ? undefined : abyssClock(game.seconds)],
    [isTeam ? '全队等级' : '等级', game.level],
    [isTeam ? '全队击退' : '击退', game.kills],
    ['打捞分', game.salvage],
  ];

  return <View testID="abyss-result-summary" style={styles.summary}>
    {outcomeDetail && <Text style={styles.text}>{outcomeDetail}</Text>}
    {map && <Text style={styles.label}>地图 · {map}</Text>}
    {game.boss !== undefined && <Text style={styles.label}>
      断缆守卫 · {game.boss_dead ? '已击败' : game.boss ? '仍在活动' : '未遭遇'}
    </Text>}
    {metrics.some(([, value]) => value !== undefined) && <View style={styles.metrics}>
      {metrics.map(([label, value]) => value !== undefined && <View key={label} style={styles.metric}>
        <Text style={styles.label}>{label}</Text>
        <Text style={styles.value}>{value}</Text>
      </View>)}
    </View>}
    {me?.talent_award && <View testID="abyss-talent-award" style={styles.talentBox}>
      <Text style={styles.talentTitle}>天赋点 +{me.talent_award.points}</Text>
      <Text style={styles.label}>
        {me.talent_award.detail.map(item => `${item.label} +${item.points}`).join(' · ')}
      </Text>
      <Text style={styles.label}>
        现有 {me.talent_award.balance} 点 · 回房间打开天赋树加点
      </Text>
    </View>}
    {(isTeam || game.players.some(player => player.damage_dealt !== undefined || player.damage_taken !== undefined || player.xp_collected !== undefined || player.rescues !== undefined)) && <>
      <Text style={styles.section}>{isTeam ? '潜水小队' : '本局贡献'}</Text>
      {game.players.map(player => {
        const contributions = [
          player.damage_dealt === undefined ? null : `输出伤害 ${player.damage_dealt}`,
          player.damage_taken === undefined ? null : `承受伤害 ${player.damage_taken}`,
          player.xp_collected === undefined ? null : `拾取经验 ${player.xp_collected}`,
          player.rescues === undefined ? null : `救起 ${player.rescues} 次`,
        ].filter(Boolean).join(' · ');
        return <View key={player.id} style={styles.diver}>
          <View style={styles.row}>
            <Text style={[styles.text, styles.name]} numberOfLines={1} accessibilityLabel={player.name}>
              {player.id === uid ? '你' : player.name}
            </Text>
            <Text style={styles.label}>
              {isTeam ? `击退 ${player.kills} · ` : ''}{player.out ? '出局' : player.hp > 0 ? '存活' : '倒地'}
            </Text>
          </View>
          {!!contributions && <Text style={styles.label}>{contributions}</Text>}
        </View>;
      })}
    </>}
    {me && <>
      <Text style={styles.section}>我的装备</Text>
      <View style={styles.loadout}>
        {Object.entries(me.weapons ?? {}).map(([weapon, level]) => {
          const info = Object.hasOwn(abyssWeapons, weapon) ? abyssWeapons[weapon as keyof typeof abyssWeapons] : undefined;
          const evolved = !!info && 'evolved' in info;
          return <Text key={weapon} style={[styles.item, evolved && styles.evolved]}>
            {info?.title ?? '旧版武器'} {evolved ? '已进化' : `Lv.${level}`}
          </Text>;
        })}
      </View>
      {!!Object.keys(me.passives ?? {}).length && <View style={styles.loadout}>
        {Object.entries(me.passives ?? {}).map(([passive, level]) => {
          const info = Object.hasOwn(abyssPassives, passive) ? abyssPassives[passive as keyof typeof abyssPassives] : undefined;
          return <Text key={passive} style={styles.item}>{info?.title ?? '旧版被动'} Lv.{level}</Text>;
        })}
      </View>}
    </>}
  </View>;
}

const styles = StyleSheet.create({
  summary: { gap: 7 },
  label: { color: c.muted, fontSize: 13 },
  metrics: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 6 },
  metric: { width: '49%', padding: 7, gap: 2, backgroundColor: c.deep, borderRadius: 6 },
  value: { color: c.signal, fontSize: 16, fontWeight: '700', fontVariant: ['tabular-nums'] },
  talentBox: { gap: 3, backgroundColor: c.deep, borderRadius: 6, padding: 7 },
  talentTitle: { color: c.signal, fontSize: 15, fontWeight: '800' },
  section: { color: c.signal, fontSize: 14, fontWeight: '700', marginTop: 4 },
  diver: { gap: 3, backgroundColor: c.deep, borderRadius: 6, padding: 6 },
  row: { flexDirection: 'row', gap: 6, alignItems: 'center', justifyContent: 'space-between' },
  text: { color: c.text, fontSize: 14 },
  name: { flex: 1, minWidth: 0 },
  loadout: { flexDirection: 'row', flexWrap: 'wrap', gap: 5 },
  item: { color: c.text, backgroundColor: c.deep, borderRadius: 5, paddingHorizontal: 6, paddingVertical: 4, fontSize: 13 },
  evolved: { color: c.chest },
});
