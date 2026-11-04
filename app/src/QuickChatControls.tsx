import React, { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { AppModal } from './ui';
import { colors, fontSize, radius, spacing } from './theme';
import { sayPhrase, type QuickMessage, type QuickPhrase } from './social';
import type { RoomSocket, SocketMessage } from './socket';

/** The server supplies the only selectable phrases; arbitrary text is never sent. */
export default function QuickChatControls({ token, code, phrases, getSocket, enabled }: {
  token: string | null; code: string; phrases?: QuickPhrase[];
  getSocket: () => RoomSocket | null; enabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<QuickMessage | null>(null);
  const [error, setError] = useState('');
  const [remaining, setRemaining] = useState(0);
  const deadline = useRef(0);
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const socket = getSocket();
    if (!socket) return;
    const receive = (msg: SocketMessage) => {
      if (typeof msg.id === 'string' && typeof msg.text === 'string' && typeof msg.name === 'string') {
        setMessage(msg as unknown as QuickMessage);
      }
    };
    socket.on('quick_chat', receive);
    return () => socket.off('quick_chat', receive);
  }, [getSocket, enabled, code]);
  useEffect(() => {
    if (!remaining) return;
    const timer = setInterval(() => setRemaining(Math.max(0, Math.ceil((deadline.current - Date.now()) / 1000))), 200);
    return () => clearInterval(timer);
  }, [remaining]);
  const send = useCallback(async (id: string) => {
    if (!enabled || !token || busy.current || Date.now() < deadline.current) return;
    busy.current = true;
    setError('');
    try {
      const response = await sayPhrase(token, code, id);
      if (!mounted.current) return;
      deadline.current = Date.now() + response.cooldown_seconds * 1000;
      setRemaining(response.cooldown_seconds);
      setMessage(response.message);
      setOpen(false);
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : '发送失败，请重试');
    } finally { busy.current = false; }
  }, [enabled, token, code]);
  if (!phrases?.length) return null;
  const status = !enabled ? '连接正常且仍在局时可发送' : remaining ? `${remaining} 秒后可再发送` : '仅发送预设短语';
  return <View style={styles.wrap}>
    <TouchableOpacity testID="quick-chat-open" style={styles.button} onPress={() => setOpen(true)}>
      <Text style={styles.text}>快捷语{remaining ? ` · ${remaining}s` : ''}</Text>
    </TouchableOpacity>
    {message && <Text testID="quick-chat-message" style={styles.message} accessibilityLiveRegion="polite">{message.name}：{message.text}</Text>}
    <AppModal visible={open} onClose={() => setOpen(false)} testID="quick-chat-sheet">
      <Text style={styles.title}>快捷语</Text>
      <Text testID="quick-chat-status" style={styles.message}>{status}</Text>
      {phrases.map(phrase => <TouchableOpacity key={phrase.id} testID={`quick-phrase-${phrase.id}`}
        style={[styles.button, (!enabled || remaining > 0) && styles.disabled]} disabled={!enabled || remaining > 0}
        onPress={() => void send(phrase.id)}><Text style={styles.text}>{phrase.text}</Text></TouchableOpacity>)}
      {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
      <TouchableOpacity style={styles.button} onPress={() => setOpen(false)}><Text style={styles.text}>关闭</Text></TouchableOpacity>
    </AppModal>
  </View>;
}
const styles = StyleSheet.create({
  wrap: { gap: spacing.xs, flexShrink: 1 },
  button: { minHeight: 44, paddingHorizontal: spacing.md, marginVertical: spacing.xs, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.surfaceMuted, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong },
  text: { color: colors.text, fontSize: fontSize.sm },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700' },
  message: { color: colors.textSecondary, fontSize: fontSize.sm },
  disabled: { opacity: 0.5 }, error: { color: colors.danger, fontSize: fontSize.sm },
});
