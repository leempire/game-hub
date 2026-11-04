/** Authenticated read-only viewing; no player socket, join, leave or game actions. */
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAuth } from '../auth';
import { getRoom, type Room } from '../api';
import { requestWatch, type QuickMessage, type WatchState } from '../social';
import WatchConnection from '../watchConnection';
import GameShell, { type ShellPlayer } from '../GameShell';
import StasisWatchMap, { type PublicStasis } from '../StasisWatchMap';
import WatchUnoTable, { type PublicUno } from '../WatchUnoTable';
import { Card, FormInput } from '../ui';
import { colors, fontSize, playerColors, radius, spacing } from '../theme';

interface Entry { key: string; room?: Room; error?: string }
interface Session { key: string; packet?: WatchState; error?: string; connected: boolean; loading: boolean }
interface Credential { key: string; password: string; attempt: number }

export default function SpectatorScreen({ route }: { route: { params: { code: string } } }) {
  const { token, user } = useAuth();
  const navigation = useNavigation<any>();
  const focused = useIsFocused();
  const insets = useSafeAreaInsets();
  const code = route.params.code.trim().toUpperCase();
  const [reload, setReload] = useState(0);
  const [entry, setEntry] = useState<Entry | null>(null);
  const [credential, setCredential] = useState<Credential | null>(null);
  const [password, setPassword] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [phrase, setPhrase] = useState<QuickMessage | null>(null);
  const submitting = useRef(false);
  const entryKey = `${code}:${reload}`;
  const room = entry?.key === entryKey ? entry.room : undefined;
  const entryError = entry?.key === entryKey ? entry.error : undefined;
  const needsPassword = !!room?.has_password && !room.players.some(player => player.id === user?.id);
  const submitted = credential?.key === entryKey;
  const authKey = `${entryKey}:${submitted ? credential.attempt : 0}`;
  const current = session?.key === authKey ? session : null;
  const packet = current?.packet;
  const shownRoom = packet?.room || room;
  const error = entryError || current?.error;

  useEffect(() => {
    let alive = true;
    if (!token || !focused) return;
    void getRoom(token, code).then(value => {
      if (!alive) return;
      if (!['uno', 'stasis'].includes(value.game) || !value.allow_spectators || value.practice)
        setEntry({ key: entryKey, error: '房主未开放观战，或这个房间不支持观战' });
      else setEntry({ key: entryKey, room: value });
    }).catch(cause => { if (alive) setEntry({ key: entryKey, error: (cause as Error).message }); });
    return () => { alive = false; };
  }, [code, entryKey, token, focused]);

  useEffect(() => {
    if (!focused || !token || !room || (needsPassword && !submitted)) return;
    let alive = true;
    let connection: WatchConnection | null = null;
    submitting.current = true;
    void Promise.resolve().then(() => {
      if (!alive) return null;
      setPhrase(null);
      setSession({ key: authKey, connected: false, loading: true });
      return requestWatch(token, code, submitted ? credential.password : undefined);
    }).then(grant => {
      if (!alive || !grant) return;
      connection = new WatchConnection(token, grant,
        value => { if (alive) setSession({ key: authKey, packet: value, connected: true, loading: false }); },
        connected => { if (alive) setSession(previous => previous?.key === authKey ? { ...previous, connected } : previous); },
        message => { if (alive) setSession({ key: authKey, error: message, connected: false, loading: false }); },
        value => { if (alive) setPhrase(value); });
      connection.connect();
    }).catch(cause => {
      if (alive) setSession({ key: authKey, error: (cause as Error).message, connected: false, loading: false });
    }).finally(() => { if (alive) submitting.current = false; });
    return () => { alive = false; submitting.current = false; connection?.close(); };
  }, [token, room, needsPassword, submitted, credential, authKey, code, focused]);

  useEffect(() => {
    if (!phrase) return;
    const timer = setTimeout(() => setPhrase(null), 4500);
    return () => clearTimeout(timer);
  }, [phrase]);

  const leave = () => { if (navigation.canGoBack()) navigation.goBack(); else navigation.navigate('MainTabs'); };
  const retry = () => { setPhrase(null); setReload(value => value + 1); };
  const authorize = () => {
    if (submitting.current) return;
    submitting.current = true;
    setCredential(previous => ({ key: entryKey, password,
      attempt: previous?.key === entryKey ? previous.attempt + 1 : 1 }));
  };
  const players: ShellPlayer[] = (shownRoom?.players || []).map((player, seat) => ({
    id: player.id, name: player.nickname || player.username, identity: `座位 ${seat + 1}`,
    isBot: player.is_bot, connected: player.connected,
    active: shownRoom?.game === 'uno' && packet?.game?.turn === seat,
    color: playerColors[String((packet?.game?.seat_colors as string[] | undefined)?.[seat] || '')],
  }));
  const status = <Text style={styles.hint} testID="watch-status" accessibilityLiveRegion="polite">
    只读观战 · 不占座{current?.connected ? ' · 已连接' : current?.loading ? ' · 正在连接…' : ''}
  </Text>;
  const content = <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
    {(!entry || entry.key !== entryKey || current?.loading) && !error &&
      <ActivityIndicator color={colors.primary} accessibilityLabel="正在加载观战" />}
    {!!error && <Card><Text accessibilityRole="alert" style={styles.error} testID="watch-error">{error}</Text>
      <TouchableOpacity style={styles.secondary} testID="watch-retry" onPress={retry} accessibilityRole="button">
        <Text style={styles.secondaryText}>重新连接</Text>
      </TouchableOpacity></Card>}
    {needsPassword && !packet && <Card style={styles.passwordCard}>
      <FormInput label="观战密码" testID="watch-password" value={password} onChangeText={setPassword}
        secureTextEntry maxLength={32} autoCapitalize="none" onSubmitEditing={authorize}
        hint="密码只用于获取观战许可，不会加入房间" />
      <TouchableOpacity accessibilityRole="button" testID="watch-authorize" disabled={!!current?.loading}
        style={[styles.primary, current?.loading && styles.disabled]} onPress={authorize}>
        <Text style={styles.primaryText}>{current?.loading ? '正在验证…' : '进入观战'}</Text>
      </TouchableOpacity>
    </Card>}
    {phrase && <Card><Text testID="watch-phrase" accessibilityLiveRegion="polite" style={styles.hint}>
      {phrase.name}：{phrase.text}</Text></Card>}
    {packet && !packet.game && <Card><Text style={styles.hint}>房间等待中，开局后将自动显示公共局面</Text></Card>}
    {packet?.room.game === 'uno' && packet.game && <WatchUnoTable room={packet.room} game={packet.game as unknown as PublicUno} />}
    {packet?.room.game === 'stasis' && packet.game && <Card style={styles.station}>
      <Text style={styles.title}>{packet.game.finished ? '本局已结束' : packet.game.phase === 'run' ? '跑动抢舱室' : '全站防守中'}</Text>
      <StasisWatchMap room={packet.room} game={packet.game as unknown as PublicStasis} />
    </Card>}
  </ScrollView>;
  const actions = <View style={{ padding: spacing.sm, paddingBottom: spacing.sm + insets.bottom }}>
    <TouchableOpacity accessibilityRole="button" testID="watch-leave" style={styles.secondary} onPress={leave}>
      <Text style={styles.secondaryText}>退出观战</Text>
    </TouchableOpacity>
  </View>;
  return <View style={styles.page} testID="spectator-screen">
    {shownRoom && ['uno', 'stasis'].includes(shownRoom.game)
      ? <GameShell game={shownRoom.game as 'uno' | 'stasis'} code={code} connected={!!current?.connected}
        players={players} status={status} actions={actions}>{content}</GameShell>
      : <>{status}{content}{actions}</>}
  </View>;
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  content: { flexGrow: 1, padding: spacing.md, gap: spacing.md, alignItems: 'stretch' },
  hint: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 21, textAlign: 'center' },
  error: { color: colors.danger, fontSize: fontSize.md, lineHeight: 22 },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  passwordCard: { gap: spacing.md }, station: { gap: spacing.sm, paddingHorizontal: 0 },
  secondary: { minHeight: 48, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md,
    padding: spacing.sm, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surfaceMuted },
  secondaryText: { color: colors.textSecondary, fontWeight: '700', fontSize: fontSize.md },
  primary: { minHeight: 48, borderRadius: radius.md, padding: spacing.sm, backgroundColor: colors.primary,
    alignItems: 'center', justifyContent: 'center' },
  primaryText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  disabled: { backgroundColor: colors.disabled },
});
