import { View, Text, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, spacing, fontSize } from '../theme';

export default function SocialTab() {
  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <Ionicons name="globe" size={64} color={colors.primary} />
        <Text style={styles.title}>社交功能</Text>
        <Text style={styles.subtitle}>正在建设中...</Text>
        <View style={styles.divider} />
        <Text style={styles.hint}>好友、聊天、排行榜等功能即将上线</Text>
        <View style={styles.dots}>
          <View style={[styles.dot, styles.dotActive]} />
          <View style={styles.dot} />
          <View style={styles.dot} />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.md,
  },
  card: {
    backgroundColor: colors.card,
    borderRadius: 24,
    padding: spacing.xxl,
    alignItems: 'center',
    maxWidth: 400,
    width: '100%',
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08,
    shadowRadius: 16,
    elevation: 6,
  },
  icon: { marginBottom: spacing.md },
  title: { fontSize: fontSize.xxl, fontWeight: '800', color: colors.text },
  subtitle: { fontSize: fontSize.lg, color: colors.primary, fontWeight: '600', marginTop: spacing.xs },
  divider: {
    width: 48,
    height: 3,
    backgroundColor: colors.primary,
    borderRadius: 2,
    marginVertical: spacing.lg,
  },
  hint: {
    fontSize: fontSize.md,
    color: colors.textSecondary,
    textAlign: 'center',
    lineHeight: 24,
  },
  dots: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.lg },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.border,
  },
  dotActive: { backgroundColor: colors.primary },
});
