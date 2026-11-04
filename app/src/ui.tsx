/** Small shared controls; all layout, colors and shadows come from theme tokens. */
import React from 'react';
import { KeyboardAvoidingView, Modal, Platform, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import type { StyleProp, TextInputProps, ViewProps, ViewStyle } from 'react-native';
import { useModalBackGuard } from './backGuard';
import { colors, fontSize, radius, shadow, spacing } from './theme';

export function Card({ style, ...props }: ViewProps) {
  return <View {...props} style={[styles.card, style]} />;
}

interface AppModalProps {
  visible: boolean;
  onClose: () => void;
  children: React.ReactNode;
  testID?: string;
  cardStyle?: StyleProp<ViewStyle>;
}

/** Back closes the top modal; long content and keyboard remain reachable on small screens. */
export function AppModal({ visible, onClose, children, testID, cardStyle }: AppModalProps) {
  useModalBackGuard(visible, onClose);
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}
      supportedOrientations={['portrait', 'landscape']}>
      <KeyboardAvoidingView style={styles.overlay} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
        <ScrollView contentContainerStyle={styles.modalScroll} keyboardShouldPersistTaps="handled">
          <View testID={testID} style={[styles.modalCard, cardStyle]} accessibilityViewIsModal>
            {children}
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Modal>
  );
}

interface FormInputProps extends TextInputProps {
  label: string;
  error?: string | null;
  hint?: string;
}

export function FormInput({ label, error, hint, style, editable = true, ...props }: FormInputProps) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput {...props} editable={editable} accessibilityLabel={props.accessibilityLabel ?? label}
        accessibilityHint={error || hint || props.accessibilityHint}
        placeholderTextColor={colors.textMuted}
        style={[styles.input, !editable && styles.disabled, error ? styles.invalid : null, style]} />
      {!!error && <Text accessibilityRole="alert" style={styles.error}>{error}</Text>}
      {!error && !!hint && <Text style={styles.hint}>{hint}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: radius.lg, padding: spacing.md, ...shadow.card },
  overlay: { flex: 1, backgroundColor: colors.overlay },
  modalScroll: { flexGrow: 1, justifyContent: 'center', alignItems: 'center', padding: spacing.lg },
  modalCard: { width: '100%', maxWidth: 400, backgroundColor: colors.card,
    borderRadius: radius.lg, padding: spacing.lg, gap: spacing.sm, ...shadow.modal },
  field: { gap: spacing.xs, alignSelf: 'stretch' },
  label: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  input: { minHeight: 48, backgroundColor: colors.card, borderWidth: 1, borderColor: colors.borderStrong,
    borderRadius: radius.md, paddingHorizontal: spacing.sm + 4, paddingVertical: spacing.sm,
    color: colors.text, fontSize: fontSize.md },
  disabled: { backgroundColor: colors.disabled, color: colors.textMuted },
  invalid: { borderColor: colors.danger, borderWidth: 2 },
  error: { color: colors.danger, fontSize: fontSize.sm },
  hint: { color: colors.textSecondary, fontSize: fontSize.sm },
});
