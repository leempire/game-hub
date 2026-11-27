import React from 'react';
import { StyleSheet, Text, View, type ViewStyle } from 'react-native';
import { colors, doudizhuColors as ink, fontSize, radius, spacing } from './theme';

export const pokerTableMax = 1120;
export const pokerTableEdge = 12;
export const pokerCardRaise = 8;

export function PokerSeat({ id, name, role, remaining, status, active, compact, style, label }: {
  id: string; name: string; role: string; remaining: number; status: string;
  active: boolean; compact?: boolean; style?: ViewStyle; label?: string;
}) {
  const low = remaining <= 2;
  return <View testID={id} accessible={label ? true : undefined} accessibilityLabel={label}
    style={[pokerStyles.seat, compact && pokerStyles.seatCompact, style, active && pokerStyles.activeSeat, low && pokerStyles.lowSeat]}>
    <View style={pokerStyles.seatDetails}>
      <Text numberOfLines={1} style={pokerStyles.seatName}>{name}</Text>
      <Text numberOfLines={2} style={pokerStyles.muted}>{role}</Text>
    </View>
    <View style={pokerStyles.seatCount}>
      <Text style={[pokerStyles.count, low && pokerStyles.gold]}>{remaining}</Text>
      <Text style={pokerStyles.muted}>张</Text>
    </View>
    {!!status && <Text style={[pokerStyles.seatAction, active && pokerStyles.gold]}>{status}</Text>}
  </View>;
}

export const pokerStyles = StyleSheet.create({
  safeArea: { flex: 1, minHeight: 0, backgroundColor: ink.felt },
  table: { flex: 1, minHeight: 0, width: '100%', maxWidth: pokerTableMax, alignSelf: 'center' },
  fieldScroll: { flex: 1, minHeight: 0 },
  field: { flexGrow: 1, paddingHorizontal: pokerTableEdge, paddingTop: spacing.md, gap: spacing.md },
  fieldCompact: { paddingTop: spacing.xs, gap: spacing.xs },
  arena: { flex: 1, gap: spacing.sm },
  arenaWide: { flexDirection: 'row', alignItems: 'center' },
  topSeats: { flexDirection: 'row', gap: spacing.sm },
  side: { width: '21%', maxWidth: 184 },
  seat: { minWidth: 0, backgroundColor: ink.panel, borderRadius: radius.md, borderWidth: 1,
    borderColor: ink.line, flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: spacing.xs, padding: spacing.sm },
  seatCompact: { padding: spacing.xs },
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
  center: { flex: 1, minWidth: 0, justifyContent: 'center', gap: spacing.xs },
  centerCompact: { flexDirection: 'row', alignItems: 'center' },
  playArea: { flexShrink: 1, minWidth: 0, minHeight: 62, alignItems: 'center', justifyContent: 'center', gap: spacing.xs, padding: spacing.xs },
  playAreaCompact: { flex: 1, minHeight: 34, paddingVertical: 0 },
  publicCards: { flexDirection: 'row', width: '100%', flexWrap: 'wrap', justifyContent: 'center', gap: spacing.xs },
  turn: { color: ink.light, fontSize: fontSize.sm, fontWeight: '700', textAlign: 'center' },
  compactCard: { flexDirection: 'row', alignItems: 'center', gap: 1, paddingHorizontal: 3,
    borderRadius: radius.sm, backgroundColor: ink.paper },
  compactRank: { fontSize: 11, fontWeight: '700' },
  playerArea: { borderTopWidth: 1, borderTopColor: ink.line, paddingHorizontal: pokerTableEdge, paddingTop: spacing.xs, paddingBottom: spacing.sm },
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
});
