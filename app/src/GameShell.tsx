/** Presentation-only game chrome. Rules, sockets and leave guards remain in each screen. */
import React from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import type { StyleProp, ViewStyle } from 'react-native';
import { gameTitle } from './games';
import type { GameKey } from './games';
import { GameHelpButton } from './help';
import { AppModal } from './ui';
import { GameClock, ObservedClock } from './GameClock';
import { colors, fontSize, radius, shadow, spacing } from './theme';

export interface ShellPlayer {
  id: number;
  name: string;
  identity?: string;
  color?: string;
  active?: boolean;
  isMe?: boolean;
  isBot?: boolean;
  connected?: boolean;
  detail?: string;
}

export interface GameShellProps {
  game: GameKey;
  code: string;
  connected: boolean;
  players?: ShellPlayer[];
  status?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  /** Fullscreen board routes can collapse the title while keeping identities visible. */
  compact?: boolean;
  clock?: ObservedClock | null;
}

export function GamePlayers({ players = [], stacked = false }: { players?: ShellPlayer[]; stacked?: boolean }) {
  if (!players.length) return null;
  return <View style={styles.players} testID="game-players">
    {players.map(player => <View key={player.id} testID={`game-player-${player.id}`}
      style={[styles.player, stacked && styles.playerStacked, player.active && styles.playerActive]}>
      <View style={[styles.avatar, player.color ? { borderColor: player.color } : null]}>
        <Text style={styles.avatarText}>{player.name.slice(0, 1) || '?'}</Text>
      </View>
      <View style={styles.playerInfo}>
        <Text numberOfLines={stacked ? 2 : 1} style={styles.playerName}>{player.name}{player.isMe ? '（我）' : ''}</Text>
        <Text numberOfLines={1} style={styles.playerDetail}>
          {[player.identity, player.isBot ? '人机' : null, player.active ? '行动中' : null,
            player.connected === false && !player.isBot ? '离线' : null, player.detail].filter(Boolean).join(' · ')}
        </Text>
      </View>
    </View>)}
  </View>;
}

export default function GameShell({ game, code, connected, players, status, actions, children, style, clock,
  compact = false }: GameShellProps) {
  return <View style={[styles.shell, style]} testID={`game-shell-${game}`}>
    <View style={[styles.header, compact && styles.compact]}>
      <Text numberOfLines={1} style={styles.title}>{gameTitle(game)}{compact && players?.length
        ? ` · ${players.map(player => player.name).join(' · ')}` : ''}</Text>
      <Text style={styles.code}>#{code}</Text>
      <Text accessibilityLiveRegion="polite" style={[styles.connection, !connected && styles.offline]}>
        {connected ? '已连接' : '重连中…'}
      </Text>
      <GameHelpButton game={game} size={28} />
    </View>
    {!compact && <GamePlayers players={players} />}
    <GameClock clock={clock} game={game} />
    {status != null && <View style={styles.status}>{typeof status === 'string'
      ? <Text style={styles.statusText}>{status}</Text> : status}</View>}
    <View style={[styles.content, style,
      ['gomoku', 'xiangqi', 'reversi', 'luzhanqi', 'connect4'].includes(game) && styles.tabletop]}>{children}</View>
    {actions != null && <View style={styles.actions}>{actions}</View>}
  </View>;
}

interface GameResultProps {
  visible: boolean;
  title?: string;
  result: string;
  reason?: string;
  players?: ShellPlayer[];
  children?: React.ReactNode;
  onBackToRoom: () => void;
  onViewHistory: () => void;
  roomButtonTestID?: string;
}

/** One result layout for ranked, cooperative and two-player games; no automatic rematch. */
export function GameResult({ visible, title = '对局结束', result, reason, players, children,
  onBackToRoom, onViewHistory, roomButtonTestID = 'game-result-room' }: GameResultProps) {
  return <AppModal visible={visible} onClose={onBackToRoom} testID="game-result">
    <Text accessibilityRole="header" style={styles.resultTitle}>{title}</Text>
    <Text style={styles.result}>{result}</Text>
    {!!reason && <Text style={styles.reason}>{reason}</Text>}
    <GamePlayers players={players} stacked />
    {children}
    <TouchableOpacity accessibilityRole="button" testID={roomButtonTestID}
      style={[styles.button, styles.primary]} onPress={onBackToRoom}>
      <Text style={styles.primaryText}>返回房间</Text>
    </TouchableOpacity>
    <TouchableOpacity accessibilityRole="button" testID="game-result-history"
      style={[styles.button, styles.secondary]} onPress={onViewHistory}>
      <Text style={styles.secondaryText}>查看战绩</Text>
    </TouchableOpacity>
    <Text style={styles.reason}>续开请返回房间，由房主开始</Text>
  </AppModal>;
}

const styles = StyleSheet.create({
  shell: { flex: 1, minHeight: 0, backgroundColor: colors.bg },
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, backgroundColor: colors.card,
    alignSelf: 'stretch', paddingHorizontal: spacing.md, paddingVertical: spacing.xs, minHeight: 44 },
  compact: { paddingVertical: 0 },
  title: { flex: 1, fontSize: fontSize.md, color: colors.text, fontWeight: '700' },
  code: { fontSize: fontSize.xs, color: colors.textMuted },
  connection: { fontSize: fontSize.xs, color: colors.success },
  offline: { color: colors.warning, fontWeight: '700' },
  players: { alignSelf: 'stretch', flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs, backgroundColor: colors.card },
  player: { flex: 1, minWidth: '30%', flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    borderWidth: 1, borderColor: colors.border, borderRadius: radius.md,
    paddingHorizontal: spacing.xs, paddingVertical: spacing.xs },
  playerActive: { borderColor: colors.primary, borderWidth: 2, backgroundColor: colors.surfaceMuted },
  playerStacked: { minWidth: '100%' },
  avatar: { width: 28, height: 28, borderRadius: radius.full, backgroundColor: colors.surfaceMuted,
    borderWidth: 2, borderColor: colors.borderStrong, alignItems: 'center', justifyContent: 'center' },
  avatarText: { fontSize: fontSize.sm, color: colors.text, fontWeight: '700' },
  playerInfo: { flex: 1, minWidth: 0 },
  playerName: { fontSize: fontSize.xs, color: colors.text, fontWeight: '700' },
  playerDetail: { fontSize: fontSize.xs, color: colors.textSecondary },
  status: { alignSelf: 'stretch', paddingHorizontal: spacing.sm, paddingVertical: spacing.xs },
  statusText: { fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center' },
  content: { flex: 1, minHeight: 0, alignSelf: 'stretch' },
  tabletop: { margin: spacing.sm, borderRadius: radius.lg, backgroundColor: colors.surfaceMuted,
    borderWidth: 1, borderColor: colors.border, ...shadow.card },
  actions: { alignSelf: 'stretch', backgroundColor: colors.card, borderTopWidth: 1, borderTopColor: colors.border },
  resultTitle: { fontSize: fontSize.xl, fontWeight: '800', color: colors.text, textAlign: 'center' },
  result: { fontSize: fontSize.xxl, fontWeight: '800', color: colors.primary, textAlign: 'center' },
  reason: { fontSize: fontSize.sm, color: colors.textSecondary, textAlign: 'center' },
  button: { minHeight: 48, paddingVertical: spacing.sm + 6, borderRadius: radius.md,
    alignItems: 'center', justifyContent: 'center' },
  primary: { backgroundColor: colors.primary },
  secondary: { backgroundColor: colors.surfaceMuted, borderWidth: 1, borderColor: colors.borderStrong },
  primaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  secondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
