import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useIsFocused, useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import type { RootStackParamList } from '../navigation';
import { useAuth } from '../auth';
import { blockContact, displayContact, getSocial, removeFriend, respondFriend, unblockContact,
  type ContactProfile, type SocialSnapshot } from '../contacts';
import useForegroundPolling from '../useForegroundPolling';
import ContactSearchModal from '../ContactSearchModal';
import { PersonCard, SocialButton, SocialEmpty } from '../SocialCards';
import { AppModal, Card } from '../ui';
import { colors, fontSize, radius, spacing } from '../theme';

type Segment = 'messages' | 'friends' | 'requests';
type Management = { person: ContactProfile; operation?: 'remove' | 'block' };
export default function SocialTab() {
  const { token } = useAuth();
  const focused = useIsFocused();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [loaded, setLoaded] = useState<{ token: string; data: SocialSnapshot } | null>(null);
  const data = loaded?.token === token ? loaded.data : null;
  const [segment, setSegment] = useState<Segment>('messages');
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [blockedOpen, setBlockedOpen] = useState(false);
  const [manage, setManage] = useState<Management | null>(null);
  const alive = useRef(true), lock = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const refresh = useForegroundPolling(`social:${token}`, async valid => {
    if (!token) return;
    const response = await getSocial(token);
    if (valid()) { setLoaded({ token, data: response }); setLoadError(''); }
  }, error => setLoadError(error instanceof Error ? error.message : '社交数据加载失败'));
  const chat = (id: number) => navigation.navigate('Chat', { userId: id });
  const perform = async (key: string, task: () => Promise<unknown>, after?: () => void) => {
    if (lock.current) return;
    lock.current = true; setBusy(key); setActionError('');
    try { await task(); if (alive.current) { after?.(); refresh(); } }
    catch (error) { if (alive.current) setActionError(error instanceof Error ? error.message : '操作失败，请重试'); }
    finally { lock.current = false; if (alive.current) setBusy(''); }
  };
  const respond = (id: number, action: 'accept' | 'decline' | 'cancel') => {
    if (token) void perform(`${action}-${id}`, () => respondFriend(token, id, action));
  };
  const conversationFriends = data?.friends.filter(person => person.last_message) || [];
  const count = { messages: data?.unread_total || 0, friends: data?.friends.length || 0, requests: data?.incoming.length || 0 };
  const alert = !!actionError && <Text style={styles.error} accessibilityRole="alert">{actionError}</Text>;
  return <View style={styles.page} testID="social-screen">
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <View style={styles.heading}><Text style={styles.title}>社交</Text><Text style={styles.hint}>和好友聊天，再约一局。</Text></View>
        <SocialButton label="添加好友" testID="social-add-friend" primary onPress={() => { setActionError(''); setSearchOpen(true); }} />
      </View>
      <View style={styles.segments}>
        {(['messages', 'friends', 'requests'] as Segment[]).map(key => <TouchableOpacity key={key}
          accessibilityRole="button" accessibilityState={{ selected: segment === key }} testID={`social-segment-${key}`}
          style={[styles.segment, segment === key && styles.selected]} onPress={() => { setSegment(key); setActionError(''); }}>
          <Text style={[styles.segmentText, segment === key && styles.selectedText]}>
            {{ messages: '消息', friends: '好友', requests: '申请' }[key]}{count[key] ? ` ${count[key] > 99 ? '99+' : count[key]}` : ''}
          </Text>
        </TouchableOpacity>)}
      </View>
      {!!loadError && <Card><Text style={styles.error} accessibilityRole="alert">{loadError}</Text>
        <SocialButton label="重新加载" testID="social-retry" onPress={refresh} /></Card>}
      {!manage && !blockedOpen && alert}
      {!data && !loadError && <ActivityIndicator color={colors.primary} accessibilityLabel="正在加载社交" />}
      {data && segment === 'messages' && <>
        {!conversationFriends.length && <SocialEmpty title="还没有聊天记录" detail="在好友列表中发起聊天，消息会保存在这里。" />}
        {conversationFriends.map(person => <PersonCard key={person.id} person={person} unread={person.unread_count}
          preview={person.last_message?.body}>
          <SocialButton label="打开聊天" testID={`social-chat-${person.id}`} onPress={() => chat(person.id)} primary />
        </PersonCard>)}
      </>}
      {data && segment === 'friends' && <>
        {!data.friends.length && <SocialEmpty title="认识一位新朋友" detail="点击上方「添加好友」，通过完整用户名发送申请。" />}
        {data.friends.map(person => <PersonCard key={person.id} person={person} unread={person.unread_count}
          onMore={() => { setActionError(''); setManage({ person }); }}>
          <SocialButton label="发消息" testID={`social-chat-${person.id}`} onPress={() => chat(person.id)} primary />
        </PersonCard>)}
        <SocialButton label={`已拉黑的用户${data.blocked.length ? `（${data.blocked.length}）` : ''}`} testID="social-blocked-open"
          onPress={() => { setActionError(''); setBlockedOpen(true); }} />
      </>}
      {data && segment === 'requests' && <>
        <Text style={styles.section}>收到的申请</Text>
        {!data.incoming.length && <Text style={styles.hint}>暂无待处理的好友申请。</Text>}
        {data.incoming.map(person => <PersonCard key={person.id} person={person}>
          <SocialButton label="拒绝" testID={`social-decline-${person.id}`} disabled={!!busy}
            onPress={() => respond(person.id, 'decline')} />
          <SocialButton label="接受" testID={`social-accept-${person.id}`} primary disabled={!!busy}
            busy={busy === `accept-${person.id}`} onPress={() => respond(person.id, 'accept')} />
        </PersonCard>)}
        <Text style={styles.section}>发出的申请</Text>
        {!data.outgoing.length && <Text style={styles.hint}>暂无等待确认的申请。</Text>}
        {data.outgoing.map(person => <PersonCard key={person.id} person={person} preview="等待对方确认">
          <SocialButton label="取消申请" testID={`social-cancel-${person.id}`} disabled={!!busy}
            busy={busy === `cancel-${person.id}`} onPress={() => respond(person.id, 'cancel')} />
        </PersonCard>)}
      </>}
      {!!data && <SocialButton label="刷新" testID="social-refresh" onPress={refresh} />}
    </ScrollView>
    {searchOpen && focused && <ContactSearchModal visible token={token} onClose={() => setSearchOpen(false)} onChanged={refresh} onChat={chat} />}
    <AppModal visible={!!manage && focused} onClose={() => { if (!lock.current) setManage(null); }} testID="social-manage-sheet">
      <Text style={styles.section}>{manage?.operation ? '确认操作' : displayContact(manage?.person || { id: 0, username: '', nickname: null })}</Text>
      {manage?.operation ? <>
        <Text style={styles.hint}>{manage.operation === 'block'
          ? '拉黑后将解除好友关系，双方不能再申请好友或聊天。你可以在已拉黑用户中解除。'
          : '删除后双方不能继续聊天，再次成为好友后可查看原聊天记录。'}</Text>
        <SocialButton label={manage.operation === 'block' ? '确认拉黑' : '确认删除'} danger testID="social-confirm" busy={!!busy}
          onPress={() => {
            if (!token || !manage) return;
            void perform(manage.operation!, () => manage.operation === 'block'
              ? blockContact(token, manage.person.id) : removeFriend(token, manage.person.id), () => setManage(null));
          }} />
      </> : <>
        <SocialButton label="删除好友" danger testID="social-remove" onPress={() => setManage(value => value ? { ...value, operation: 'remove' } : null)} />
        <SocialButton label="拉黑用户" danger testID="social-block" onPress={() => setManage(value => value ? { ...value, operation: 'block' } : null)} />
      </>}
      {alert}
      <SocialButton label="取消" testID="social-manage-cancel" disabled={!!busy} onPress={() => setManage(null)} />
    </AppModal>
    <AppModal visible={blockedOpen && focused} onClose={() => { if (!lock.current) setBlockedOpen(false); }} testID="social-blocked-sheet">
      <Text style={styles.section}>已拉黑的用户</Text>
      {!data?.blocked.length && <Text style={styles.hint}>暂无拉黑用户。</Text>}
      {data?.blocked.map(person => <PersonCard key={person.id} person={person}>
        <SocialButton label="解除拉黑" testID={`social-unblock-${person.id}`} disabled={!!busy} onPress={() => {
          if (token) void perform(`unblock-${person.id}`, () => unblockContact(token, person.id));
        }} />
      </PersonCard>)}
      {alert}<SocialButton label="关闭" disabled={!!busy} testID="social-blocked-close" onPress={() => setBlockedOpen(false)} />
    </AppModal>
  </View>;
}
const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  content: { width: '100%', maxWidth: 680, alignSelf: 'center', padding: spacing.md, gap: spacing.md, paddingBottom: spacing.xl },
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm }, heading: { flex: 1, minWidth: 0, gap: spacing.xs },
  title: { color: colors.text, fontSize: fontSize.xl, fontWeight: '700' }, hint: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 22 },
  section: { color: colors.text, fontSize: fontSize.md, fontWeight: '700' },
  segments: { flexDirection: 'row', backgroundColor: colors.surfaceMuted, padding: spacing.xs, borderRadius: radius.md, gap: spacing.xs },
  segment: { flex: 1, minHeight: 44, justifyContent: 'center', alignItems: 'center', borderRadius: radius.sm },
  selected: { backgroundColor: colors.primary }, segmentText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' }, selectedText: { color: colors.white },
  error: { color: colors.danger, fontSize: fontSize.sm, lineHeight: 22 },
});
