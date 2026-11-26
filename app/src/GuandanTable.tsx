import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  describeGuandanCard, describeGuandanCombo, guandanActionRail, guandanHandInset, guandanRanks,
  guandanTableMax, guandanTeam, guandanWideHand, isGuandanWild, layoutGuandanHand,
} from './guandan';
import type { GuandanCard, GuandanCommand, GuandanView } from './guandan';
import PlayingCardSuit from './PlayingCardSuit';
import { colors, doudizhuColors as ink, fontSize, radius, spacing } from './theme';
import { AppModal } from './ui';

type Name = (id: number) => string;
const raise = 8;

function cardLabel(card: GuandanCard, level: string) {
  const { label } = describeGuandanCard(card);
  return isGuandanWild(card, level) ? `${label}，逢人配` : card.rank === level ? `${label}，级牌` : label;
}

function CardFace({ card, level, width, height, selected, large, returning, eligible }: {
  card: GuandanCard; level: string; width: number; height: number; selected?: boolean; large?: boolean;
  returning: boolean; eligible: boolean;
}) {
  const { rank, suit, red } = describeGuandanCard(card);
  const marker = isGuandanWild(card, level) ? '配' : card.rank === level ? '级' : eligible ? '还' : '';
  const color = red ? ink.red : ink.black;
  return <View style={[styles.face, { width, minHeight: height },
    eligible && styles.faceEligible, returning && !eligible && styles.faceIneligible, selected && styles.faceSelected]}>
    <Text maxFontSizeMultiplier={large ? undefined : 1.12}
      style={[styles.rank, large && styles.largeRank, !suit && !large && styles.joker, { color }]}>
      {!suit && !large ? rank.split('').join('\n') : rank}
    </Text>
    {!!suit && <PlayingCardSuit suit={suit} color={color} size={large ? 15 : 12} />}
    {!!marker && <Text maxFontSizeMultiplier={large ? undefined : 1.12} style={styles.marker}>{marker}</Text>}
    <View style={styles.motif} />
  </View>;
}

function Seat({ game, index, position, name, offline, compact }: {
  game: GuandanView; index: number; position: string; name: Name; offline: boolean; compact: boolean;
}) {
  const id = game.players[index];
  const nickname = name(id);
  const active = game.current_id === id;
  const place = game.finish_order.indexOf(id);
  const remaining = game.remaining[index];
  const detail = [
    active && '行动中', offline && '离线',
    place >= 0 ? ['头游', '二游', '三游', '末游'][place]
      : remaining !== null && remaining !== undefined && `剩 ${remaining} 张`,
    place < 0 && game.passed.includes(id) && '过牌',
  ].filter(Boolean).join(' · ');
  return <View testID={`guandan-seat-${id}`} accessible accessibilityLabel={[position, nickname, detail].filter(Boolean).join('，')}
    style={[styles.seat, compact && styles.seatCompact, active && styles.seatActive]}>
    <Text numberOfLines={1} style={styles.seatName}><Text style={styles.muted}>{position} </Text>{nickname}</Text>
    {!!detail && <Text numberOfLines={compact ? 1 : 2} style={[styles.muted, active && styles.accent]}>{detail}</Text>}
  </View>;
}

function DealRecap({ game, uid, name }: { game: GuandanView; uid: number; name: Name }) {
  const result = game.deal_result;
  if (!result) return null;
  const winner = result.winner === guandanTeam(game.players, uid) ? '我方' : '对方';
  return <View testID="guandan-deal-result" style={styles.recap}>
    <Text accessibilityRole="header" style={styles.turn}>第 {result.deal} 副 · {winner}获胜</Text>
    {game.phase === 'between' && <Text style={styles.accent}>下一副 · {winner}打 {result.levels[result.winner]}</Text>}
    <View style={styles.ranks}>
      {guandanRanks(game.players, result).map(({ id, place }) =>
        <Text key={id} style={styles.muted}>{id === uid ? '你' : name(id)} · {place}</Text>)}
    </View>
  </View>;
}

function PublicPlay({ game, uid, name, compact, bots }: {
  game: GuandanView; uid: number; name: Name; compact: boolean; bots: number[];
}) {
  if (game.phase === 'between' || game.phase === 'finished') return <DealRecap game={game} uid={uid} name={name} />;
  const exchange = game.exchange;
  if (game.phase === 'returning' && exchange) {
    const returningBot = exchange.pairs.find(pair => !pair.submitted && bots.includes(pair.to_id));
    return <View testID="guandan-exchange" style={[styles.playArea, compact && styles.playCompact]}>
      <Text accessibilityLiveRegion="polite" style={styles.turn}>{game.return_choices.length
        ? '轮到你还贡' : returningBot ? `${name(returningBot.to_id)} 思考中…` : '等待还贡'}</Text>
      {exchange.pairs.map(pair => <Text key={pair.from_id} style={styles.muted}>
        {name(pair.from_id)} 向 {name(pair.to_id)} 进贡 {describeGuandanCard(pair.tribute).label}
        {pair.returned ? ` · 已还 ${describeGuandanCard(pair.returned).label}` : pair.submitted ? ' · 已提交还贡' : ' · 待还贡'}
      </Text>)}
    </View>;
  }
  const current = game.current_id === null ? '' : game.current_id === uid ? '你' : name(game.current_id);
  const play = game.last_play;
  const turn = play ? game.current_id === uid ? '轮到你跟牌' : `等待 ${current} 跟牌`
    : game.lead_reason === 'partner' ? `${current}接风，重新领出`
      : game.lead_reason === 'antitribute' ? `抗贡 · ${current}领出`
        : game.lead_reason === 'tribute' ? `贡还完成 · ${current}领出`
          : game.current_id === uid ? '轮到你领出' : `${current}领出`;
  const thinking = game.current_id !== null && bots.includes(game.current_id);
  const botContext = game.lead_reason === 'partner' ? ' · 接风领出'
    : game.lead_reason === 'antitribute' ? ' · 抗贡后领出'
      : game.lead_reason === 'tribute' ? ' · 贡还完成后领出' : '';
  return <View style={[styles.playArea, compact && styles.playCompact]}>
    {!!play && <>
      <Text style={styles.muted}>{play.uid === uid ? '你' : name(play.uid)} 出 {describeGuandanCombo(play, game.level)}</Text>
      <View testID="guandan-public-cards" style={styles.publicCards}>
        {play.cards.map(card => {
          const face = describeGuandanCard(card);
          const color = face.red ? ink.red : ink.black;
          return <View key={card.id} accessible accessibilityLabel={cardLabel(card, game.level)} style={styles.publicCard}>
            <Text style={[styles.publicRank, { color }]}>{face.rank}</Text>
            {!!face.suit && <PlayingCardSuit suit={face.suit} color={color} size={10} />}
          </View>;
        })}
      </View>
    </>}
    {!!current && <Text testID="guandan-turn" accessibilityLiveRegion="polite" style={styles.turn}>
      {thinking ? `${current} 思考中…${botContext}` : turn}
    </Text>}
  </View>;
}

interface ActionsProps {
  game: GuandanView;
  uid: number;
  host: boolean;
  connected: boolean;
  pending: boolean;
  previewReady: boolean;
  focus: string;
  onFocus: (target: string) => void;
  selectedCount: number;
  onAction: (action: GuandanCommand | 'hint') => void;
}

function Actions({ game, uid, host, connected, pending, previewReady, focus, onFocus, selectedCount, onAction, rail, picker }: ActionsProps & {
  rail?: boolean; picker?: boolean;
}) {
  const prefix = picker ? 'guandan-picker' : 'guandan';
  const disabled = !connected || pending;
  const waiting = disabled || game.current_id !== uid;
  const button = (action: GuandanCommand | 'hint', text: string, unavailable: boolean, primary = false) => {
    const id = `${prefix}-${action}`;
    return <Pressable testID={id} accessibilityRole="button" disabled={unavailable}
      accessibilityState={{ disabled: unavailable }} onPress={() => onAction(action)}
      onFocus={() => onFocus(id)} onBlur={() => onFocus('')}
      style={({ pressed }) => [styles.action, primary && styles.primary, unavailable && styles.unavailable,
        pressed && styles.pressed, focus === id && styles.actionFocus]}>
      <Text style={[styles.actionLabel, primary && styles.primaryLabel, unavailable && styles.muted]}>{text}</Text>
    </Pressable>;
  };
  if (game.phase === 'finished') return null;
  return <View style={[styles.actions, rail && styles.actionsRail]}>
    {game.phase === 'playing' && <>
      {button('pass', '过牌', waiting || !game.last_play)}
      {button('hint', '提示', waiting)}
      {button('play', '出牌', waiting || !selectedCount || !previewReady, true)}
    </>}
    {game.phase === 'returning' && button('return', '还贡', disabled || !game.return_choices.length || selectedCount !== 1, true)}
    {game.phase === 'between' && (host ? button('next', '下一副', disabled, true)
      : <Text style={styles.muted}>等待房主开始下一副</Text>)}
  </View>;
}

interface GuandanTableProps {
  game: GuandanView;
  uid: number;
  host: boolean;
  name: Name;
  online: Record<number, boolean>;
  bots: number[];
  connected: boolean;
  pending: boolean;
  selected: number[];
  feedback: string;
  previewReady: boolean;
  pickerOpen: boolean;
  onPicker: (visible: boolean) => void;
  onToggle: (card: GuandanCard) => void;
  onAction: (action: GuandanCommand | 'hint') => void;
  onSurrender: () => void;
  onLeave: () => void;
}

export default function GuandanTable({
  game, uid, host, name, online, bots, connected, pending, selected, feedback, previewReady, pickerOpen,
  onPicker, onToggle, onAction, onSurrender, onLeave,
}: GuandanTableProps) {
  const { width, height, fontScale = 1 } = useWindowDimensions();
  const { left = 0, right = 0, top = 0, bottom = 0 } = useSafeAreaInsets();
  const [space, setSpace] = useState({ width: 0, height: 0 });
  const [focus, setFocus] = useState('');
  const tableWidth = Math.min(guandanTableMax, width - left - right, space.width || guandanTableMax);
  const usableHeight = Math.min(height - top - bottom, space.height || height);
  const compact = usableHeight < 400;
  const returning = game.phase === 'returning';
  const showHand = game.phase === 'playing' || returning;
  const rail = compact && tableWidth >= 520 && showHand;
  const wide = !compact && tableWidth - 2 * guandanHandInset >= guandanWideHand;
  const { rows, cardWidth, step } = layoutGuandanHand(game.hand, tableWidth - 2 * guandanHandInset - (rail ? guandanActionRail : 0));
  const handHeight = compact ? 48 : wide ? 82 : 55;
  const me = game.players.indexOf(uid);
  const team = guandanTeam(game.players, uid) ?? 0;
  const seat = (offset: number, position: string) => {
    const index = (me + offset) % game.players.length;
    return <Seat game={game} index={index} position={position} name={name} compact={compact}
      offline={online[game.players[index]] === false} />;
  };
  const upstream = seat(3, '上家');
  const partner = seat(2, '对家');
  const downstream = seat(1, '下家');
  const publicPlay = <PublicPlay game={game} uid={uid} name={name} compact={compact} bots={bots} />;
  const selection = new Set(selected);
  const actionProps = { game, uid, host, connected, pending, previewReady, focus, onFocus: setFocus, selectedCount: selected.length, onAction };
  const eligible = (card: GuandanCard) => game.return_choices.includes(card.id);
  const cardProps = (card: GuandanCard, testID: string) => {
    const disabled = !connected || pending || !showHand || (returning && !eligible(card));
    return {
      testID, accessibilityRole: 'button' as const, accessibilityLabel: cardLabel(card, game.level),
      accessibilityHint: eligible(card) ? '可以还贡' : undefined,
      accessibilityState: { selected: selection.has(card.id), disabled }, 'aria-pressed': selection.has(card.id),
      disabled, onPress: () => onToggle(card), onFocus: () => setFocus(testID), onBlur: () => setFocus(''),
    };
  };
  const note = !connected ? '正在重新同步对局…' : pending ? '等待服务器确认…' : feedback;
  const gridWidth = Math.max(52, Math.round(35 * fontScale));
  const gridHeight = Math.max(68, Math.round(45 * fontScale));
  return <View style={styles.felt}>
    <View testID="guandan-table" style={styles.table}
      onLayout={({ nativeEvent: { layout } }) => setSpace({ width: layout.width, height: layout.height })}>
      <View style={styles.score} testID="guandan-score">
        <Text style={styles.muted}>我方 <Text style={styles.scoreValue}>{game.levels[team]}</Text></Text>
        <Text style={styles.scoreValue}>第 {game.deal} 副 · {game.level_team === null ? '' : game.level_team === team ? '我方' : '对方'}打 {game.level}</Text>
        <Text style={styles.muted}>对方 <Text style={styles.scoreValue}>{game.levels[1 - team]}</Text></Text>
      </View>
      <ScrollView style={styles.fieldScroll} contentContainerStyle={[styles.field, compact && styles.fieldCompact]}>
        {wide ? <>
          <View style={styles.partner}>{partner}</View>
          <View style={styles.wideArena}>
            <View style={styles.side}>{upstream}</View>
            {publicPlay}
            <View style={styles.side}>{downstream}</View>
          </View>
        </> : <>
          <View style={styles.seats}>{upstream}{partner}{downstream}</View>
          {publicPlay}
        </>}
      </ScrollView>
      <View style={styles.playerArea}>
        <View style={styles.playerHeading}>
          <Text testID={`guandan-seat-${uid}`} accessibilityLabel={`${name(uid)}，你，剩 ${game.hand.length} 张`}
            style={styles.playerName}>你 · {game.current_id === uid ? '行动中 · ' : ''}剩 {game.hand.length} 张</Text>
          {game.phase !== 'finished' && <Pressable testID="guandan-surrender" accessibilityRole="button"
            onPress={onSurrender} style={styles.textButton}><Text style={styles.muted}>认输</Text></Pressable>}
          <Pressable testID="guandan-leave" accessibilityRole="button" accessibilityLabel="离开房间"
            onPress={onLeave} style={styles.textButton}><Text style={styles.muted}>离开</Text></Pressable>
          {showHand && game.hand.length > 0 && <Pressable testID="guandan-expand" accessibilityRole="button"
            aria-expanded={pickerOpen} onPress={() => onPicker(true)} style={styles.textButton}>
            <Text style={styles.accent}>放大选牌</Text>
          </Pressable>}
        </View>
        {!!note && <Text testID="guandan-feedback" accessibilityLiveRegion="polite" style={styles.feedback}>{note}</Text>}
        <View style={rail && styles.handAndRail}>
          {showHand && <View style={styles.hand} testID="guandan-hand">
            {rows.map((row, index) => <View key={index} style={[
              styles.handRow, { width: cardWidth + step * (row.length - 1), height: handHeight + raise },
            ]}>
              {row.map((card, position) => <View key={card.id} pointerEvents="none" accessible={false}
                importantForAccessibility="no-hide-descendants" accessibilityElementsHidden aria-hidden
                style={[styles.facePosition, { left: position * step, top: selection.has(card.id) ? 0 : raise }]}>
                <CardFace card={card} level={game.level} width={cardWidth} height={handHeight} selected={selection.has(card.id)}
                  returning={returning} eligible={eligible(card)} />
              </View>)}
              <View style={styles.hitRow} testID={`guandan-hand-row-${index + 1}`}>
                {row.map((card, position) => {
                  const key = `guandan-card-${card.id}`;
                  return <Pressable key={key} {...cardProps(card, key)}
                    style={[{ width: position === row.length - 1 ? cardWidth : step }, focus === key && styles.focus]} />;
                })}
              </View>
            </View>)}
          </View>}
          <Actions {...actionProps} rail={rail} />
        </View>
      </View>
    </View>
    <AppModal visible={pickerOpen} onClose={() => onPicker(false)} testID="guandan-picker" cardStyle={styles.pickerCard}>
      <View style={[styles.picker, { height: Math.max(188, Math.min(516, height - top - bottom - 60)) }]}>
        <View style={styles.pickerHeading}>
          <View style={styles.pickerTitles}>
            <Text accessibilityRole="header" style={styles.pickerTitle}>放大选牌</Text>
            <Text accessibilityLiveRegion="polite" style={styles.pickerCount}>已选 {selected.length} 张</Text>
          </View>
          <Pressable testID="guandan-picker-close" accessibilityRole="button" accessibilityLabel="关闭放大选牌"
            style={styles.textButton} onPress={() => onPicker(false)}><Text style={styles.pickerClose}>关闭</Text></Pressable>
        </View>
        <ScrollView style={styles.fieldScroll} contentContainerStyle={styles.grid} keyboardShouldPersistTaps="handled">
          {game.hand.map(card => {
            const key = `guandan-large-card-${card.id}`;
            return <Pressable key={key} {...cardProps(card, key)} style={focus === key && styles.focus}>
              <View accessible={false} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden aria-hidden>
                <CardFace card={card} level={game.level} width={gridWidth} height={gridHeight} selected={selection.has(card.id)}
                  returning={returning} eligible={eligible(card)} large />
              </View>
            </Pressable>;
          })}
        </ScrollView>
        {!!note && <Text accessibilityLiveRegion="polite" style={styles.modalFeedback}>{note}</Text>}
        <Actions {...actionProps} picker />
      </View>
    </AppModal>
  </View>;
}

const styles = StyleSheet.create({
  felt: { flex: 1, minHeight: 0, backgroundColor: ink.felt },
  table: { flex: 1, minHeight: 0, width: '100%', maxWidth: guandanTableMax, alignSelf: 'center' },
  score: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap',
    gap: spacing.xs, minHeight: 36, paddingHorizontal: guandanHandInset, paddingVertical: spacing.xs, borderBottomWidth: 1, borderColor: ink.line },
  scoreValue: { color: ink.light, fontSize: fontSize.sm, fontWeight: '800' },
  fieldScroll: { flex: 1, minHeight: 0 },
  field: { flexGrow: 1, gap: spacing.sm, paddingHorizontal: guandanHandInset, paddingVertical: spacing.sm },
  fieldCompact: { paddingVertical: spacing.xs, gap: spacing.xs },
  wideArena: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  seats: { flexDirection: 'row', gap: spacing.xs },
  side: { width: '21%', maxWidth: 184 },
  partner: { alignSelf: 'center', width: '21%', maxWidth: 184 },
  seat: { flexGrow: 1, flexBasis: 0, minWidth: 0, borderRadius: radius.md, borderWidth: 2,
    borderColor: ink.line, backgroundColor: ink.panel, padding: spacing.sm, gap: 2 },
  seatCompact: { padding: spacing.xs },
  seatActive: { borderColor: ink.active },
  seatName: { color: ink.light, fontSize: fontSize.sm, fontWeight: '700' },
  muted: { color: ink.muted, fontSize: fontSize.xs },
  accent: { color: ink.gold, fontSize: fontSize.xs, fontWeight: '700' },
  playArea: { flex: 1, gap: spacing.xs, minHeight: 62, alignItems: 'center', justifyContent: 'center', padding: spacing.xs },
  playCompact: { minHeight: 24, paddingVertical: 0 },
  publicCards: { flexDirection: 'row', justifyContent: 'center', flexWrap: 'wrap', gap: spacing.xs, width: '100%' },
  publicCard: { backgroundColor: ink.paper, borderColor: ink.cardEdge, borderWidth: 1, borderRadius: radius.sm,
    flexDirection: 'row', alignItems: 'center', gap: 2, paddingHorizontal: 4, paddingVertical: 2 },
  publicRank: { fontSize: fontSize.xs, fontWeight: '800' },
  turn: { color: ink.light, fontSize: fontSize.sm, textAlign: 'center', fontWeight: '800' },
  recap: { flex: 1, minWidth: 0, gap: spacing.xs, alignItems: 'center', justifyContent: 'center', paddingVertical: spacing.sm },
  ranks: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: spacing.sm },
  playerArea: { borderTopWidth: 1, borderTopColor: ink.line, paddingHorizontal: guandanHandInset, paddingBottom: spacing.xs, gap: spacing.xs },
  playerHeading: { minHeight: 48, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' },
  playerName: { flex: 1, minWidth: 60, color: ink.light, fontSize: fontSize.xs, fontWeight: '700' },
  textButton: { paddingHorizontal: spacing.xs, minHeight: 48, minWidth: 48, alignItems: 'center', justifyContent: 'center' },
  handAndRail: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  hand: { flexShrink: 1, alignItems: 'center', gap: 3, paddingBottom: 2 },
  handRow: { position: 'relative' },
  facePosition: { position: 'absolute' },
  hitRow: { flex: 1, flexDirection: 'row' },
  face: { backgroundColor: ink.paper, borderWidth: 1, borderColor: ink.cardEdge, borderRadius: radius.sm,
    paddingTop: 2, paddingLeft: 4, overflow: 'hidden' },
  faceEligible: { borderColor: ink.selected },
  faceIneligible: { backgroundColor: ink.muted },
  faceSelected: { backgroundColor: ink.selectedPaper, borderColor: ink.selected, borderWidth: 2, paddingTop: 1, paddingLeft: 3 },
  motif: { position: 'absolute', right: -10, bottom: -10, width: 37, height: 33,
    borderColor: ink.motif, borderWidth: 1, borderRadius: radius.sm, transform: [{ rotate: '38deg' }] },
  rank: { fontSize: 15, fontWeight: '800', lineHeight: 16, fontVariant: ['tabular-nums'] },
  joker: { fontSize: 13, lineHeight: 13 },
  largeRank: { fontSize: fontSize.lg, lineHeight: 22 },
  marker: { fontSize: 10, lineHeight: 12, color: ink.selected, fontWeight: '800' },
  focus: { borderWidth: 2, borderColor: ink.active, borderRadius: radius.sm },
  actions: { alignSelf: 'center', flexDirection: 'row', justifyContent: 'center', alignItems: 'center',
    width: '100%', maxWidth: 400, gap: spacing.xs },
  actionsRail: { flexDirection: 'column', alignItems: 'stretch', width: guandanActionRail - spacing.sm },
  action: { flexGrow: 1, flexBasis: 0, minHeight: 48, minWidth: 48, alignItems: 'center', justifyContent: 'center',
    paddingVertical: spacing.xs, paddingHorizontal: spacing.xs, backgroundColor: ink.panel,
    borderWidth: 1, borderColor: ink.line, borderRadius: radius.md },
  primary: { backgroundColor: ink.active, borderColor: ink.active },
  actionFocus: { borderWidth: 2, borderColor: ink.gold },
  unavailable: { backgroundColor: ink.panel, borderColor: ink.panel },
  pressed: { opacity: 0.8 },
  actionLabel: { color: ink.light, fontSize: fontSize.sm, fontWeight: '700', textAlign: 'center' },
  primaryLabel: { color: ink.black },
  feedback: { color: ink.gold, textAlign: 'center', fontSize: fontSize.xs },
  pickerCard: { maxWidth: 500, padding: spacing.sm },
  picker: { gap: spacing.xs },
  pickerHeading: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  pickerTitles: { flex: 1 },
  pickerTitle: { color: colors.text, fontSize: fontSize.md, fontWeight: '800' },
  pickerCount: { color: colors.textSecondary, fontSize: fontSize.xs },
  pickerClose: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '700' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', alignContent: 'flex-start', justifyContent: 'center', gap: spacing.sm, padding: 2 },
  modalFeedback: { color: colors.warning, textAlign: 'center', fontSize: fontSize.xs },
});
