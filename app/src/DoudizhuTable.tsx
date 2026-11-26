import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Svg, { Path } from 'react-native-svg';
import type { RoomSetting } from './api';
import { describeDoudizhuCard, doudizhuCardWidth, doudizhuRole, layoutDoudizhuHand } from './doudizhu';
import type { DoudizhuAction, DoudizhuView } from './doudizhu';
import { boldSegments } from './richText';
import { settingBlurb } from './roomSettings';
import { colors, doudizhuColors as ink, fontSize, radius, spacing } from './theme';
import { AppModal } from './ui';

type Name = (id: number) => string;

const tableMax = 1120;
const edge = 12;
const raise = 8;

const suitPaths: Record<string, string> = {
  '♠': 'M8 1C5 5 1 7 1 10a3.5 3.5 0 0 0 6 2l-1 3h4l-1-3a3.5 3.5 0 0 0 6-2c0-3-4-5-7-9Z',
  '♥': 'M8 14 2 8a4 4 0 0 1 6-5 4 4 0 0 1 6 5Z',
  '♣': 'M6 7a3 3 0 1 1 4 0 3.5 3.5 0 1 1-1 6l1 2H6l1-2a3.5 3.5 0 1 1-1-6Z',
  '♦': 'M8 1 14 8 8 15 2 8Z',
};

function CardSuit({ suit, color, size }: { suit: string; color: string; size: number }) {
  return <Svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
    <Path d={suitPaths[suit]} fill={color} />
  </Svg>;
}

function CardFace({ card, width, height, selected, small, large }: {
  card?: string; width: number; height: number; selected?: boolean; small?: boolean; large?: boolean;
}) {
  if (!card) return <View style={[styles.cardBack, { width, height }]}>
    <View style={styles.cardBackInset} />
  </View>;
  const { rank, suit, red } = describeDoudizhuCard(card);
  const color = red ? ink.red : ink.black;
  return <View style={[styles.cardFace, { width, minHeight: height }, selected && styles.cardSelected]}>
    <Text maxFontSizeMultiplier={large ? undefined : small ? 1 : 1.15} style={[
      styles.rank, { color }, small && styles.smallRank, large && styles.largeRank,
      !suit && (large ? styles.largeJoker : small ? styles.smallJoker : styles.joker),
    ]}>{rank}</Text>
    {!!suit && <CardSuit suit={suit} color={color} size={large ? 15 : small ? 10 : 12} />}
    {!small && <View style={styles.cardMotif} />}
  </View>;
}

function Seat({ game, index, name, wide, offline }: {
  game: DoudizhuView; index: number; name: Name; wide: boolean; offline: boolean;
}) {
  const id = game.seat_uids[index];
  const action = game.actions[index];
  const remaining = game.remaining[index];
  const active = id === game.current_id;
  const low = remaining <= 2;
  const actionLabel = action?.kind === 'bid'
    ? action.bid === 0 ? '不叫' : `叫 ${action.bid} 分`
    : action?.kind === 'pass' ? '不出' : action?.kind === 'play' ? '已出牌' : '';
  return <View testID={`doudizhu-seat-${id}`} style={[styles.seat, !wide && styles.topSeat, active && styles.activeSeat, low && styles.lowSeat]}>
    <View style={styles.seatDetails}>
      <Text numberOfLines={1} style={styles.seatName}>{name(id)}</Text>
      <Text numberOfLines={2} style={styles.muted}>{doudizhuRole(id, game.landlord_id)}</Text>
    </View>
    <View style={styles.seatCount}>
      <Text style={[styles.count, low && styles.gold]}>{remaining}</Text>
      <Text style={styles.muted}>张</Text>
    </View>
    {(active || offline || !!actionLabel) && <Text numberOfLines={2} style={[styles.seatAction, active && styles.gold]}>
      {[active && '行动中', offline && '离线', actionLabel].filter(Boolean).join(' · ')}
    </Text>}
  </View>;
}

function PublicPlay({ game, uid, name, compact }: { game: DoudizhuView; uid: number; name: Name; compact: boolean }) {
  const current = game.current_id === null ? '' : game.current_id === uid ? '你' : name(game.current_id);
  const play = game.last_play;
  let turn = '正在结算';
  if (game.phase === 'bidding') turn = game.current_id === uid ? '轮到你叫分' : `等待 ${current} 叫分`;
  if (game.phase === 'playing') {
    if (game.current_id === uid) turn = play ? `轮到你跟牌 · ${play.label}` : '轮到你领出';
    else turn = play ? `等待 ${current} 跟牌` : `${current} 领出`;
  }
  if (game.current_id !== null && game.current_id < 0) turn = `${current} 思考中…`;
  const cards = play?.cards ?? [];
  // The server orders the body before the attachments. This split only changes their spacing.
  const bodyCount = !play ? 0
    : play.kind === 'airplane_single' ? cards.length * 3 / 4
    : play.kind === 'airplane_pair' ? cards.length * 3 / 5
    : play.kind === 'four_two_singles' || play.kind === 'four_two_pairs' ? 4
    : play.kind === 'triple_single' || play.kind === 'triple_pair' ? 3 : cards.length;
  const groups = [cards.slice(0, bodyCount), cards.slice(bodyCount)].filter(group => group.length > 0);
  return <View style={[styles.playArea, compact && styles.playAreaCompact]} testID="doudizhu-public-play">
    {play && <Text numberOfLines={2} style={styles.muted}>
      {play.player_id === uid ? '你' : name(play.player_id)}出牌 · {play.label} · {cards.length} 张
    </Text>}
    {play && <View style={styles.publicCards} accessibilityLabel={cards.map(card => describeDoudizhuCard(card).label).join('、')}>
      {groups.map((group, index) => <View key={index} style={styles.publicGroup}>
        {group.map(card => {
          if (!compact) return <CardFace key={card} card={card} width={25} height={38} small />;
          const { rank, suit, red } = describeDoudizhuCard(card);
          const color = red ? ink.red : ink.black;
          return <View key={card} style={styles.compactCard}>
            <Text style={[styles.compactRank, { color }]}>{rank}</Text>
            {!!suit && <CardSuit suit={suit} color={color} size={9} />}
          </View>;
        })}
      </View>)}
    </View>}
    <Text accessibilityLiveRegion="polite" style={styles.turn}>{turn}</Text>
  </View>;
}

function ActionButton({ id, label, disabled, primary, onPress }: {
  id: string; label: string; disabled: boolean; primary?: boolean; onPress: () => void;
}) {
  return <Pressable testID={id} accessibilityRole="button" accessibilityState={{ disabled }}
    disabled={disabled} onPress={onPress} style={({ pressed }) => [
      styles.action, primary && styles.primary, disabled && styles.actionDisabled, pressed && styles.pressed,
    ]}>
    <Text style={[styles.actionLabel, primary && styles.primaryLabel, disabled && styles.disabledLabel]}>{label}</Text>
  </Pressable>;
}

function ActionBar({ game, uid, connected, pending, selected, feedback, onAction, large }: {
  game: DoudizhuView; uid: number; connected: boolean; pending: boolean; selected: string[];
  feedback: string; onAction: (action: DoudizhuAction) => void; large?: boolean;
}) {
  const disabled = !connected || pending || game.current_id !== uid || game.phase === 'finished';
  const prefix = large ? 'doudizhu-large' : 'doudizhu';
  const note = !connected ? '正在重新同步对局…' : feedback || (pending ? '等待服务器确认…' : '');
  return <View style={styles.actionBar}>
    {!!note && <Text accessibilityLiveRegion="polite" style={[styles.feedback, large && styles.modalFeedback]}>{note}</Text>}
    {game.phase === 'bidding' && <View style={styles.actionRow}>
      {[0, 1, 2, 3].map(bid => <ActionButton key={bid} id={`${prefix}-bid-${bid}`}
        label={bid === 0 ? '不叫' : `${bid} 分`} primary={bid === 3}
        disabled={disabled || !game.available_bids.includes(bid)} onPress={() => onAction({ action: 'bid', bid })} />)}
    </View>}
    {game.phase === 'playing' && <View style={styles.actionRow}>
      <ActionButton id={`${prefix}-pass`} label="不出" disabled={disabled || !game.can_pass} onPress={() => onAction({ action: 'pass' })} />
      <ActionButton id={`${prefix}-hint`} label="提示" disabled={disabled} onPress={() => onAction({ action: 'hint' })} />
      <ActionButton id={`${prefix}-play`} label="出牌" primary disabled={disabled || selected.length === 0}
        onPress={() => onAction({ action: 'play', cards: selected })} />
    </View>}
  </View>;
}

interface DoudizhuTableProps {
  game: DoudizhuView;
  uid: number;
  name: Name;
  connected: boolean;
  pending: boolean;
  selected: string[];
  settings: RoomSetting[];
  online: Record<number, boolean>;
  feedback: string;
  pickerOpen: boolean;
  onPicker: (visible: boolean) => void;
  onToggle: (card: string) => void;
  onAction: (action: DoudizhuAction) => void;
  onSurrender: () => void;
  onLeave: () => void;
}

export default function DoudizhuTable({
  game, uid, name, connected, pending, selected, settings, online, feedback,
  pickerOpen, onPicker, onToggle, onAction, onSurrender, onLeave,
}: DoudizhuTableProps) {
  const { width, height, fontScale = 1 } = useWindowDimensions();
  const { left = 0, right = 0 } = useSafeAreaInsets();
  const [space, setSpace] = useState({ width: 0, height: 0 });
  const [rulesOpen, setRulesOpen] = useState(false);
  const tableWidth = Math.min(tableMax, width - left - right, space.width || tableMax);
  const wide = tableWidth >= 580;
  const compact = Math.min(height, space.height || height) < 345;
  const handHeight = compact ? 50 : 70;
  const { rows, step } = layoutDoudizhuHand(game.hand, tableWidth - edge * 2);
  const myIndex = game.seat_uids.indexOf(uid);
  const rightIndex = (myIndex + 1) % game.seat_uids.length;
  const leftIndex = (myIndex + 2) % game.seat_uids.length;
  const canSelect = game.phase === 'playing' && !pending && connected;
  const selection = new Set(selected);
  const leftSeat = <Seat game={game} index={leftIndex} name={name} wide={wide} offline={online[game.seat_uids[leftIndex]] === false} />;
  const rightSeat = <Seat game={game} index={rightIndex} name={name} wide={wide} offline={online[game.seat_uids[rightIndex]] === false} />;
  const actionProps = { game, uid, connected, pending, selected, feedback, onAction };
  const ruleLabel = settings.find(item => item.in_summary)?.value_label;
  const gridWidth = Math.max(52, Math.round(33 * fontScale));
  const gridHeight = Math.max(64, Math.round(40 * fontScale));
  return <View style={styles.safeArea}>
    <View testID="doudizhu-table" style={styles.table}
      onLayout={({ nativeEvent: { layout } }) => setSpace({ width: layout.width, height: layout.height })}>
      <ScrollView style={styles.fieldScroll} contentContainerStyle={[styles.field, compact && styles.fieldCompact]}>
        <View style={styles.bottomCards} testID="doudizhu-bottom">
          <Text style={styles.muted}>底牌</Text>
          {[0, 1, 2].map(index => <CardFace key={index} card={game.landlord_id !== null ? game.bottom[index] : undefined}
            width={29} height={compact ? 33 : 38} small />)}
          <Text style={styles.bidStatus}>{game.landlord_id === null
            ? `最高叫分 ${game.bid}` : `底分 ${game.bid} · ×${game.multiplier}`}</Text>
          {!!ruleLabel && <Pressable testID="doudizhu-rules" accessibilityRole="button"
            accessibilityLabel={`查看本局房规，${ruleLabel}`} style={styles.ruleButton} onPress={() => setRulesOpen(true)}>
            <Text style={styles.expandLabel}>{ruleLabel}</Text>
          </Pressable>}
        </View>
        <View style={[styles.arena, wide && styles.arenaWide]}>
          {wide
            ? <View style={styles.side}>{leftSeat}</View>
            : <View style={styles.topSeats}>{leftSeat}{rightSeat}</View>}
          <PublicPlay game={game} uid={uid} name={name} compact={compact} />
          {wide && <View style={styles.side}>{rightSeat}</View>}
        </View>
      </ScrollView>
      <View style={styles.playerArea}>
        <ActionBar {...actionProps} />
        <View style={styles.playerHeading} testID={`doudizhu-seat-${uid}`}>
          <Text style={styles.playerName} numberOfLines={2}>
            你 · {doudizhuRole(uid, game.landlord_id)}{game.current_id === uid ? ' · 行动中' : ''}
          </Text>
          {game.phase === 'playing' && <Pressable testID="doudizhu-surrender" accessibilityRole="button"
            onPress={onSurrender} style={styles.plainButton}><Text style={styles.plainLabel}>认输</Text></Pressable>}
          <Pressable testID="doudizhu-leave" accessibilityRole="button" onPress={onLeave} style={styles.plainButton}>
            <Text style={styles.plainLabel}>离开房间</Text>
          </Pressable>
          <Pressable testID="doudizhu-expand" accessibilityRole="button" aria-expanded={pickerOpen}
            onPress={() => onPicker(true)} style={styles.plainButton}><Text style={styles.expandLabel}>放大选牌</Text></Pressable>
        </View>
        <View style={styles.hand} testID="doudizhu-hand">
          {rows.map((row, index) => <View key={index} testID={`doudizhu-hand-row-${index + 1}`} style={[
            styles.handRow, { width: doudizhuCardWidth + step * (row.length - 1), height: handHeight + raise },
          ]}>
            {row.map((card, position) => <View key={card} pointerEvents="none" accessible={false}
              importantForAccessibility="no-hide-descendants" accessibilityElementsHidden aria-hidden
              style={[styles.facePosition, { left: position * step, top: selection.has(card) ? 0 : raise }]}>
              <CardFace card={card} width={doudizhuCardWidth} height={handHeight} selected={selection.has(card)} />
            </View>)}
            <View style={styles.hitRow}>
              {row.map((card, position) => <Pressable key={card} testID={`doudizhu-card-${card}`} accessibilityRole="button"
                accessibilityLabel={describeDoudizhuCard(card).label} aria-pressed={selection.has(card)}
                accessibilityState={{ selected: selection.has(card), disabled: !canSelect }} disabled={!canSelect}
                style={{ width: position === row.length - 1 ? doudizhuCardWidth : step }}
                onPress={() => onToggle(card)} />)}
            </View>
          </View>)}
        </View>
      </View>
    </View>
    <AppModal visible={pickerOpen} onClose={() => onPicker(false)} testID="doudizhu-picker" cardStyle={styles.modalCard}>
      <View style={[styles.picker, { height: Math.max(182, Math.min(470, height - 72)) }]}>
        <View style={styles.pickerHeading}>
          <View style={styles.pickerTitles}>
            <Text accessibilityRole="header" style={styles.pickerTitle}>放大选牌</Text>
            <Text style={styles.pickerCount} accessibilityLiveRegion="polite">已选 {selected.length} 张</Text>
          </View>
          <Pressable testID="doudizhu-picker-close" accessibilityRole="button" accessibilityLabel="关闭放大选牌"
            onPress={() => onPicker(false)} style={styles.pickerClose}><Text style={styles.pickerCloseLabel}>关闭</Text></Pressable>
        </View>
        <ScrollView style={styles.pickerScroll} contentContainerStyle={[styles.grid, { maxWidth: 5 * gridWidth + 4 * spacing.sm }]}>
          {rows.flat().map(card => <Pressable key={card} testID={`doudizhu-large-card-${card}`} accessibilityRole="button"
            accessibilityLabel={describeDoudizhuCard(card).label} aria-pressed={selection.has(card)}
            accessibilityState={{ selected: selection.has(card), disabled: !canSelect }}
            disabled={!canSelect} onPress={() => onToggle(card)}>
            <CardFace card={card} width={gridWidth} height={gridHeight} selected={selection.has(card)} large />
          </Pressable>)}
        </ScrollView>
        <ActionBar {...actionProps} large />
      </View>
    </AppModal>
    <AppModal visible={rulesOpen} onClose={() => setRulesOpen(false)}>
      <Text accessibilityRole="header" style={styles.pickerTitle}>本局房规</Text>
      {settings.map(item => <View key={item.key} style={styles.rule}>
        <Text style={styles.ruleValue}>{item.label} · {item.value_label}</Text>
        <Text style={styles.pickerCount}>{boldSegments(settingBlurb(item), styles.ruleStrong)}</Text>
      </View>)}
      <Pressable accessibilityRole="button" onPress={() => setRulesOpen(false)} style={styles.pickerClose}>
        <Text style={styles.pickerCloseLabel}>关闭</Text>
      </Pressable>
    </AppModal>
  </View>;
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, minHeight: 0, backgroundColor: ink.felt },
  table: { flex: 1, minHeight: 0, width: '100%', maxWidth: tableMax, alignSelf: 'center' },
  fieldScroll: { flex: 1, minHeight: 0 },
  field: { flexGrow: 1, paddingHorizontal: edge, paddingTop: spacing.md, gap: spacing.md },
  fieldCompact: { paddingTop: spacing.xs, gap: spacing.xs },
  bottomCards: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs },
  bidStatus: { color: ink.light, fontSize: fontSize.xs, fontWeight: '700', marginLeft: spacing.sm, flexShrink: 1 },
  ruleButton: { marginLeft: spacing.xs, minHeight: 44, minWidth: 44, paddingHorizontal: spacing.xs,
    alignItems: 'center', justifyContent: 'center' },
  arena: { flex: 1, gap: spacing.sm },
  arenaWide: { flexDirection: 'row', alignItems: 'center' },
  topSeats: { flexDirection: 'row', gap: spacing.sm },
  side: { width: '21%', maxWidth: 184 },
  seat: { minWidth: 0, backgroundColor: ink.panel, borderRadius: radius.md, borderWidth: 1,
    borderColor: ink.line, flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: spacing.xs, padding: spacing.sm },
  topSeat: { flex: 1 },
  activeSeat: { borderColor: ink.active },
  lowSeat: { borderColor: ink.gold },
  seatDetails: { flex: 1, minWidth: 36 },
  seatName: { color: ink.light, fontSize: fontSize.sm, fontWeight: '700' },
  seatCount: { flexDirection: 'row', gap: spacing.xs, alignItems: 'baseline' },
  count: { color: ink.light, fontSize: fontSize.lg, fontWeight: '800', fontVariant: ['tabular-nums'] },
  gold: { color: ink.gold },
  muted: { color: ink.muted, fontSize: fontSize.xs },
  seatAction: { width: '100%', color: ink.muted, fontSize: fontSize.xs, fontWeight: '600' },
  playArea: { flex: 1, minHeight: 85, alignItems: 'center', justifyContent: 'center', gap: spacing.xs, padding: spacing.xs },
  playAreaCompact: { minHeight: 34, paddingVertical: 0 },
  publicCards: { flexDirection: 'row', width: '100%', flexWrap: 'wrap', justifyContent: 'center', gap: spacing.xs },
  publicGroup: { flexDirection: 'row', minWidth: 0, flexShrink: 1, flexWrap: 'wrap', justifyContent: 'center', gap: 2 },
  turn: { color: ink.light, fontSize: fontSize.sm, fontWeight: '700', textAlign: 'center' },
  compactCard: { flexDirection: 'row', alignItems: 'center', gap: 1, paddingHorizontal: 3,
    borderRadius: radius.sm, backgroundColor: ink.paper },
  compactRank: { fontSize: 11, fontWeight: '700' },
  playerArea: { borderTopWidth: 1, borderTopColor: ink.line, paddingHorizontal: edge, paddingTop: spacing.xs, paddingBottom: spacing.sm },
  actionBar: { gap: spacing.xs },
  actionRow: { alignSelf: 'center', width: '100%', maxWidth: 400, flexDirection: 'row', gap: spacing.xs },
  action: { flex: 1, minHeight: 48, minWidth: 48, alignItems: 'center', justifyContent: 'center',
    paddingVertical: spacing.xs, paddingHorizontal: spacing.xs, borderWidth: 1, borderColor: ink.line,
    backgroundColor: ink.panel, borderRadius: radius.md },
  primary: { backgroundColor: ink.active, borderColor: ink.active },
  actionDisabled: { backgroundColor: ink.panel, borderColor: ink.panel },
  pressed: { opacity: 0.8 },
  actionLabel: { color: ink.light, fontSize: fontSize.sm, textAlign: 'center', fontWeight: '700' },
  disabledLabel: { color: ink.muted },
  primaryLabel: { color: ink.black },
  feedback: { color: ink.gold, fontSize: fontSize.xs, textAlign: 'center' },
  modalFeedback: { color: colors.warning },
  playerHeading: { alignSelf: 'center', width: '100%', maxWidth: 850, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' },
  playerName: { flex: 1, minWidth: 51, color: ink.light, fontSize: fontSize.xs, fontWeight: '700' },
  plainButton: { minWidth: 48, minHeight: 48, paddingHorizontal: 5, alignItems: 'center', justifyContent: 'center' },
  plainLabel: { color: ink.muted, fontSize: fontSize.xs },
  expandLabel: { color: ink.active, fontSize: fontSize.xs, fontWeight: '700' },
  hand: { alignItems: 'center', gap: spacing.xs },
  handRow: { position: 'relative' },
  facePosition: { position: 'absolute' },
  hitRow: { position: 'absolute', left: 0, top: 0, right: 0, bottom: 0, flexDirection: 'row' },
  cardFace: { backgroundColor: ink.paper, borderWidth: 1, borderColor: ink.cardEdge, borderRadius: radius.sm,
    paddingLeft: 3, paddingTop: 2, overflow: 'hidden' },
  cardSelected: { borderColor: ink.selected, backgroundColor: ink.selectedPaper, borderWidth: 2, paddingLeft: 2, paddingTop: 1 },
  rank: { fontSize: fontSize.md, fontWeight: '800', includeFontPadding: false },
  smallRank: { fontSize: fontSize.xs },
  largeRank: { fontSize: fontSize.xl },
  joker: { fontSize: 10, lineHeight: 15 },
  smallJoker: { fontSize: 10 },
  largeJoker: { fontSize: fontSize.sm },
  cardMotif: { position: 'absolute', bottom: 7, right: 8, width: 14, height: 14, transform: [{ rotate: '45deg' }],
    borderWidth: 1, borderColor: ink.motif },
  cardBack: { backgroundColor: ink.back, borderWidth: 1, borderColor: ink.active, borderRadius: radius.sm, padding: 4 },
  cardBackInset: { flex: 1, borderWidth: 1, borderColor: ink.active, borderRadius: 2 },
  modalCard: { maxWidth: 460, padding: spacing.md },
  picker: { gap: spacing.sm },
  pickerHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.sm },
  pickerTitles: { flex: 1 },
  pickerTitle: { color: colors.text, fontSize: fontSize.md, fontWeight: '800' },
  pickerCount: { color: colors.textMuted, fontSize: fontSize.xs },
  pickerClose: { paddingHorizontal: spacing.sm, minWidth: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center' },
  pickerCloseLabel: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '700' },
  pickerScroll: { flex: 1, minHeight: 0 },
  grid: { width: '100%', flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, justifyContent: 'center', alignSelf: 'center' },
  rule: { gap: spacing.xs },
  ruleValue: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  ruleStrong: { fontWeight: '700' },
});
