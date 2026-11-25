import React, { useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { AppModal, FormInput } from './ui';
import { PersonCard, SocialButton } from './SocialCards';
import { requestFriend, respondFriend, searchContacts, type ContactSearch } from './contacts';
import { colors, fontSize, spacing } from './theme';

export default function ContactSearchModal({ visible, token, onClose, onChanged, onChat }: {
  visible: boolean; token: string | null; onClose: () => void; onChanged: () => void; onChat: (id: number) => void;
}) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<ContactSearch[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const version = useRef(0), locked = useRef(false);
  useEffect(() => {
    const generation = version;
    if (!visible) generation.current++;
    return () => { generation.current++; };
  }, [visible]);
  const search = async () => {
    if (!token || locked.current || !query.trim()) return;
    locked.current = true; setBusy(true); setError(''); setResult(null);
    const current = ++version.current;
    try { const response = await searchContacts(token, query); if (current === version.current) setResult(response.items); }
    catch (cause) { if (current === version.current) setError((cause as Error).message); }
    finally { locked.current = false; if (current === version.current) setBusy(false); }
  };
  const action = async (person: ContactSearch) => {
    if (!token || locked.current) return;
    if (person.relation === 'friend') { close(); onChat(person.id); return; }
    locked.current = true; setBusy(true); setError('');
    const current = ++version.current;
    try {
      if (person.relation === 'incoming') await respondFriend(token, person.id, 'accept');
      else await requestFriend(token, person.id);
      if (current === version.current) {
        setResult([{ ...person, relation: person.relation === 'incoming' ? 'friend' : 'outgoing' }]); onChanged();
      }
    } catch (cause) { if (current === version.current) setError((cause as Error).message); }
    finally { locked.current = false; if (current === version.current) setBusy(false); }
  };
  const close = () => { version.current++; setBusy(false); setError(''); setResult(null); onClose(); };
  return <AppModal visible={visible} onClose={close} testID="social-search-sheet">
    <Text style={styles.title}>添加好友</Text>
    <Text style={styles.hint}>输入对方的完整登录用户名，发送好友申请。</Text>
    <FormInput label="用户名" testID="social-search-input" value={query}
      onChangeText={value => { version.current++; setQuery(value); setResult(null); setError(''); setBusy(false); }}
      maxLength={20} autoCapitalize="none" autoCorrect={false} editable={!busy}
      placeholder="完整用户名（不是昵称）" onSubmitEditing={() => void search()} />
    <SocialButton label="查找用户" testID="social-search-submit" primary onPress={() => void search()} busy={busy} disabled={!query.trim()} />
    {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
    {result?.length === 0 && <Text style={styles.hint}>没有找到可添加的用户，请核对用户名。</Text>}
    {result?.map(person => <View key={person.id} testID={`social-search-result-${person.id}`}>
      <PersonCard person={person}>
        <SocialButton testID={`social-search-action-${person.id}`} busy={busy} disabled={person.relation === 'outgoing'}
          label={{ none: '发送申请', friend: '发消息', incoming: '接受申请', outgoing: '等待对方同意' }[person.relation]}
          onPress={() => void action(person)} primary={person.relation !== 'outgoing'} />
      </PersonCard>
    </View>)}
    <SocialButton label="关闭" testID="social-search-close" onPress={close} />
  </AppModal>;
}
const styles = StyleSheet.create({
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700' },
  hint: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 22 },
  error: { color: colors.danger, fontSize: fontSize.sm, marginVertical: spacing.xs },
});
