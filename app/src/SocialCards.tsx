import React from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { ContactProfile } from './contacts';
import { displayContact } from './contacts';
import { Card } from './ui';
import { colors, fontSize, radius, spacing } from './theme';

export function SocialButton({ label, onPress, testID, disabled, danger, primary, busy }: {
  label: string; onPress: () => void; testID?: string; disabled?: boolean;
  danger?: boolean; primary?: boolean; busy?: boolean;
}) {
  return <TouchableOpacity accessibilityRole="button" accessibilityLabel={label}
    accessibilityState={{ disabled: !!disabled || !!busy, busy: !!busy }} testID={testID} disabled={disabled || busy}
    onPress={onPress} style={[styles.button, primary && styles.primary, danger && styles.danger,
      (disabled || busy) && styles.disabled]}>
    {busy ? <ActivityIndicator color={primary ? colors.white : colors.primary} />
      : <Text style={[styles.buttonText, primary && styles.white, danger && styles.dangerText]}>{label}</Text>}
  </TouchableOpacity>;
}
export function PersonCard({ person, preview, unread, children, onMore }: {
  person: ContactProfile; preview?: string; unread?: number; children?: React.ReactNode; onMore?: () => void;
}) {
  return <Card style={styles.person} testID={`social-person-${person.id}`}>
    <View style={styles.identity}>
      <View style={styles.avatar}><Text style={styles.initial}>{(Array.from(displayContact(person))[0] || '?').toUpperCase()}</Text></View>
      <View style={styles.nameWrap}>
        <Text numberOfLines={1} style={styles.name}>{displayContact(person)}</Text>
        <Text numberOfLines={1} style={styles.username}>@{person.username}</Text>
      </View>
      {!!unread && <View style={styles.badge}><Text style={styles.badgeText}>{unread > 99 ? '99+' : unread}</Text></View>}
      {onMore && <TouchableOpacity accessibilityRole="button" accessibilityLabel={`管理好友${displayContact(person)}`}
        testID={`social-more-${person.id}`} style={styles.more} onPress={onMore}>
        <Ionicons name="ellipsis-horizontal" size={22} color={colors.textSecondary} />
      </TouchableOpacity>}
    </View>
    {!!preview && <Text numberOfLines={2} style={styles.preview}>{preview}</Text>}
    {!!children && <View style={styles.actions}>{children}</View>}
  </Card>;
}
export function SocialEmpty({ title, detail }: { title: string; detail: string }) {
  return <Card style={styles.empty}>
    <Ionicons name="people-outline" size={34} color={colors.textMuted} />
    <Text style={styles.name}>{title}</Text><Text style={styles.emptyDetail}>{detail}</Text>
  </Card>;
}
const styles = StyleSheet.create({
  person: { gap: spacing.sm }, identity: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  avatar: { width: 44, height: 44, borderRadius: radius.full, backgroundColor: colors.surfaceMuted, alignItems: 'center', justifyContent: 'center' },
  initial: { color: colors.primary, fontSize: fontSize.lg, fontWeight: '700' },
  nameWrap: { flex: 1, minWidth: 0, gap: 3 }, name: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  username: { color: colors.textMuted, fontSize: fontSize.xs }, preview: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 21 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, justifyContent: 'flex-end' },
  button: { minHeight: 44, minWidth: 72, paddingHorizontal: spacing.md, justifyContent: 'center', alignItems: 'center',
    borderRadius: radius.md, backgroundColor: colors.surfaceMuted, borderWidth: 1, borderColor: colors.borderStrong },
  buttonText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  primary: { backgroundColor: colors.primary, borderColor: colors.primary }, white: { color: colors.white },
  danger: { borderColor: colors.danger }, dangerText: { color: colors.danger }, disabled: { opacity: 0.5 },
  more: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  badge: { backgroundColor: colors.danger, borderRadius: radius.full, minWidth: 22, paddingHorizontal: 6, paddingVertical: 3, alignItems: 'center' },
  badgeText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '700' },
  empty: { gap: spacing.sm, alignItems: 'center', paddingVertical: spacing.xl },
  emptyDetail: { color: colors.textSecondary, fontSize: fontSize.sm, textAlign: 'center', lineHeight: 22 },
});
