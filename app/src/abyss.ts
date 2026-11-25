export const abyssWorld = { width: 1440, height: 1000, wallInset: 22 };
export const abyssRescue = { radius: 52, ticks: 40 };

export function abyssLowPressure(hp: number, maxHp: number) {
  return hp > 0 && hp <= maxHp * .25;
}

// 基础 12 把武器 + 12 个进化。数值由服务端权威结算，这里只提供名字与说明。
export const abyssWeapons = {
  whip: { title: '裂潮鞭', detail: '身前身后横向挥击，穿透所有敌人', maxLevel: 8 },
  rivet: { title: '铆钉发射器', detail: '自动射击最近的敌人', maxLevel: 8 },
  harpoon: { title: '贯流鱼叉', detail: '朝移动方向连发，穿透多个敌人', maxLevel: 8 },
  axe: { title: '锚刃', detail: '向上抛出，重击并穿透', maxLevel: 8 },
  cross: { title: '回声十字镖', detail: '追踪最近敌人并回旋', maxLevel: 8 },
  bible: { title: '环流浮标', detail: '环绕自身持续撞击', maxLevel: 8 },
  charge: { title: '深压火球', detail: '追踪随机敌人，高伤火球', maxLevel: 8 },
  ring: { title: '荧孢光环', detail: '灼烧身边所有敌人', maxLevel: 8 },
  spore: { title: '腐潮水池', detail: '在附近留下持续伤害水域', maxLevel: 8 },
  rune: { title: '弹跳声呐', detail: '撞墙反弹的声呐浮标', maxLevel: 8 },
  arc: { title: '裂潮电弧', detail: '随机落雷，范围伤害', maxLevel: 8 },
  pentagram: { title: '深渊脉冲', detail: '清空视野内敌人，冷却极长', maxLevel: 8 },
  bloody_tear: { title: '血潮鞭', detail: '裂潮鞭进化：暴击时汲取生命', maxLevel: 1, evolved: true, base: 'whip' },
  holy_wand: { title: '圣铆炮', detail: '铆钉发射器进化：高频连射', maxLevel: 1, evolved: true, base: 'rivet' },
  thousand_edge: { title: '千刃鱼群', detail: '贯流鱼叉进化：无间断刃流', maxLevel: 1, evolved: true, base: 'harpoon' },
  death_spiral: { title: '死神漩涡', detail: '锚刃进化：向四周抛出九道巨刃', maxLevel: 1, evolved: true, base: 'axe' },
  heaven_sword: { title: '天穹剑', detail: '回声十字镖进化：巨型回旋剑', maxLevel: 1, evolved: true, base: 'cross' },
  unholy_vespers: { title: '邪潮圣咏', detail: '环流浮标进化：永不停歇的环流', maxLevel: 1, evolved: true, base: 'bible' },
  hellfire: { title: '狱火', detail: '深压火球进化：无限穿透的狱火', maxLevel: 1, evolved: true, base: 'charge' },
  soul_eater: { title: '噬魂光环', detail: '荧孢光环进化：汲取生命', maxLevel: 1, evolved: true, base: 'ring' },
  la_borra: { title: '拉波拉潮', detail: '腐潮水池进化：水洼追着你移动', maxLevel: 1, evolved: true, base: 'spore' },
  no_future: { title: '未来隔绝', detail: '弹跳声呐进化：反弹时爆炸，无尽地图首次命中时爆炸', maxLevel: 1, evolved: true, base: 'rune' },
  thunder_loop: { title: '雷环', detail: '裂潮电弧进化：每道雷击两次', maxLevel: 1, evolved: true, base: 'arc' },
  gorgeous_moon: { title: '华月', detail: '深渊脉冲进化：清屏并回收全部信标', maxLevel: 1, evolved: true, base: 'pentagram' },
};

export type AbyssWeapon = keyof typeof abyssWeapons;

// 13 个战斗向被动。Greed / Curse / Revival 暂未进入候选池。
export const abyssPassives = {
  spinach: { title: '浓缩燃素', detail: '所有武器伤害 +10%/级', maxLevel: 5, stat: 'might' },
  hollow_heart: { title: '高压气瓶', detail: '最大生命 +20%/级', maxLevel: 5, stat: 'max_hp' },
  empty_tome: { title: '循环泵', detail: '武器冷却 -8%/级', maxLevel: 5, stat: 'cooldown' },
  candelabrador: { title: '扩散声呐', detail: '武器范围 +10%/级', maxLevel: 5, stat: 'area' },
  bracer: { title: '流体加速器', detail: '投射物速度 +10%/级', maxLevel: 5, stat: 'speed' },
  spellbinder: { title: '延时引信', detail: '效果持续时间 +10%/级', maxLevel: 5, stat: 'duration' },
  duplicator: { title: '分流阀', detail: '投射物数量 +1/级', maxLevel: 2, stat: 'amount' },
  wings: { title: '推进鳍', detail: '移动速度 +10%/级', maxLevel: 5, stat: 'move_speed' },
  attractorb: { title: '信标磁锚', detail: '拾取范围大幅提升（乘算）', maxLevel: 5, stat: 'magnet' },
  clover: { title: '幸运锈币', detail: '幸运 +10%/级', maxLevel: 5, stat: 'luck' },
  crown: { title: '打捞执照', detail: '经验获取 +8%/级', maxLevel: 5, stat: 'growth' },
  armor: { title: '抗压壳', detail: '受到伤害 -1/级', maxLevel: 5, stat: 'armor' },
  pummarola: { title: '自愈膜', detail: '每秒回复 +0.2/级', maxLevel: 5, stat: 'recovery' },
};

export type AbyssPassive = keyof typeof abyssPassives;

export const abyssUtility = {
  repair: { title: '紧急补片', detail: '修复 30 点耐久；倒地时延长救援窗口' },
  rest: { title: '短暂休整', detail: '修复 15 点耐久' },
  salvage: { title: '打捞锈币', detail: '增加 100 点打捞分' },
};

export type AbyssChoice = AbyssWeapon | AbyssPassive | keyof typeof abyssUtility;
export type AbyssEnemy = 'drifter' | 'eel' | 'crab' | 'needlefish' | 'jelly' | 'angler' | 'elite' | 'warden';
export type XY = [number, number];

export interface AbyssStats {
  might: number;
  area: number;
  cooldown: number;
  duration: number;
  speed: number;
  amount: number;
  magnet: number;
  luck: number;
  growth: number;
  armor: number;
  recovery: number;
}

const changeLabels = {
  damage: '伤害', amount: '数量', cooldown: '冷却', cooldown_scale: '冷却倍率',
  area: '范围', pierce: '穿透', duration: '持续', duration_scale: '持续倍率', speed: '弹速', interval: '连发间隔',
  knockback: '击退', crit: '暴击率', preserve: '掉落保留率', might: '伤害',
  max_hp: '最大生命', move_speed: '移速', luck: '幸运', magnet: '吸取范围',
  growth: '经验', armor: '护甲', recovery: '每秒回复',
};

type AbyssChange = [keyof typeof changeLabels, number, number];

export function abyssChangeText(changes: AbyssChange[]) {
  return changes.map(([stat, before, after]) => {
    function format(value: number) {
      switch (stat) {
        case 'cooldown':
        case 'duration':
        case 'interval': return `${value}秒`;
        case 'area':
        case 'speed':
        case 'might':
        case 'magnet':
        case 'move_speed':
        case 'duration_scale':
        case 'cooldown_scale':
        case 'luck':
        case 'growth':
        case 'crit':
        case 'preserve': return `${Math.round(value * 1000) / 10}%`;
        case 'pierce': return value >= 9999 ? '无限' : String(value);
        default: return String(value);
      }
    }
    return `${changeLabels[stat]} ${format(before)} → ${format(after)}`;
  }).join(' · ');
}

type AbyssNewWeaponStat = ['damage' | 'cooldown', number];

export function abyssNewWeaponText(stats: AbyssNewWeaponStat[]) {
  return stats.map(([stat, value]) =>
    `${changeLabels[stat]} ${value}${stat === 'cooldown' ? '秒' : ''}`).join(' · ');
}

export interface AbyssPlayer {
  id: number;
  name: string;
  position: XY;
  angle: number;
  hp: number;
  max_hp: number;
  out: boolean;
  protected?: boolean;
  protection_ms?: number;
  down: number;
  rescue: number;
  rescuer?: number;
  weapons: Partial<Record<AbyssWeapon, number>>;
  passives: Partial<Record<AbyssPassive, number>>;
  choices: AbyssChoice[];
  repair_health?: Partial<Record<'repair' | 'rest', [number, number]>>;
  new_weapon_stats?: Partial<Record<AbyssWeapon, AbyssNewWeaponStat[]>>;
  choice_changes?: Partial<Record<AbyssChoice, AbyssChange[]>>;
  evolution_requirements?: Partial<Record<AbyssWeapon, { target: AbyssWeapon; passive: AbyssPassive; rank: number }>>;
  kills: number;
  damage_dealt?: number;
  xp_collected?: number;
  rescues?: number;
  move_speed: number;
  stats: AbyssStats;
}

export interface AbyssChestEvent { frame: number; player: number; text: string }

export interface AbyssWardenAttack {
  id: number;
  kind: 'strike' | 'pulse';
  impact: boolean;
  progress: number;
  duration_ms?: number;
  zones: [number, number, number][];
}

export const abyssMapNames = { small: '小', medium: '中', large: '大', endless: '无尽' };

export interface AbyssState {
  map_size?: keyof typeof abyssMapNames;
  bounds?: [number | null, number | null];
  frame: number;
  duration: number;
  seconds: number;
  level: number;
  xp: number;
  next_xp: number;
  growth_boost?: number;
  kills: number;
  salvage: number;
  boss: boolean;
  boss_dead: boolean;
  warden_enraged?: boolean;
  warden_attack?: AbyssWardenAttack | null;
  choice_seconds: number;
  result: 'won' | 'lost' | 'timeout' | 'abandoned' | null;
  players: AbyssPlayer[];
  enemies: [number, AbyssEnemy, number, number, number, number, number][];
  loot: [number, number, number][];
  chests: [number, number, number][];
  chest_events: AbyssChestEvent[];
  fields: [number, AbyssWeapon, number, number, number][];
  shots: [number, AbyssWeapon, number, number, number, number, launch?: [number, number, number]][];
  orbits: [number, AbyssWeapon, number, number, number, number][];
  auras: [number, AbyssWeapon, number, number, number][];
  effects: {
    id: number; weapon: AbyssWeapon | 'chest' | 'rescue' | 'defeat'; kind?: string; start?: number; until: number;
    points?: XY[]; x?: number; y?: number; r?: number; angle?: number; target?: number; shot?: number;
  }[];
}

export function abyssVibrationDuration(
  previous: AbyssState, current: AbyssState, uid: number, sinceLastMs: number, supportsShortPulse: boolean,
): number {
  const gap = current.frame - previous.frame;
  if (gap <= 0 || gap > 20 || previous.result) return 0;
  const before = previous.players.find(player => player.id === uid);
  const me = current.players.find(player => player.id === uid);
  if (!before || !me || me.out) return 0;
  if (before.hp > 0 && !me.hp) return 85;
  if (!before.hp && me.hp) return 55;
  if (!me.hp || current.result) return 0;
  if (!previous.boss && current.boss) return 65;
  const attack = current.warden_attack;
  if (attack && !attack.impact && attack.id !== previous.warden_attack?.id) return 40;
  if (me.hp < before.hp) return sinceLastMs >= 700 ? 25 : 0;
  return supportsShortPulse && me.kills - before.kills >= 2 && sinceLastMs >= 850 ? 16 : 0;
}

export type AbyssSummary = Pick<AbyssState, 'result'> &
  Partial<Pick<AbyssState, 'seconds' | 'level' | 'kills' | 'salvage' | 'boss' | 'boss_dead' | 'map_size'>> & {
    players: Pick<AbyssPlayer, 'id' | 'name' | 'kills' | 'hp' | 'out' | 'weapons' | 'passives' | 'xp_collected' | 'rescues' | 'damage_dealt'>[];
  };

export const abyssResults = {
  won: '回收完成', lost: '全队失压', timeout: '断缆守卫仍在活动', abandoned: '试潜中止',
};

export function abyssItemInfo(choice: AbyssChoice) {
  if (choice in abyssWeapons) {
    return { ...abyssWeapons[choice as AbyssWeapon], kind: 'weapon' as const };
  }
  if (choice in abyssPassives) {
    return { ...abyssPassives[choice as AbyssPassive], kind: 'passive' as const };
  }
  return { ...abyssUtility[choice as keyof typeof abyssUtility], maxLevel: 0, kind: 'utility' as const };
}

export function abyssLevelOf(player: AbyssPlayer, choice: AbyssChoice) {
  if (choice in abyssWeapons) return player.weapons[choice as AbyssWeapon] ?? 0;
  if (choice in abyssPassives) return player.passives[choice as AbyssPassive] ?? 0;
  return 0;
}

export function abyssEvolutionPreview(player: AbyssPlayer, choice: AbyssChoice) {
  return Object.entries(player.evolution_requirements ?? {}).flatMap(([weapon, evolution]) => {
    const rank = abyssLevelOf(player, weapon as AbyssWeapon);
    const hasPassive = !!player.passives[evolution.passive];
    const gainsLastRank = weapon === choice && hasPassive && rank + 1 === evolution.rank;
    const gainsPassive = evolution.passive === choice && !hasPassive && rank >= evolution.rank;
    return gainsLastRank || gainsPassive ? [abyssWeapons[evolution.target].title] : [];
  });
}

export function abyssClock(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}
