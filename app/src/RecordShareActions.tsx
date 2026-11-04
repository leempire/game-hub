/** Share is explicit and revocable. Copy is a separate gesture for WebKit clipboard support. */
import React, { useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { AppModal } from './ui';
import { createReplayLink, revokeReplay } from './sharing';
import { colors, fontSize, radius, spacing } from './theme';

export default function RecordShareActions({ token, recordId }: { token: string; recordId: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const create = async () => {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null);
    try { setUrl(await createReplayLink(token, recordId)); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const copy = () => {
    if (!url) return;
    void Clipboard.setStringAsync(url).then(() => setNotice('链接已复制'))
      .catch(() => setError('暂时无法复制，请长按下方链接手动复制'));
  };
  const revoke = async () => {
    setConfirm(false); setBusy(true); setError(null);
    try { await revokeReplay(token, recordId); setUrl(null); setNotice('分享已撤销，旧链接已失效'); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  return <View style={styles.wrap}>
    <TouchableOpacity accessibilityRole="button" style={styles.button} disabled={busy}
      onPress={event => { event.stopPropagation(); void create(); }}>
      <Text style={styles.buttonText}>{busy ? '正在处理…' : url ? '重新获取链接' : '生成回放分享链接'}</Text>
    </TouchableOpacity>
    {url && <>
      <Text style={styles.hint}>持有链接的人可免登录查看最终局面；不会公开房间号或 UNO 手牌。</Text>
      <Text selectable style={styles.url}>{url}</Text>
      <View style={styles.row}>
        <TouchableOpacity style={styles.button} onPress={event => { event.stopPropagation(); copy(); }}>
          <Text style={styles.buttonText}>复制链接</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.button} onPress={event => { event.stopPropagation(); setConfirm(true); }}>
          <Text style={styles.danger}>撤销分享</Text>
        </TouchableOpacity>
      </View>
    </>}
    {!!notice && <Text accessibilityLiveRegion="polite" style={styles.hint}>{notice}</Text>}
    {!!error && <Text accessibilityRole="alert" style={styles.danger}>{error}</Text>}
    <AppModal visible={confirm} onClose={() => setConfirm(false)}>
      <Text style={styles.title}>撤销这条分享？</Text>
      <Text style={styles.hint}>旧链接将无法查看，自己的历史战绩会保留。</Text>
      <TouchableOpacity style={styles.button} onPress={() => void revoke()}><Text style={styles.danger}>确认撤销</Text></TouchableOpacity>
      <TouchableOpacity style={styles.button} onPress={() => setConfirm(false)}><Text style={styles.buttonText}>取消</Text></TouchableOpacity>
    </AppModal>
  </View>;
}
const styles = StyleSheet.create({
  wrap: { gap: spacing.sm, marginTop: spacing.md },
  row: { flexDirection: 'row', gap: spacing.sm },
  button: { minHeight: 48, justifyContent: 'center', alignItems: 'center', paddingHorizontal: spacing.md,
    backgroundColor: colors.surfaceMuted, borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong },
  buttonText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  title: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700' },
  hint: { color: colors.textSecondary, fontSize: fontSize.sm, lineHeight: 20 },
  url: { color: colors.primary, fontSize: fontSize.sm },
  danger: { color: colors.danger, fontSize: fontSize.sm },
});
