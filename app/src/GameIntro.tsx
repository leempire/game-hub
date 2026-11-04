/** At most three optional steps per complex game; local preference is per account. */
import React, { useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useIsFocused } from '@react-navigation/native';
import { AppModal } from './ui';
import { colors, fontSize, radius, spacing } from './theme';

const GUIDES: Record<string, { title: string; body: string }[]> = {
  stasis: [
    { title: '先找舱室躺下', body: '开局用摇杆进入空舱室，在休眠舱旁点「躺下」。躺下后开始产资源，整局不能起身。' },
    { title: '守住门，再扩建', body: '点击本舱空格建炮台，点击门和床升级。升床有门等级门槛，升级费用和可用操作都显示在面板中。' },
    { title: '和队友一起守站', body: '静默体靠砸门成长，炮台能把它打退。门快破时修补或升级，也可远程修补队友；打死静默体才算全队胜利。' },
  ],
  luzhanqi: [
    { title: '先确认阵型', body: '点击两枚己方棋子可交换，军旗在大本营、地雷在后两排、炸弹不能放前排。也可选预设，摆好再确认。' },
    { title: '看高亮走棋', body: '先选自己的棋子，再点高亮的落点。公路一步，铁路直走，工兵可沿铁路转弯；行营里的棋子受保护。' },
    { title: '夺旗取胜', body: '大子吃小子、同级同归于尽，炸弹与敌子同毁，工兵能挖地雷。吃掉军旗即胜；暗棋的敌子身份不会提前公开。' },
  ],
  battleship: [
    { title: '摆好舰队和武器', body: '先在自己的海域布阵，可拖动、旋转或重新随机。三种特殊武器分别挂在不同舰船上，携带舰被击沉后对应武器失效。' },
    { title: '命中就继续追击', body: '开战后切到敌方海域点格开炮。通常命中可继续射击，落空换手；先手的首发例外。击沉全部敌舰获胜。' },
    { title: '武器每局一次', body: '雷达探查区域、齐射点选三格、空袭覆盖九格。武器占用整回合，挂靠关系公开，可优先攻击对手武器舰。' },
  ],
  uno: [
    { title: '按颜色或牌面接牌', body: '高亮的手牌现在可以出，黑牌按提示先选颜色。没有合适的牌就摸牌，摸到后是否能出或过牌由按钮提示。' },
    { title: '留意房规与罚牌', body: '叠牌、抢出、换手等由房主在大厅设置。牌桌会显示当前颜色、待罚张数和轮到谁，别按另一套房规猜测。' },
    { title: '一张牌时喊 UNO', body: '只剩一张时点「喊 UNO」。开启抓罚时漏喊可能受罚；出完手牌的获胜方式取决于单局、组队或累计分赛制。' },
  ],
};
export default function GameIntro({ game, userId, practice, onPractice }: {
  game: string; userId?: number; practice?: boolean; onPractice: () => Promise<void>;
}) {
  const focused = useIsFocused();
  const steps = GUIDES[game];
  const key = `gamehub:guide:v1:${userId}:${game}`;
  const [visible, setVisible] = useState(false);
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  useEffect(() => {
    if (!steps || userId == null) return;
    let current = true;
    void AsyncStorage.getItem(key).then(value => { if (current && value == null) setVisible(true); })
      .catch(() => { if (current) setVisible(true); });
    return () => { current = false; };
  }, [key, steps, userId]);
  if (!steps) return null;
  const close = () => {
    setVisible(false);
    void AsyncStorage.setItem(key, 'seen').catch(() => {});
  };
  const start = async () => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    try { await onPractice(); close(); }
    catch (cause) { setError((cause as Error).message); }
    finally { lock.current = false; setBusy(false); }
  };
  return <View style={styles.wrap}>
    <View style={styles.row}>
      <TouchableOpacity style={styles.secondary} onPress={() => { setStep(0); setVisible(true); }}>
        <Text style={styles.secondaryText}>新手指引</Text>
      </TouchableOpacity>
      {!practice && <TouchableOpacity style={styles.secondary} disabled={busy} testID="start-practice"
        onPress={() => void start()}><Text style={styles.secondaryText}>{busy ? '正在进入…' : '打一局人机练习'}</Text></TouchableOpacity>}
    </View>
    {practice && <Text style={styles.hint}>私人练习局 · 不计入历史战绩、统计和排行榜</Text>}
    {!!error && <Text style={styles.error} accessibilityRole="alert">{error}</Text>}
    <AppModal visible={visible && focused} onClose={() => { if (!busy) close(); }} testID="game-intro">
      <Text style={styles.hint}>新手指引 · {step + 1} / {steps.length}</Text>
      <Text style={styles.title}>{steps[step].title}</Text>
      <Text style={styles.body}>{steps[step].body}</Text>
      <TouchableOpacity style={styles.primary} testID="guide-next" onPress={() => {
        if (step + 1 < steps.length) setStep(value => value + 1); else close();
      }}><Text style={styles.onPrimary}>{step + 1 < steps.length ? '下一步' : '开始游戏'}</Text></TouchableOpacity>
      <TouchableOpacity style={styles.secondary} testID="guide-skip" disabled={busy} onPress={close}><Text style={styles.secondaryText}>跳过</Text></TouchableOpacity>
      {!practice && <TouchableOpacity style={styles.secondary} disabled={busy} onPress={() => void start()}>
        <Text style={styles.secondaryText}>{busy ? '正在进入…' : '打一局人机练习（不计战绩）'}</Text>
      </TouchableOpacity>}
      {!!error && <Text style={styles.error} accessibilityRole="alert">{error}</Text>}
    </AppModal>
  </View>;
}
const styles = StyleSheet.create({
  wrap: { gap: spacing.sm, marginVertical: spacing.sm }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  secondary: { minHeight: 44, paddingHorizontal: spacing.md, justifyContent: 'center', alignItems: 'center',
    backgroundColor: colors.surfaceMuted, borderWidth: 1, borderColor: colors.borderStrong, borderRadius: radius.md },
  secondaryText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  primary: { minHeight: 48, justifyContent: 'center', alignItems: 'center', borderRadius: radius.md, backgroundColor: colors.primary },
  onPrimary: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  title: { color: colors.text, fontSize: fontSize.xl, fontWeight: '700' },
  body: { color: colors.textSecondary, fontSize: fontSize.md, lineHeight: 25, marginVertical: spacing.sm },
  hint: { color: colors.textMuted, fontSize: fontSize.sm }, error: { color: colors.danger, fontSize: fontSize.sm },
});
