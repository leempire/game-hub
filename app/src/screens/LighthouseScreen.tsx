import React, { useEffect, useRef, useState } from 'react';
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import LighthouseRouteMap from '../LighthouseRouteMap';
import { apiFetch } from '../api';
import { useAuth } from '../auth';
import useForegroundPolling from '../useForegroundPolling';

const labels = {
  start: '启动发电机', light: '点亮灯塔', battery: '安装蓄电组', dock: '修复码头',
  power_salvage: '打捞机', power_signal: '信号台', upgrade_salvage: '升级打捞机',
  upgrade_signal: '升级信号台', dispatch: '准备出航',
  order_parts: '交付零件订单', order_tickets: '交付航运券订单',
  mode_balanced: '均衡', mode_parts: '优先打捞', mode_tickets: '优先信号',
};
type Action = keyof typeof labels;
type Resources = { parts: number; tickets: number };
type Offer = Resources & { charts: number; rate_gain: number };
type PortMode = 'balanced' | 'parts' | 'tickets';
const modes: PortMode[] = ['balanced', 'parts', 'tickets'];
type Route = Resources & { name: string; hours: number; cargo: string };
type Save = Resources & {
  charts: number; rates: Resources; offers: Partial<Record<Action, Offer>>;
  started_at: number | null; lit: boolean; battery: boolean; dock: boolean;
  salvage_on: boolean; signal_on: boolean; salvage_level: number; signal_level: number;
  completed: number; arrival_at: number | null; completed_at: number | null;
  route: Route | null; upcoming: Route | null; routes: string[];
  mode: PortMode; mode_rates: Record<PortMode, Resources> | null;
  now: number; revision: number; offline_cap: number; next_order_at: number | null;
  summary: { seconds: number; gains: Resources; arrived: string | null; capped: boolean };
};
type Command = { action: Action; operation_id: string; revision: number };

const scenes = [
  require('../../assets/lighthouse/dark.jpg'),
  require('../../assets/lighthouse/lit.jpg'),
  require('../../assets/lighthouse/dock.jpg'),
  require('../../assets/lighthouse/harbor.jpg'),
  require('../../assets/lighthouse/finale.jpg'),
];

const number = (value: number) => Math.floor(value).toLocaleString('zh-CN');
function duration(seconds: number) {
  if (seconds <= 0) return '即将完成';
  if (seconds < 60) return `${Math.ceil(seconds)}秒`;
  const minutes = Math.ceil(seconds / 60);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor(minutes / 60) % 24;
  const remainder = minutes % 60;
  return [days && `${days}天`, hours && `${hours}小时`, remainder && `${remainder}分`]
    .filter(Boolean).join(' ');
}

function costText(cost: Offer) {
  return [
    cost.parts > 0 && `${number(cost.parts)} 零件`,
    cost.tickets > 0 && `${number(cost.tickets)} 航运券`,
    cost.charts > 0 && `${cost.charts} 海图`,
  ].filter(Boolean).join(' · ');
}

export default function LighthouseScreen() {
  const { token } = useAuth();
  const [snapshot, setSnapshot] = useState<{ data: Save; receivedAt: number } | null>(null);
  const [clock, setClock] = useState(Date.now);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [working, setWorking] = useState(false);
  const [retry, setRetry] = useState<Command | null>(null);
  const [swap, setSwap] = useState<'power_salvage' | 'power_signal' | null>(null);
  const submitting = useRef(false);
  const requestEpoch = useRef(0);

  function accept(data: Save, showGains: boolean) {
    const receivedAt = Date.now();
    setSnapshot({ data, receivedAt });
    setClock(receivedAt);
    const { arrived, gains, seconds, capped } = data.summary;
    const messages = [];
    if (arrived) messages.push(`${arrived}已抵达，获得 1 张海图`);
    if (showGains && seconds >= 120 && (gains.parts || gains.tickets)) {
      messages.push(`已结算 ${duration(seconds)}${capped ? '（离线上限）' : ''} · +${number(gains.parts)} 零件 · +${number(gains.tickets)} 航运券`);
    }
    if (messages.length) setNotice(messages.join('。'));
  }

  const refresh = useForegroundPolling(token ?? '', async isCurrent => {
    if (!token || submitting.current) return;
    const epoch = requestEpoch.current;
    const data = await apiFetch<Save>('/api/lighthouse', {}, token);
    if (isCurrent() && epoch === requestEpoch.current) {
      accept(data, true);
      if (!retry) setError('');
    }
  }, err => { if (!submitting.current) setError((err as Error).message); }, 10_000);

  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  async function send(command: Command) {
    if (submitting.current || !token) return;
    submitting.current = true;
    requestEpoch.current++;
    setWorking(true);
    setError('');
    try {
      const data = await apiFetch<Save>('/api/lighthouse/actions', {
        method: 'POST', body: JSON.stringify(command),
      }, token);
      accept(data, false);
      setRetry(null);
      setSwap(null);
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      if (['补给不足', '这项操作当前不可用', '港口进度已变化，请刷新后重试', '操作编号已使用，请刷新进度'].includes(message)) {
        setRetry(null);
        setSwap(null);
        refresh();
      } else {
        setRetry(command);
      }
    } finally {
      submitting.current = false;
      setWorking(false);
    }
  }

  if (!snapshot) return (
    <View style={styles.empty}>
      <Text style={styles.muted}>{error || '正在同步岛屿…'}</Text>
      {!!error && <Pressable style={styles.button} onPress={refresh}><Text style={styles.buttonText}>重试</Text></Pressable>}
    </View>
  );

  const { data, receivedAt } = snapshot;
  const sinceSync = Math.max(0, Math.floor((clock - receivedAt) / 1000));
  const elapsed = Math.min(data.offline_cap, sinceSync);
  const now = data.now + sinceSync;
  const parts = data.parts + data.rates.parts * elapsed / 60;
  const tickets = data.tickets + data.rates.tickets * elapsed / 60;
  const scene = data.completed_at !== null ? 4 : data.completed ? 3 : data.dock ? 2 : data.lit ? 1 : 0;
  const primary = !data.started_at ? 'start' : !data.lit ? 'light' : !data.battery ? 'battery'
    : !data.dock ? 'dock' : 'dispatch';
  const routeName = data.route?.name;
  const voyageProgress = data.arrival_at !== null && data.route
    ? Math.max(0, Math.min(100, 100 * (1 - (data.arrival_at - now) / (data.route.hours * 3600)))) : 0;
  const orderHint = '本轮可任选一份港口订单，交付后获得 1 张海图。';

  function submit(action: Action) {
    void send({
      action, revision: data.revision,
      operation_id: `lighthouse-${data.revision}-${action}`,
    });
  }

  function renderAction(action: Action, title: string = labels[action], featured = false) {
    const cost = data.offers[action];
    if (!cost) return null;
    const missingParts = Math.max(0, cost.parts - parts);
    const missingTickets = Math.max(0, cost.tickets - tickets);
    const missingCharts = Math.max(0, cost.charts - data.charts);
    const eta = Math.max(
      missingParts ? 60 * missingParts / data.rates.parts : 0,
      missingTickets ? 60 * missingTickets / data.rates.tickets : 0,
    );
    const affordable = !eta && !missingCharts;
    const disabled = !affordable || working || !!retry;
    const price = costText(cost);
    return (
      <View style={styles.action} key={action}>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled}
          onPress={() => submit(action)}
          style={({ pressed }) => [styles.button, featured && styles.primary, disabled && styles.disabled, pressed && styles.pressed]}>
          <Text style={styles.buttonText}>{title}</Text>
          {!!price && <Text style={styles.price}>{price}</Text>}
        </Pressable>
        {!affordable && <Text style={styles.muted}>
          {missingCharts ? `还缺 ${missingCharts} 张海图` :
            !Number.isFinite(eta) ? '有缺少的资源尚未生产，请检查供电' : `当前配置约 ${duration(eta)} 凑齐`}
        </Text>}
      </View>
    );
  }

  function power(action: 'power_salvage' | 'power_signal', active: boolean) {
    const otherOn = action === 'power_signal' ? data.salvage_on : data.signal_on;
    const disabled = working || !!retry;
    return (
      <Pressable accessibilityRole="switch" accessibilityState={{ checked: active, disabled }}
        disabled={disabled} style={styles.power}
        onPress={() => {
          if (!data.battery && !active && otherOn) setSwap(action);
          else { setSwap(null); submit(action); }
        }}>
        <Text style={styles.heading}>{labels[action]}</Text>
        <Text style={styles.muted}>{active ? '供电中' : '未供电'}</Text>
      </Pressable>
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.resources}>
        <Text style={styles.resource}>零件 {number(parts)} <Text style={styles.muted}>+{data.rates.parts}/分</Text></Text>
        <Text style={styles.resource}>航运券 {number(tickets)} <Text style={styles.muted}>+{data.rates.tickets}/分</Text></Text>
        <Text style={styles.resource}>海图 {data.charts}</Text>
      </View>
      <View style={styles.hero}>
        <Image source={scenes[scene]} style={styles.image} resizeMode="cover"
          accessibilityLabel={['停摆的灯塔', '灯塔已点亮', '修复后的码头', '已启用的港口', '群岛全线通航'][scene]} />
      </View>
      {notice !== '' && <Pressable accessibilityRole="button" onPress={() => setNotice('')} style={styles.notice}>
        <Text style={styles.muted}>{notice} · 点按关闭</Text>
      </Pressable>}
      {(!!error || retry) && <View style={styles.notice}>
        {!!error && <Text style={styles.muted}>{error}</Text>}
        <Pressable accessibilityRole="button" disabled={working} style={styles.button}
          onPress={retry ? () => void send(retry) : refresh}><Text style={styles.buttonText}>重试</Text></Pressable>
      </View>}

      <View style={styles.panel}>
        <Text style={styles.eyebrow}>{data.completed} / {data.routes.length} 条航线</Text>
        <Text style={styles.title}>{data.completed_at !== null ? '群岛全线通航' :
          data.dock ? routeName : labels[primary]}</Text>
        {!!data.dock && !!data.route && <Text style={styles.muted}>{data.route.cargo}</Text>}
        {data.arrival_at !== null ? (
          <>
            <Text style={styles.muted}>
              {now < data.arrival_at ? `航行中 · ${duration(data.arrival_at - now)}后到港` : '正在确认到港'}
            </Text>
            <View accessibilityRole="progressbar" accessibilityLabel="航行进度"
              accessibilityValue={{ min: 0, max: 100, now: Math.floor(voyageProgress) }} style={styles.progressTrack}>
              <View style={[styles.progressFill, { width: `${voyageProgress}%` }]} />
            </View>
            {data.upcoming && <View style={styles.notice}>
              <Text style={styles.heading}>下一站 · {data.upcoming.name}</Text>
              <Text style={styles.muted}>
                备货到 {number(data.upcoming.parts)} 零件 / {number(data.upcoming.tickets)} 航运券
              </Text>
            </View>}
            {data.offers.order_parts ? <>
              <Text style={styles.muted}>{orderHint}</Text>
              {renderAction('order_parts')}
              {renderAction('order_tickets')}
            </> : <Text style={styles.muted}>本轮订单已完成。</Text>}
            {data.next_order_at !== null && <Text style={styles.muted}>下批订单 · {duration(data.next_order_at - now)}</Text>}
          </>
        ) : data.completed_at !== null ? (
          <Text style={styles.muted}>首轮经营耗时 {duration(data.completed_at - (data.started_at ?? data.completed_at))}。</Text>
        ) : <>
          <Text style={styles.muted}>{data.route && data.dock ?
            `航程 ${duration(data.route.hours * 3600)} · 到港获得 1 张海图` :
            { start: '启动后，打捞机会自动采集零件。', light: '点亮灯塔后，即可为信号台供电。',
              battery: '扩充供电后，打捞和发送信号可以同时进行。',
              dock: '修好码头后，岛屿就能派出渡船。', dispatch: '' }[primary]}</Text>
          {renderAction(primary, primary === 'dispatch' ? '装载补给并出航' : labels[primary], true)}
        </>}
      </View>

      {data.lit && <View style={styles.panel}>
        <Text style={styles.heading}>设施供电 · {Number(data.salvage_on) + Number(data.signal_on)} / {data.battery ? 2 : 1}</Text>
        <View style={styles.powerRow}>{power('power_salvage', data.salvage_on)}{power('power_signal', data.signal_on)}</View>
        {swap && <>
          <Text style={styles.muted}>{swap === 'power_signal' ?
            '切换后，打捞暂停，航运券每分钟 +1。' : '切换后，信号暂停，零件每分钟 +6。'}</Text>
          <View style={styles.powerRow}>
            <Pressable accessibilityRole="button" disabled={working || !!retry} style={styles.button}
              onPress={() => submit(swap)}><Text style={styles.buttonText}>确认切换</Text></Pressable>
            <Pressable accessibilityRole="button" style={styles.button} onPress={() => setSwap(null)}>
              <Text style={styles.buttonText}>取消</Text>
            </Pressable>
          </View>
        </>}
        {data.dock && <>
          {renderAction('upgrade_salvage', `打捞机 ${data.salvage_level + 2} 级 · +${data.offers.upgrade_salvage?.rate_gain} 零件/分`)}
          {renderAction('upgrade_signal', `信号台 ${data.signal_level + 2} 级 · +${data.offers.upgrade_signal?.rate_gain} 航运券/分`)}
        </>}
      </View>}

      {data.mode_rates && <View style={styles.panel}>
        <Text style={styles.heading}>港口调度</Text>
        <Text style={styles.muted}>按下一站的补给缺口分配港口人手。切换后立即生效。</Text>
        {modes.map(mode => {
          const action = `mode_${mode}` as const;
          const rate = data.mode_rates?.[mode];
          return mode === data.mode ? <Text key={action} style={styles.resource}>
            当前 · {labels[action]} · 零件 +{rate?.parts}/分 · 航运券 +{rate?.tickets}/分
          </Text> : renderAction(action, `${labels[action]} · 零件 +${rate?.parts}/分 · 航运券 +${rate?.tickets}/分`);
        })}
      </View>}

      {data.dock && <View style={styles.panel}>
        <Text style={styles.heading}>群岛航线</Text>
        <LighthouseRouteMap completed={data.completed} sailing={data.arrival_at !== null} />
        {data.routes.map((name, index) =>
          <Text key={name} style={index === data.completed ? styles.resource : styles.muted}>
            {index + 1}. {name} · {index < data.completed ? '已通航' :
              index === data.completed ? (data.arrival_at ? '航行中' : '待出航') : '未解锁'}
          </Text>
        )}
      </View>}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { backgroundColor: '#0a1921', flex: 1 },
  content: { width: '100%', maxWidth: 760, alignSelf: 'center', padding: 16, paddingBottom: 40, gap: 14 },
  empty: { flex: 1, backgroundColor: '#0a1921', alignItems: 'center', justifyContent: 'center', gap: 16 },
  resources: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', justifyContent: 'space-between' },
  resource: { color: '#f4d38c', fontWeight: '700', fontSize: 14 },
  hero: { overflow: 'hidden', borderRadius: 14, backgroundColor: '#183b42' },
  image: { width: '100%', aspectRatio: 1.18 },
  progressTrack: { height: 6, overflow: 'hidden', borderRadius: 3, backgroundColor: '#294650' },
  progressFill: { height: 6, backgroundColor: '#f4d38c' },
  panel: { backgroundColor: '#132b35', padding: 15, borderRadius: 14, gap: 9 },
  notice: { backgroundColor: '#203b45', padding: 10, borderRadius: 8, gap: 6 },
  eyebrow: { color: '#a7d4cd', fontWeight: '700', fontSize: 13 },
  title: { color: '#fff4d9', fontWeight: '800', fontSize: 26 },
  heading: { color: '#fff4d9', fontWeight: '700', fontSize: 16 },
  muted: { color: '#bbd0d2', fontSize: 14 },
  action: { gap: 4 },
  button: { backgroundColor: '#38535b', paddingHorizontal: 13, paddingVertical: 10, minHeight: 48,
    borderRadius: 8, justifyContent: 'center', gap: 3 },
  primary: { backgroundColor: '#735524' },
  buttonText: { color: '#fff5de', fontWeight: '700', fontSize: 15 },
  price: { color: '#f1dcad', fontSize: 13 },
  disabled: { backgroundColor: '#293e46' },
  pressed: { opacity: 0.78 },
  powerRow: { flexDirection: 'row', gap: 8 },
  power: { flex: 1, minHeight: 48, borderRadius: 8, backgroundColor: '#294650', padding: 10, gap: 4 },
});
