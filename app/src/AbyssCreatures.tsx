import React, { memo } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, Defs, Ellipse, G, Path } from 'react-native-svg';
import type { AbyssEnemy } from './abyss';
import { abyssColors as c, colors } from './theme';

type CreatureKind = Exclude<AbyssEnemy, 'warden'>;
const creatureKinds: CreatureKind[] = ['drifter', 'needlefish', 'eel', 'crab', 'jelly', 'angler', 'elite'];

function Creature({ kind }: { kind: CreatureKind }) {
  const color = c[kind];
  if (kind === 'drifter') return <G>
    <Path d="M-7-7Q-15-12-24-5M-8 0Q-17-4-26 3M-7 7Q-16 4-22 10"
      stroke={color} strokeWidth="2.5" fill="none" strokeLinecap="round" />
    <Path d="M-11-8Q-1-16 14 0Q-1 16-11 8L-7 0Z" fill={color} stroke={c.shadow} strokeWidth="2" />
    <Path d="M-4-8Q3-7 8 0Q3 7-4 8L-1 0Z" fill={c.deep} />
    <Path d="M1-4L6 0L1 4" fill="none" stroke={c.signal} strokeWidth="1.5" strokeLinecap="round" />
  </G>;
  if (kind === 'eel') return <G>
    <Path d="M-28 8C-18-17-8 16 9 0" stroke={c.shadow} strokeWidth="10" fill="none" strokeLinecap="round" />
    <Path d="M-28 8C-18-17-8 16 9 0" stroke={color} strokeWidth="6" fill="none" strokeLinecap="round" />
    <Path d="M-18-4L-14 0M-8 3L-4 8M-33 1L-29-1" stroke={c.text} strokeOpacity=".68"
      strokeWidth="1.5" fill="none" strokeLinecap="round" />
    <Path d="M-3-5Q8-13 18-4L21 0L17 6Q7 9-3 5Z" fill={color} stroke={c.shadow} />
    <Path d="M14 3H19" stroke={c.deep} strokeWidth="1.5" />
    <Circle cx="13" cy="-2" r="2" fill={c.text} />
  </G>;
  if (kind === 'needlefish') return <G>
    <Path d="M-38 0H-23M-31-5H-24M-31 5H-24" stroke={color} strokeOpacity=".58"
      strokeWidth="1.5" strokeLinecap="round" fill="none" />
    <Path d="M-9 0L-22-8L-19 0L-22 8Z" fill={color} stroke={c.shadow} />
    <Path d="M-15 0L-6-5L-3-9L4-5L12-3L25 0L12 3L4 5L-3 9L-6 5Z"
      fill={color} stroke={c.shadow} />
    <Path d="M-10 0H16" stroke={c.text} />
    <Circle cx="9" cy="-2" r="1.7" fill={c.deep} />
  </G>;
  if (kind === 'jelly') return <G>
    <Path d="M-7-10Q-18-14-23-9M-8-4Q-20-8-22 0M-8 4Q-20 5-25 3M-7 10Q-19 14-23 10"
      fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" />
    <Path d="M-8-15Q13-21 19 0Q13 21-8 15Q-2 0-8-15Z"
      fill={color} fillOpacity=".72" stroke={color} strokeWidth="2" />
    <Path d="M2-11Q12-8 13 0Q12 8 2 11" fill="none" stroke={c.text} strokeWidth="2" />
    <Circle cx="4" r="4" fill={c.signal} />
  </G>;
  if (kind === 'angler') return <G>
    <Path d="M-12 0L-22-11L-19 0L-22 11Z" fill={color} stroke={c.shadow} />
    <Path d="M-15 0Q-13-15 2-15Q15-14 19-5L12 0L19 5Q15 14 2 15Q-13 15-15 0Z"
      fill={color} stroke={c.shadow} strokeWidth="2" />
    <Path d="M19-5L13-3L12 0L13 3L19 5Z" fill={c.text} />
    <Path d="M0-10Q8-21 21-15" fill="none" stroke={color} strokeWidth="2" />
    <Circle cx="22" cy="-14" r="4" fill={c.chest} stroke={c.text} />
    <Circle cx="8" cy="-6" r="2" fill={c.deep} />
  </G>;
  if (kind === 'elite') return <G>
    <Path d="M-17-7L-23-17L-31-20M-18 7L-23 17L-31 20M-5-11L-8-25L-19-28M-5 11L-8 25L-19 28"
      fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" />
    <Path d="M8-10L18-25L35-24L25-16L35-10L19-9ZM8 10L18 25L35 24L25 16L35 10L19 9Z"
      fill={color} stroke={c.shadow} strokeWidth="2" />
    <Path d="M-24-8L-15-16L4-15L14-8L18 0L14 8L4 15L-15 16L-24 8L-28 0Z"
      fill={color} stroke={c.shadow} strokeWidth="2" />
    <Path d="M-18-7L-12-11H0L8-5M-18 7L-12 11H0L8 5M-20 0H5"
      fill="none" stroke={c.text} strokeWidth="1.7" strokeLinecap="round" />
    <Circle cx="12" cy="-5" r="2.5" fill={c.charge} />
    <Circle cx="12" cy="5" r="2.5" fill={c.charge} />
  </G>;
  return <G>
    <Path d="M4 6l-6 8-8 4M-2 6l-7 8-8 4M-8 6l-8 8-8 4M4-6l-6-8-8-4M-2-6l-7-8-8-4M-8-6l-8-8-8-4" fill="none" stroke={color} strokeWidth="3" />
    <Path d="M7 7L16 12M7-7L16-12" fill="none" stroke={color} strokeWidth="5" />
    <Path d="M13-11L18-18L27-17L22-13L26-9L18-8ZM13 11L18 18L27 17L22 13L26 9L18 8Z"
      fill={color} stroke={c.shadow} strokeWidth="1.5" />
    <Ellipse rx="15" ry="12" fill={color} stroke={c.shadow} />
    <Path d="M-11-5L-1-8L8-4" fill="none" stroke={c.brass} strokeWidth="2" />
    <Circle cx="10" cy="-4" r="2" fill={c.text} /><Circle cx="10" cy="4" r="2" fill={c.text} />
  </G>;
}

export const CreatureDefinitions = memo(function CreatureDefinitions({ prefix }: { prefix: string }) {
  return <Defs>
    {creatureKinds.map(kind => <G id={`${prefix}-creature-${kind}`} key={kind}><Creature kind={kind} /></G>)}
    {/* 减速标记：虚线环 + 三根冰棱。形状与颜色双通道，不只靠变蓝传达。 */}
    <G id={`${prefix}-slow-mark`}>
      <Circle r="30" fill="none" stroke={c.beacon} strokeWidth="2" strokeDasharray="4 5" strokeOpacity=".85" />
      <Path d="M0-38V-31M-33-17L-27-14M33-17L27-14"
        fill="none" stroke={c.beacon} strokeWidth="2.5" strokeLinecap="round" />
    </G>
  </Defs>;
});

export const wardenImage = require('../assets/abyss/cable-warden.webp');

const fieldGuide: Record<AbyssEnemy, [string, string, number, number, number]> = {
  drifter: ['漂囊', '尖头带拖尾，开场常成群出现。', 10, 56, 2],
  needlefish: ['针鳍鱼', '尖吻和叉尾，体型小但速度最快。', 8, 125, 1],
  eel: ['裂鳍鳗', '弯曲长身，走位转向时留意追上来的鳗鱼。', 15, 105, 2],
  crab: ['锈甲蟹', '宽壳和双钳，移动慢，别被它们堵在墙边。', 22, 43, 2],
  jelly: ['脉光水母', '伞盖宽、触须长，绕行时多留一点距离。', 26, 58, 3],
  angler: ['灯颌鱼', '头顶悬灯，耐久和接触伤害都较高。', 38, 87, 5],
  elite: ['精英锈甲蟹', '宽叉巨钳和分层背甲，击倒后掉落宝箱。', 450, 83, 2],
  warden: ['断缆守卫', '耐久降到 45% 后进入狂暴。看到落点或脉冲圈就先撤出范围。', 1350, 84, 12],
};

const guideKinds: AbyssEnemy[] = [...creatureKinds, 'warden'];

export function AbyssFieldGuide() {
  return <View testID="abyss-field-guide" style={styles.guide}>
    <Text accessibilityRole="header" style={styles.heading}>辨认怪物</Text>
    <Text style={styles.baseStats}>以下为基础数值。耐久随波次、人数或局长变化，移速单位为像素/秒。</Text>
    {guideKinds.map(kind => {
      const [name, detail, hp, speed, damage] = fieldGuide[kind];
      return <View key={kind} style={styles.row}>
        {kind === 'warden'
          ? <Image accessible={false} source={wardenImage} resizeMode="contain" style={styles.warden} />
          : <Svg accessible={false} width={56} height={48} viewBox="-40 -34 80 68">
            <Creature kind={kind} />
          </Svg>}
        <View style={styles.description}>
          <Text style={styles.name}>{name}</Text>
          <Text style={styles.detail}>{detail}</Text>
          <Text testID={`abyss-creature-stats-${kind}`} style={styles.stats}>
            耐久 {hp} · 移速 {speed} · 接触伤害 {damage}
          </Text>
        </View>
      </View>;
    })}
  </View>;
}

const styles = StyleSheet.create({
  guide: { gap: 6, marginBottom: 12 },
  heading: { color: colors.text, fontWeight: '700', fontSize: 16 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 9, padding: 6, backgroundColor: c.deep, borderRadius: 7 },
  warden: { width: 56, height: 48 },
  baseStats: { color: colors.textSecondary, fontSize: 13, marginBottom: 2 },
  stats: { color: c.signal, fontSize: 12, fontVariant: ['tabular-nums'] },
  description: { flex: 1, gap: 3 },
  name: { color: c.text, fontWeight: '700', fontSize: 14 },
  detail: { color: c.muted, fontSize: 13 },
});
