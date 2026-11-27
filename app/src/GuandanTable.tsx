import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  describeGuandanCard, describeGuandanCombo, describeGuandanPlay, guandanPlaces, guandanRanks, guandanTeam, isGuandanWild, layoutGuandanHand,
} from './guandan';
import type { GuandanCard, GuandanCommand, GuandanView } from './guandan';
import PlayingCardSuit from './PlayingCardSuit';
import PokerPlayHistory from './PokerPlayHistory';
import { pokerCardRaise as raise, PokerSeat, pokerStyles, pokerTableEdge as edge, pokerTableMax as tableMax } from './PokerTable';
import { doudizhuColors as ink, radius, spacing } from './theme';
import { AppModal } from './ui';

type Name = (id: number) => string;
const stackedRankHeight = 16;
const stackedSuitSize = 11;
const markerHeight = 10;
const rankScaleLimit = 1.15;
const markerScaleLimit = 1.12;

function cardLabel(card: GuandanCard, level: string) {
  const { label } = describeGuandanCard(card);
  return isGuandanWild(card, level) ? `${label}，逢人配` : card.rank === level ? `${label}，级牌` : label;
}

function CardFace({ card, level, width, height, selected, small, large, returning, eligible, stacked }: {
  card: GuandanCard; level: string; width: number; height: number; selected?: boolean; small?: boolean; large?: boolean;
  returning?: boolean; eligible?: boolean; stacked?: boolean;
}) {
  const { rank, suit, red } = describeGuandanCard(card);
  const marker = eligible ? '还' : isGuandanWild(card, level) ? '配' : card.rank === level ? '级' : '';
  const color = red ? ink.red : ink.black;
  return <View style={[styles.cardFace, { width, minHeight: height },
    eligible && styles.faceEligible, returning && !eligible && styles.faceIneligible, selected && styles.cardSelected]}>
    <Text maxFontSizeMultiplier={large ? undefined : small ? 1 : rankScaleLimit} style={[
      styles.rank, { color }, small && styles.smallRank, large && styles.largeRank,
      !suit && (large ? styles.largeJoker : small ? styles.smallJoker : styles.joker),
      stacked && styles.stackedRank,
    ]}>{rank}</Text>
    {!!suit && <PlayingCardSuit suit={suit} color={color} size={large ? 15 : small ? 10 : stacked ? stackedSuitSize : 12} />}
    {!!marker && <Text maxFontSizeMultiplier={large ? undefined : markerScaleLimit} style={styles.marker}>{marker}</Text>}
    {!small && <View style={styles.cardMotif} />}
  </View>;
}

function Seat({ game, index, role, name, offline, wide, compact }: {
  game: GuandanView; index: number; role: string; name: Name; offline: boolean; wide: boolean; compact: boolean;
}) {
  const id = game.players[index];
  const nickname = name(id);
  const place = game.finish_order.indexOf(id);
  const remaining = game.remaining[index];
  const exchangePair = game.phase === 'returning' ? game.exchange?.pairs.find(pair => pair.to_id === id) : undefined;
  const active = game.current_id === id || (exchangePair !== undefined && !exchangePair.submitted);
  const status = [
    offline && '离线',
    guandanPlaces[place],
    exchangePair ? exchangePair.submitted ? '已提交还贡' : '待还贡' : active && '行动中',
    game.passed.includes(id) ? '过牌' : game.last_play?.uid === id && '已出牌',
  ].filter(Boolean).join(' · ');
  const countLabel = `剩 ${remaining} 张`;
  return <PokerSeat id={`guandan-seat-${id}`} name={nickname} role={role} active={active} compact={compact}
    remaining={remaining} status={status} style={!wide ? styles.topSeat : undefined}
    label={[role, nickname, countLabel, status].filter(Boolean).join('，')} />;
}

function DealRecap({ game, uid, name }: { game: GuandanView; uid: number; name: Name }) {
  const result = game.deal_result;
  if (!result) return null;
  const winner = result.winner === guandanTeam(game.players, uid) ? '我方' : '对方';
  return <View testID="guandan-deal-result" style={styles.recap}>
    <Text accessibilityRole="header" style={styles.turn}>第 {result.deal} 副 · {winner}获胜</Text>
    {game.phase === 'between' && <Text style={styles.expandLabel}>下一副 · {winner}打 {result.levels[result.winner]}</Text>}
    <View style={styles.ranks}>
      {guandanRanks(game.players, result).map(({ id, place }) =>
        <Text key={id} style={styles.muted}>{id === uid ? '你' : name(id)} · {place}</Text>)}
    </View>
  </View>;
}

function ExchangeDetails({ exchange, name, inModal }: {
  exchange: NonNullable<GuandanView['exchange']>; name: Name; inModal?: boolean;
}) {
  return exchange.antitribute
    ? <Text style={inModal ? styles.pickerCount : styles.muted}>本副抗贡 · {name(exchange.lead_id)}领出</Text>
    : exchange.pairs.map(pair => <Text key={pair.from_id} style={inModal ? styles.pickerCount : styles.muted}>
      {name(pair.from_id)} 向 {name(pair.to_id)} 进贡 {describeGuandanCard(pair.tribute).label}
      {pair.returned ? ` · 已还 ${describeGuandanCard(pair.returned).label}` : pair.submitted ? ' · 已提交还贡' : ' · 待还贡'}
    </Text>);
}

function PublicPlay({ game, uid, name, compact, bots }: {
  game: GuandanView; uid: number; name: Name; compact: boolean; bots: number[];
}) {
  if (game.phase === 'between' || game.phase === 'finished') return <DealRecap game={game} uid={uid} name={name} />;
  const exchange = game.exchange;
  if (game.phase === 'returning' && exchange) {
    const returningBot = exchange.pairs.find(pair => !pair.submitted && bots.includes(pair.to_id));
    return <View testID="guandan-exchange" style={[styles.playArea, compact && styles.playAreaCompact]}>
      <Text accessibilityLiveRegion="polite" style={styles.turn}>{game.return_choices.length
        ? '轮到你还贡' : returningBot ? `${name(returningBot.to_id)} 思考中…` : '等待还贡'}</Text>
      <ExchangeDetails exchange={exchange} name={name} />
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
  const turnLabel = current ? <Text testID="guandan-turn" accessibilityLiveRegion="polite" style={styles.turn}>
    {thinking ? `${current} 思考中…${botContext}` : turn}
  </Text> : null;
  return <View style={[styles.playArea, compact && styles.playAreaCompact]}>
    {compact && turnLabel}
    {!!play && <>
      <Text style={styles.publicLabel}>当前牌 · {play.uid === uid ? '你' : name(play.uid)} · {describeGuandanCombo(play, game.level)} · {play.cards.length} 张</Text>
      <View testID="guandan-public-cards" style={styles.publicCards}>
        {play.cards.map(card => {
          if (!compact) return <View key={card.id} accessible accessibilityLabel={cardLabel(card, game.level)}>
            <CardFace card={card} level={game.level} width={29} height={44} small />
          </View>;
          const face = describeGuandanCard(card);
          const color = face.red ? ink.red : ink.black;
          return <View key={card.id} accessible accessibilityLabel={cardLabel(card, game.level)} style={styles.compactCard}>
            <Text style={[styles.compactRank, { color }]}>{face.rank}</Text>
            {!!face.suit && <PlayingCardSuit suit={face.suit} color={color} size={9} />}
            {isGuandanWild(card, game.level) && <Text style={styles.marker}>配</Text>}
          </View>;
        })}
      </View>
    </>}
    {!compact && turnLabel}
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

function Actions({ game, uid, host, connected, pending, previewReady, focus, onFocus, selectedCount, onAction, picker }: ActionsProps & {
  picker?: boolean;
}) {
  const prefix = picker ? 'guandan-picker' : 'guandan';
  const disabled = !connected || pending;
  const waiting = disabled || game.current_id !== uid;
  const button = (action: GuandanCommand | 'hint', text: string, unavailable: boolean, primary = false) => {
    const id = `${prefix}-${action}`;
    return <Pressable testID={id} accessibilityRole="button" disabled={unavailable}
      accessibilityState={{ disabled: unavailable }} onPress={() => onAction(action)}
      onFocus={() => onFocus(id)} onBlur={() => onFocus('')}
      style={({ pressed }) => [styles.action, primary && styles.primary, unavailable && styles.actionDisabled,
        pressed && styles.pressed, focus === id && styles.actionFocus]}>
      <Text style={[styles.actionLabel, primary && styles.primaryLabel, unavailable && styles.disabledLabel]}>{text}</Text>
    </Pressable>;
  };
  if (game.phase === 'finished') return null;
  return <View style={styles.actionRow}>
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
  const [exchangeDeal, setExchangeDeal] = useState<number | null>(null);
  const tableWidth = Math.min(tableMax, width - left - right, space.width || tableMax);
  const usableHeight = Math.min(height - top - bottom, space.height || height);
  const compact = usableHeight < 400;
  const returning = game.phase === 'returning';
  const showHand = game.phase === 'playing' || returning;
  const wide = tableWidth >= 580;
  const { rows, cardWidth, step } = layoutGuandanHand(game.hand, tableWidth - 2 * edge);
  const stacked = rows.length > 1;
  const handHeight = compact ? 44 : wide || !stacked ? 70 : 56;
  // The next row can rise to its hit area's top. Leave room for the complete corner above it.
  const rowStep = Math.max(48, Math.ceil(raise + 3 + stackedSuitSize +
    stackedRankHeight * Math.min(fontScale, rankScaleLimit) + markerHeight * Math.min(fontScale, markerScaleLimit)));
  const me = game.players.indexOf(uid);
  const team = guandanTeam(game.players, uid) ?? 0;
  const seat = (offset: number, role: string) => {
    const index = (me + offset) % game.players.length;
    return <Seat game={game} index={index} role={role} name={name} wide={wide} compact={compact || tableWidth < 420}
      offline={online[game.players[index]] === false} />;
  };
  const upstream = seat(3, '上家');
  const partner = seat(2, '搭档');
  const downstream = seat(1, '下家');
  const publicPlay = <View style={[styles.center, compact && styles.centerCompact]}>
    <PublicPlay game={game} uid={uid} name={name} compact={compact} bots={bots} />
    <PokerPlayHistory game="guandan" records={game.play_history} name={name} describe={describeGuandanPlay} compact={compact} />
  </View>;
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
  const exchange = game.exchange;
  const ownDetails = ['你', `剩 ${game.hand.length} 张`, guandanPlaces[game.finish_order.indexOf(uid)],
    game.current_id === uid && '行动中', game.passed.includes(uid) && '过牌'].filter(Boolean).join(' · ');
  return <View style={styles.safeArea}>
    <View testID="guandan-table" style={styles.table}
      onLayout={({ nativeEvent: { layout } }) => setSpace({ width: layout.width, height: layout.height })}>
      <View style={styles.score} testID="guandan-score">
        <View style={styles.scoreItems}>
          <Text style={styles.scoreValue}>第 {game.deal} 副 · {game.level_team === null ? '' : game.level_team === team ? '我方' : '对方'}打 {game.level}</Text>
          <Text style={styles.muted}>我方 <Text style={styles.scoreValue}>{game.levels[team]}</Text> · 对方 <Text style={styles.scoreValue}>{game.levels[1 - team]}</Text></Text>
        </View>
        {compact && wide && <View style={styles.partner}>{partner}</View>}
        {exchange && !returning && <Pressable testID="guandan-exchange-details" accessibilityRole="button"
          style={styles.exchangeButton} onPress={() => setExchangeDeal(game.deal)}>
          <Text style={styles.expandLabel}>{exchange.antitribute ? '抗贡详情' : '贡还详情'}</Text>
        </Pressable>}
      </View>
      <ScrollView style={styles.fieldScroll} contentContainerStyle={[styles.field, compact && styles.fieldCompact]}>
        {wide ? <>
          {!compact && <View style={styles.partner}>{partner}</View>}
          <View style={[styles.arena, styles.arenaWide]}>
            <View style={styles.side}>{upstream}</View>
            {publicPlay}
            <View style={styles.side}>{downstream}</View>
          </View>
        </> : <View style={[styles.arena, compact && styles.arenaCompact]}>
          <View style={[styles.topSeats, compact && styles.arenaCompact]}>{upstream}{partner}{downstream}</View>
          {publicPlay}
        </View>}
      </ScrollView>
      <View style={[styles.playerArea, compact && styles.playerAreaCompact]}>
        <View style={styles.actionBar}>
          {!!note && <Text testID="guandan-feedback" accessibilityLiveRegion="polite" style={styles.feedback}>{note}</Text>}
          <Actions {...actionProps} />
        </View>
        <View style={styles.playerHeading}>
          <Text testID={`guandan-seat-${uid}`} accessibilityLabel={`${name(uid)}，${ownDetails}`}
            style={styles.playerName}>{ownDetails}</Text>
          {game.phase !== 'finished' && <Pressable testID="guandan-surrender" accessibilityRole="button"
            onPress={onSurrender} style={styles.plainButton}><Text style={styles.plainLabel}>认输</Text></Pressable>}
          <Pressable testID="guandan-leave" accessibilityRole="button" accessibilityLabel="离开房间"
            onPress={onLeave} style={styles.plainButton}><Text style={styles.plainLabel}>离开房间</Text></Pressable>
          {showHand && game.hand.length > 0 && <Pressable testID="guandan-expand" accessibilityRole="button"
            aria-expanded={pickerOpen} onPress={() => onPicker(true)} style={styles.plainButton}>
            <Text style={styles.expandLabel}>放大选牌</Text>
          </Pressable>}
        </View>
        {showHand && <View style={[styles.hand, stacked && styles.stackedHand]} testID="guandan-hand">
          {rows.map((row, index) => {
            const hasNextRow = index < rows.length - 1;
            const faceHeight = hasNextRow ? Math.max(handHeight, rowStep + raise) : handHeight;
            return <View key={index} style={[
              styles.handRow, { width: cardWidth + step * (row.length - 1), height: hasNextRow ? rowStep : handHeight + raise },
            ]}>
              {row.map((card, position) => <View key={card.id} pointerEvents="none" accessible={false}
                importantForAccessibility="no-hide-descendants" accessibilityElementsHidden aria-hidden
                style={[styles.facePosition, { left: position * step, top: selection.has(card.id) ? 0 : raise }]}>
                <CardFace card={card} level={game.level} width={cardWidth} height={faceHeight} selected={selection.has(card.id)}
                  stacked={stacked} returning={returning} eligible={eligible(card)} />
              </View>)}
              <View style={styles.hitRow} testID={`guandan-hand-row-${index + 1}`}>
                {row.map((card, position) => {
                  const key = `guandan-card-${card.id}`;
                  return <Pressable key={key} {...cardProps(card, key)}
                    style={[{ width: position === row.length - 1 ? cardWidth : step }, focus === key && styles.focus]} />;
                })}
              </View>
            </View>;
          })}
        </View>}
      </View>
    </View>
    <AppModal visible={pickerOpen} onClose={() => onPicker(false)} testID="guandan-picker" cardStyle={styles.modalCard}>
      <View style={[styles.picker, { height: Math.max(188, Math.min(516, height - top - bottom - 60)) }]}>
        <View style={styles.pickerHeading}>
          <View style={styles.pickerTitles}>
            <Text accessibilityRole="header" style={styles.pickerTitle}>放大选牌</Text>
            <Text accessibilityLiveRegion="polite" style={styles.pickerCount}>已选 {selected.length} 张</Text>
          </View>
          <Pressable testID="guandan-picker-close" accessibilityRole="button" accessibilityLabel="关闭放大选牌"
            style={styles.pickerClose} onPress={() => onPicker(false)}><Text style={styles.pickerCloseLabel}>关闭</Text></Pressable>
        </View>
        <ScrollView style={styles.pickerScroll} contentContainerStyle={styles.grid} keyboardShouldPersistTaps="handled">
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
        {!!note && <Text accessibilityLiveRegion="polite" style={[styles.feedback, styles.modalFeedback]}>{note}</Text>}
        <Actions {...actionProps} picker />
      </View>
    </AppModal>
    <AppModal visible={exchangeDeal === game.deal && exchange !== null && !returning}
      onClose={() => setExchangeDeal(null)} testID="guandan-exchange-modal">
      <Text accessibilityRole="header" style={styles.pickerTitle}>本副贡还牌</Text>
      {exchange && <ExchangeDetails exchange={exchange} name={name} inModal />}
      <Pressable accessibilityRole="button" style={styles.pickerClose} onPress={() => setExchangeDeal(null)}>
        <Text style={styles.pickerCloseLabel}>关闭</Text>
      </Pressable>
    </AppModal>
  </View>;
}

const styles = StyleSheet.create({
  ...pokerStyles,
  score: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center',
    gap: spacing.sm, paddingHorizontal: edge, paddingTop: spacing.xs },
  scoreItems: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center', gap: spacing.sm },
  scoreValue: { ...pokerStyles.turn, fontSize: pokerStyles.muted.fontSize },
  exchangeButton: { minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.xs },
  partner: { alignSelf: 'center', width: '21%', maxWidth: 184 },
  arenaCompact: { gap: spacing.xs },
  publicLabel: { ...pokerStyles.muted, textAlign: 'center' },
  playAreaCompact: { ...pokerStyles.playAreaCompact, gap: 2 },
  recap: { ...pokerStyles.playArea, minHeight: 0 },
  ranks: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: spacing.sm },
  playerAreaCompact: { paddingBottom: spacing.xs },
  faceEligible: { borderColor: ink.selected },
  faceIneligible: { backgroundColor: ink.muted },
  stackedHand: { gap: 0 },
  stackedRank: { fontSize: 14, lineHeight: stackedRankHeight },
  marker: { fontSize: 10, lineHeight: markerHeight, includeFontPadding: false, color: ink.selected, fontWeight: '800' },
  focus: { borderWidth: 2, borderColor: ink.active, borderRadius: radius.sm },
  actionFocus: { borderWidth: 2, borderColor: ink.gold },
});
