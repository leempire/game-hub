import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import { HeaderBackButton, useHeaderHeight } from '@react-navigation/elements';
import { useNavigation } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { leaveRoom } from '../api';
import { useAuth } from '../auth';
import { onConfirmLeave } from '../backGuard';
import GameShell, { GameResult } from '../GameShell';
import type { RootStackParamList } from '../navigation';
import { colors, fontSize, gameSurfaces, radius, spacing } from '../theme';
import { AppModal, Card } from '../ui';
import TurtleSoupSummary from '../TurtleSoupSummary';
import useTurtleSoup from '../useTurtleSoup';
import { soupReasons } from '../turtleSoup';

export default function TurtleSoupScreen({ code }: { code: string }) {
  const { token, user } = useAuth();
  const state = useTurtleSoup(code, token, user?.id);
  const { game, result, room, connected, sending, waiting, error, draft, mode, close } = state;
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const headerHeight = useHeaderHeight();
  const { bottom } = useSafeAreaInsets();
  const [confirmation, setConfirmation] = useState<'leave' | 'reveal' | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [leaveError, setLeaveError] = useState('');
  const skipRemove = useRef(false);
  const scroll = useRef<ScrollView | null>(null);
  const backToRoom = useCallback(() => {
    skipRemove.current = true;
    close();
    navigation.popTo('Room', { code });
  }, [close, navigation, code]);
  const requestBack = useCallback(() => {
    if (result || waiting || !game) backToRoom();
    else setConfirmation('leave');
  }, [result, waiting, game, backToRoom]);
  useLayoutEffect(() => {
    if (Platform.OS === 'web') navigation.setOptions({ headerLeft: ({ tintColor }) => (
      <HeaderBackButton tintColor={tintColor} onPress={requestBack} accessibilityLabel="返回" />
    ) });
  }, [navigation, requestBack]);
  useEffect(() => navigation.addListener('beforeRemove', event => {
    if (Platform.OS === 'web' || skipRemove.current) return;
    event.preventDefault();
    requestBack();
  }), [navigation, requestBack]);
  useEffect(() => onConfirmLeave(requestBack), [requestBack]);
  const leave = async () => {
    if (!token || leaving) return;
    setLeaving(true);
    try {
      await leaveRoom(token, code);
      skipRemove.current = true;
      state.close();
      navigation.popTo('MainTabs');
    } catch (cause) {
      setLeaveError(cause instanceof Error ? cause.message : '离开失败，请重试');
    } finally {
      setLeaving(false);
    }
  };
  const names = new Map(room?.players.map(player => [player.id, player.nickname || player.username]));
  const name = (id: number) => id === user?.id ? '我' : names.get(id) ?? `玩家 ${id}`;
  const disabled = !connected || !game?.can_submit || sending || !draft.trim();
  const maxLength = mode === 'ask' ? game?.question_max_length : game?.guess_max_length;

  return <KeyboardAvoidingView style={styles.page} behavior="padding" keyboardVerticalOffset={headerHeight}>
    <GameShell game="turtle_soup" code={code} connected={connected}
      players={room?.players.map(player => ({ id: player.id, name: player.nickname || player.username,
        isMe: player.id === user?.id, connected: player.connected }))}
      status={<View style={styles.status}>
        {game?.busy && <ActivityIndicator color={colors.primary} />}
        <Text style={styles.note} accessibilityLiveRegion="polite">
          {game?.busy ? 'AI 主持正在思考，请稍候…' : game ? '合作还原真相 · 一次验证一个猜想' : '正在同步…'}
        </Text>
      </View>}
      actions={<View style={[styles.actions, { paddingBottom: Math.max(bottom, spacing.xs) }]}>
        {error ? <Text testID="soup-error" style={styles.error} accessibilityRole="alert">{error}</Text> : null}
        {game && !game.ai_enabled && <Text style={styles.error}>房主 AI 积分不足，暂时无法提问。</Text>}
        {game && !result && <>
          <View style={styles.modes}>
            {(['ask', 'guess'] as const).map(value => <TouchableOpacity key={value}
              testID={`soup-mode-${value}`} accessibilityRole="button" accessibilityState={{ selected: mode === value }}
              onPress={() => state.setMode(value)} style={[styles.mode, mode === value && styles.selected]}>
              {mode === value && <Ionicons name="checkmark" size={18} color={colors.white} />}
              <Text style={[styles.modeText, mode === value && styles.modeActive]}>
                {value === 'ask' ? '提问' : '猜汤底'}
              </Text>
            </TouchableOpacity>)}
          </View>
          <Text style={styles.note}>{mode === 'ask' ? '问一个能用是或否回答的问题' : '写出事件的关键经过和原因'}</Text>
          <View style={styles.inputRow}>
            <TextInput testID="soup-input" accessibilityLabel={mode === 'ask' ? '你的问题' : '你的汤底推理'}
              style={styles.input} value={draft} editable={connected}
              onChangeText={state.setDraft} placeholder={`${mode === 'ask' ? '输入问题' : '还原汤底'}（最多 ${maxLength ?? '—'} 字）`}
              placeholderTextColor={colors.textMuted} autoCorrect={false} autoCapitalize="none" />
            <TouchableOpacity testID="soup-submit" accessibilityRole="button" disabled={disabled}
              onPress={state.send} style={[styles.button, styles.primary, disabled && styles.disabled]}>
              <Text style={disabled ? styles.note : styles.buttonText}>{sending ? '提交中' : '发送'}</Text>
            </TouchableOpacity>
          </View>
          <View style={styles.footer}>
            {game.can_reveal && <TouchableOpacity testID="soup-reveal" accessibilityRole="button"
              disabled={!connected} onPress={() => setConfirmation('reveal')} style={styles.link}>
              <Text style={styles.note}>结束并揭晓</Text>
            </TouchableOpacity>}
            <TouchableOpacity accessibilityRole="button" onPress={() => setConfirmation('leave')} style={styles.link}>
              <Text style={styles.note}>离开房间</Text>
            </TouchableOpacity>
          </View>
        </>}
        {!game && !result && <TouchableOpacity style={[styles.button, styles.primary]} onPress={backToRoom}>
          <Text style={styles.buttonText}>返回房间</Text>
        </TouchableOpacity>}
      </View>}>
      <View style={styles.content}>
        {game && <Card style={styles.surface}>
          <Text style={styles.eyebrow}>汤面 · {game.requests.length} / {game.request_limit} 次提交</Text>
          <Text style={styles.title}>{game.title}</Text>
          <Text testID="soup-surface" style={styles.story} selectable>{game.surface}</Text>
        </Card>}
        <ScrollView ref={scroll} style={styles.history} keyboardShouldPersistTaps="handled"
          contentContainerStyle={styles.historyContent} onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}>
          {game?.requests.length === 0 && <Text style={styles.note}>从人物、时间或反常细节开始提问。猜出经过后切换「猜汤底」。</Text>}
          {game?.requests.map(request => <Card key={`${request.user_id}-${request.id}`} style={styles.message}>
            <Text style={styles.eyebrow}>{name(request.user_id)} · {request.kind === 'ask' ? '提问' : '猜汤底'}</Text>
            <Text style={styles.story} selectable>{request.text}</Text>
            <Text style={[styles.answer, request.status === 'failed' && styles.error]}>
              AI 主持：{request.status === 'pending' ? '正在思考…' : request.status === 'failed'
                ? request.error : request.kind === 'guess' ? request.correct ? '猜中了！' : '尚未猜中，继续推理' : request.answer}
            </Text>
            {request.hint && <Text style={styles.story} selectable>主持提示：{request.hint}</Text>}
          </Card>)}
        </ScrollView>
      </View>
    </GameShell>
    <AppModal visible={confirmation !== null} onClose={() => { if (!leaving) setConfirmation(null); }}>
      <Text style={styles.title}>{confirmation === 'reveal' ? '结束本局并揭晓汤底？' : '确认离开房间？'}</Text>
      <Text style={styles.note}>{confirmation === 'reveal' ? '所有人会看到答案，本局记为未解开。'
        : '中途离开记为负局；支付人离开会结束本局。'}</Text>
      {!!leaveError && <Text style={styles.error}>{leaveError}</Text>}
      <TouchableOpacity testID="soup-confirm" disabled={leaving} accessibilityRole="button"
        style={[styles.button, styles.primary]} onPress={() => {
          if (confirmation === 'reveal') { state.reveal(); setConfirmation(null); }
          else void leave();
        }}><Text style={styles.buttonText}>{leaving ? '正在离开…' : '确认'}</Text></TouchableOpacity>
      <TouchableOpacity accessibilityRole="button" style={styles.link} disabled={leaving} onPress={() => setConfirmation(null)}>
        <Text style={styles.note}>取消</Text>
      </TouchableOpacity>
    </AppModal>
    {result && <GameResult visible title="海龟汤结算" result={result.reason === 'solved' ? '推理成功' : '本局结束'}
      reason={soupReasons[result.reason]} onBackToRoom={backToRoom} roomButtonTestID="soup-back-to-room"
      onViewHistory={() => { backToRoom(); navigation.navigate('History'); }}>
      <TurtleSoupSummary summary={result.summary} testID="soup-result" />
    </GameResult>}
  </KeyboardAvoidingView>;
}

const styles = StyleSheet.create({
  page: { flex: 1, minHeight: 0 },
  content: { flex: 1, minHeight: 0, alignSelf: 'center', width: '100%', maxWidth: 720, padding: spacing.sm, gap: spacing.sm },
  surface: { gap: spacing.xs, backgroundColor: gameSurfaces.selection },
  history: { flex: 1, minHeight: 0 },
  historyContent: { gap: spacing.sm, paddingBottom: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '800' },
  eyebrow: { color: colors.textSecondary, fontSize: fontSize.sm },
  story: { color: colors.text, fontSize: fontSize.md, lineHeight: 23 },
  note: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 20 },
  message: { gap: spacing.xs },
  answer: { color: colors.primaryDark, fontSize: fontSize.md, fontWeight: '700', lineHeight: 22 },
  error: { color: colors.danger, fontSize: fontSize.sm, lineHeight: 20 },
  status: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, padding: spacing.xs },
  actions: { alignSelf: 'center', width: '100%', maxWidth: 720, paddingHorizontal: spacing.sm, gap: spacing.xs },
  modes: { flexDirection: 'row', gap: spacing.sm },
  mode: { flex: 1, minHeight: 44, flexDirection: 'row', gap: spacing.xs, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong,
    alignItems: 'center', justifyContent: 'center' },
  selected: { backgroundColor: colors.primary, borderColor: colors.primary },
  modeText: { color: colors.text, fontSize: fontSize.sm, fontWeight: '600' },
  modeActive: { color: colors.white, fontWeight: '800' },
  inputRow: { flexDirection: 'row', gap: spacing.sm },
  input: { flex: 1, minWidth: 0, minHeight: 48, color: colors.text, backgroundColor: colors.card,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong, paddingHorizontal: spacing.sm, fontSize: fontSize.md },
  button: { minHeight: 48, justifyContent: 'center', alignItems: 'center', borderRadius: radius.md, paddingHorizontal: spacing.md },
  primary: { backgroundColor: colors.primary },
  disabled: { backgroundColor: colors.disabled },
  buttonText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  footer: { flexDirection: 'row', justifyContent: 'space-between' },
  link: { minHeight: 44, justifyContent: 'center', alignItems: 'center', paddingHorizontal: spacing.sm },
});
