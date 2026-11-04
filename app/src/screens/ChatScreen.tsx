/** Friend-only plain text conversation. Server access is checked on every request. */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, FlatList, KeyboardAvoidingView, Platform, StyleSheet, Text,
  TextInput, TouchableOpacity, View } from 'react-native';
import type { NativeScrollEvent, NativeSyntheticEvent } from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useAuth } from '../auth';
import { displayContact } from '../contacts';
import type { DirectMessage } from '../contacts';
import { ChatTimeline } from '../chatTimeline';
import { BottomAnchor, scrollToLatest } from '../chatScroll';
import useForegroundPolling from '../useForegroundPolling';
import { Card } from '../ui';
import { colors, fontSize, radius, spacing } from '../theme';

function timeLabel(value: string) {
  const date = new Date(value);
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}
function Message({ value, mine, read }: { value: DirectMessage; mine: boolean; read: boolean }) {
  return <View testID={`chat-message-${value.id}`} style={[styles.messageRow, mine && styles.mineRow]}>
    <View style={[styles.bubble, mine ? styles.mineBubble : styles.peerBubble]}>
      <Text selectable style={[styles.messageText, mine && styles.mineText]}>{value.body}</Text>
    </View>
    <Text style={styles.time}>{timeLabel(value.created_at)}{mine ? read ? ' · 已读' : ' · 已发送' : ''}</Text>
  </View>;
}
function Conversation({ token, actor, peer }: { token: string; actor: number; peer: number }) {
  const nav = useNavigation();
  const insets = useSafeAreaInsets();
  const [model] = useState(() => new ChatTimeline(token, actor, peer, () => AppState.currentState === 'active'));
  const [state, setState] = useState(model.state);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const list = useRef<FlatList<DirectMessage>>(null);
  const anchor = useRef(new BottomAnchor());
  useEffect(() => model.subscribe(setState), [model]);
  useFocusEffect(useCallback(() => {
    model.setActive(true);
    const listener = AppState.addEventListener('change', () => model.invalidate());
    return () => { listener.remove(); model.setActive(false); };
  }, [model]));
  const refresh = useForegroundPolling(`chat:${actor}:${peer}:${token}`,
    valid => model.poll(valid), error => model.fail(error), 2000);
  const title = state.peer ? displayContact(state.peer) : '好友聊天';
  useEffect(() => { nav.setOptions({ title }); }, [nav, title]);
  const scroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    const { bottom, snap } = anchor.current.observe(contentOffset.y, contentSize.height, layoutMeasurement.height);
    setAwayFromBottom(!bottom); model.setAtBottom(bottom);
    if (snap) scrollToLatest(list.current);
  };
  const jump = () => {
    anchor.current.follow(); setAwayFromBottom(false);
    scrollToLatest(list.current); model.setAtBottom(true);
  };
  const older = () => { anchor.current.pause(); setAwayFromBottom(true); void model.loadOlder(); };
  return <KeyboardAvoidingView testID="chat-screen" style={styles.page}
    behavior={Platform.OS === 'ios' ? 'padding' : 'height'} keyboardVerticalOffset={Platform.OS === 'ios' ? insets.top + 44 : 0}>
    <View style={styles.status}>
      <Text style={styles.note} testID={state.ready ? 'chat-ready' : 'chat-connecting'}>
        {state.peer ? `@${state.peer.username} · 好友私聊` : '好友私聊'}
      </Text>
      <Text style={styles.note}>消息保留在本账号，前台自动刷新</Text>
    </View>
    {!!state.error && <View style={styles.errorRow}>
      <Text accessibilityRole="alert" testID="chat-error" style={styles.error}>{state.error}</Text>
      <TouchableOpacity testID="chat-retry" style={styles.retry} onPress={refresh} accessibilityRole="button"><Text style={styles.link}>重试</Text></TouchableOpacity>
    </View>}
    {state.loading && <ActivityIndicator accessibilityLabel="正在加载聊天" color={colors.primary} />}
    <FlatList ref={list} data={state.messages} keyExtractor={item => String(item.id)}
      style={styles.list} contentContainerStyle={styles.messages} keyboardShouldPersistTaps="handled"
      ItemSeparatorComponent={() => <View style={styles.separator} />}
      onScroll={scroll} scrollEventThrottle={100} maintainVisibleContentPosition={{ minIndexForVisible: 0 }}
      onScrollBeginDrag={() => { anchor.current.beginDrag(); model.setAtBottom(false); }} onScrollEndDrag={() => anchor.current.endDrag()}
      onLayout={() => { if (anchor.current.following) { anchor.current.follow(); scrollToLatest(list.current); } }}
      onContentSizeChange={(_width, height) => { if (anchor.current.resized(height)) scrollToLatest(list.current); }}
      ListHeaderComponent={state.hasOlder ? <TouchableOpacity testID="chat-load-older" style={styles.loadOlder}
        disabled={state.loadingOlder} onPress={older} accessibilityRole="button">
        <Text style={styles.link}>{state.loadingOlder ? '正在加载…' : '加载更早消息'}</Text>
      </TouchableOpacity> : null}
      ListEmptyComponent={!state.loading && state.ready ? <View style={styles.empty}>
        <Text style={styles.emptyTitle}>从一句你好开始</Text><Text style={styles.note}>和好友聊聊，约下一局一起玩</Text>
      </View> : null}
      renderItem={({ item }) => <Message value={item} mine={item.sender_id === actor} read={item.id <= state.peerReadThrough} />} />
    {awayFromBottom && <TouchableOpacity testID="chat-jump-latest" style={styles.jump} onPress={jump} accessibilityRole="button">
      <Text style={styles.link}>回到最新消息</Text>
    </TouchableOpacity>}
    <View style={[styles.composer, { paddingBottom: Math.max(spacing.sm, insets.bottom) }]}>
      {!!state.sendError && <Text testID="chat-send-error" accessibilityRole="alert" style={styles.error}>{state.sendError}</Text>}
      <View style={styles.inputRow}>
        <TextInput testID="chat-input" accessibilityLabel="聊天内容" value={state.draft} onChangeText={value => model.changeDraft(value)}
          multiline maxLength={1000} editable={!state.sending} placeholder="说点什么…" placeholderTextColor={colors.textMuted}
          style={styles.input} textAlignVertical="top" />
        <TouchableOpacity testID="chat-send" accessibilityRole="button" accessibilityLabel={state.sending ? '正在发送' : '发送消息'}
          disabled={!state.ready || state.sending || !state.draft.trim()}
          style={[styles.send, (!state.ready || state.sending || !state.draft.trim()) && styles.disabled]}
          onPress={() => { anchor.current.follow(); setAwayFromBottom(false); model.setAtBottom(true); void model.send(); }}>
          {state.sending ? <ActivityIndicator color={colors.white} /> : <Text style={styles.sendText}>发送</Text>}
        </TouchableOpacity>
      </View>
      <Text style={styles.counter}>{state.draft.length}/1000</Text>
    </View>
  </KeyboardAvoidingView>;
}
export default function ChatScreen({ route }: { route: { params: { userId: number } } }) {
  const { token, user } = useAuth();
  const peer = Number(route.params.userId);
  if (!token || !user) return null;
  if (!Number.isSafeInteger(peer) || peer <= 0 || peer === user.id)
    return <View style={styles.page}><Card><Text style={styles.error}>无效的聊天对象</Text></Card></View>;
  return <Conversation key={`${user.id}:${peer}:${token}`} token={token} actor={user.id} peer={peer} />;
}
const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg }, status: { padding: spacing.sm, gap: 3, borderBottomWidth: 1, borderColor: colors.border },
  note: { color: colors.textSecondary, fontSize: fontSize.xs, textAlign: 'center' },
  errorRow: { paddingHorizontal: spacing.md, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  error: { flexShrink: 1, color: colors.danger, fontSize: fontSize.sm },
  retry: { minHeight: 44, justifyContent: 'center', paddingHorizontal: spacing.sm }, link: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '600' },
  list: { flex: 1 }, messages: { flexGrow: 1, paddingHorizontal: spacing.md, paddingVertical: spacing.sm }, separator: { height: spacing.sm },
  messageRow: { alignItems: 'flex-start', gap: 3 }, mineRow: { alignItems: 'flex-end' },
  bubble: { maxWidth: '88%', borderRadius: radius.lg, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, flexShrink: 1 },
  mineBubble: { backgroundColor: colors.primary, borderBottomRightRadius: radius.sm }, peerBubble: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border, borderBottomLeftRadius: radius.sm },
  messageText: { color: colors.text, fontSize: fontSize.md, lineHeight: 24 }, mineText: { color: colors.white },
  time: { color: colors.textMuted, fontSize: fontSize.xs }, loadOlder: { minHeight: 44, alignItems: 'center', justifyContent: 'center' },
  empty: { flex: 1, minHeight: 140, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '600' },
  jump: { minHeight: 44, alignItems: 'center', justifyContent: 'center', borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.card },
  composer: { borderTopWidth: 1, borderColor: colors.border, backgroundColor: colors.card, paddingHorizontal: spacing.sm, paddingTop: spacing.sm, gap: spacing.xs },
  inputRow: { flexDirection: 'row', alignItems: 'flex-end', gap: spacing.sm },
  input: { flex: 1, minWidth: 0, minHeight: 44, maxHeight: 120, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong,
    paddingHorizontal: spacing.sm, paddingVertical: spacing.sm, color: colors.text, fontSize: fontSize.md, backgroundColor: colors.bg },
  send: { minWidth: 64, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: radius.md, backgroundColor: colors.primary },
  sendText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '600' }, disabled: { backgroundColor: colors.textMuted },
  counter: { color: colors.textSecondary, fontSize: fontSize.xs, textAlign: 'right' },
});
