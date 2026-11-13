/** 房间页的「邀请好友」弹窗：列好友、逐个发房间邀请。
 *
 * 邀请本身走 `POST /api/social/invites`，好友在「社交」页看到卡片后点「加入房间」
 * 仍是正常的 `joinRoom` —— 密码/满员/开局这些规则只在服务端维护一份。
 */
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { AppModal } from './ui';
import { displayContact, getSocial, inviteFriendToRoom, type ContactFriend } from './contacts';
import { colors, fontSize, radius, spacing } from './theme';

interface Props {
  visible: boolean;
  token: string;
  code: string;
  onClose: () => void;
}

export default function InviteFriendsModal({ visible, token, code, onClose }: Props) {
  const [friends, setFriends] = useState<ContactFriend[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<number | null>(null);
  const [sent, setSent] = useState<number[]>([]);

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    setFriends(null);
    setError('');
    setSent([]);
    getSocial(token)
      .then((data) => { if (alive) setFriends(data.friends); })
      .catch((err) => {
        if (alive) {
          setFriends([]);
          setError((err as Error).message);
        }
      });
    return () => { alive = false; };
  }, [visible, token]);

  const send = async (friend: ContactFriend) => {
    if (busy !== null) return;
    setBusy(friend.id);
    setError('');
    try {
      await inviteFriendToRoom(token, friend.id, code);
      setSent((value) => value.includes(friend.id) ? value : [...value, friend.id]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <AppModal visible={visible} onClose={onClose} testID="invite-friends-modal">
      <Text style={styles.title}>邀请好友</Text>
      <Text style={styles.hint}>
        好友会在「社交」页看到房间邀请，点「加入房间」直接进来。
      </Text>
      {error ? <Text style={styles.error} accessibilityRole="alert">{error}</Text> : null}
      {friends === null ? (
        <ActivityIndicator color={colors.primary} accessibilityLabel="正在加载好友" />
      ) : friends.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>{error ? '好友列表加载失败' : '还没有好友'}</Text>
          <Text style={styles.hint}>
            {error ? '请稍后重试，或先到「社交」页看看。' : '先到「社交」页添加好友，再回来邀请。'}
          </Text>
        </View>
      ) : friends.map((friend) => {
        const invited = sent.includes(friend.id);
        return (
          <View key={friend.id} style={styles.row} testID={`invite-friend-row-${friend.id}`}>
            <View style={styles.identity}>
              <View style={styles.avatar}>
                <Text style={styles.initial}>{(Array.from(displayContact(friend))[0] || '?').toUpperCase()}</Text>
              </View>
              <View style={styles.nameWrap}>
                <Text numberOfLines={1} style={styles.name}>{displayContact(friend)}</Text>
                <Text numberOfLines={1} style={styles.username}>@{friend.username}</Text>
              </View>
            </View>
            <TouchableOpacity
              testID={`invite-friend-${friend.id}`}
              accessibilityRole="button"
              accessibilityLabel={invited ? `再次邀请${displayContact(friend)}` : `邀请${displayContact(friend)}`}
              accessibilityState={{ busy: busy === friend.id }}
              style={[styles.action, invited && styles.actionSent, busy === friend.id && styles.actionBusy]}
              onPress={() => void send(friend)}
            >
              {busy === friend.id
                ? <ActivityIndicator color={colors.primary} />
                : <Text style={[styles.actionText, invited && styles.actionSentText]}>
                  {invited ? '已邀请' : '邀请'}
                </Text>}
            </TouchableOpacity>
          </View>
        );
      })}
      <TouchableOpacity style={styles.close} onPress={onClose} accessibilityRole="button">
        <Text style={styles.closeText}>关闭</Text>
      </TouchableOpacity>
    </AppModal>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, textAlign: 'center' },
  hint: { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 20, textAlign: 'center' },
  error: { fontSize: fontSize.sm, color: colors.danger, lineHeight: 20 },
  empty: { gap: spacing.xs, paddingVertical: spacing.md },
  emptyTitle: { fontSize: fontSize.md, fontWeight: '600', color: colors.text, textAlign: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, minHeight: 56,
    borderTopWidth: 1, borderTopColor: colors.border },
  identity: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  avatar: { width: 40, height: 40, borderRadius: radius.full, backgroundColor: colors.surfaceMuted,
    alignItems: 'center', justifyContent: 'center' },
  initial: { color: colors.primary, fontSize: fontSize.md, fontWeight: '700' },
  nameWrap: { flex: 1, minWidth: 0, gap: 2 },
  name: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  username: { color: colors.textMuted, fontSize: fontSize.xs },
  action: { minWidth: 76, minHeight: 44, paddingHorizontal: spacing.md, justifyContent: 'center',
    alignItems: 'center', borderRadius: radius.md, borderWidth: 1,
    borderColor: colors.primary, backgroundColor: colors.card },
  actionText: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '700' },
  actionSent: { borderColor: colors.borderStrong, backgroundColor: colors.surfaceMuted },
  actionSentText: { color: colors.textSecondary },
  actionBusy: { opacity: 0.6 },
  close: { minHeight: 48, justifyContent: 'center', alignItems: 'center',
    borderRadius: radius.md, backgroundColor: colors.bg, marginTop: spacing.xs },
  closeText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});