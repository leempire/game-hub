import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView, ActivityIndicator, Platform, Modal,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room, RoomPlayer } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import { useModalBackGuard } from '../backGuard';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';
import { RoomSettingsRow, RoomSettingsSheet } from '../roomSettings';
import { GameHelpButton } from '../help';
import type { GameKey } from '../games';
// 游戏名的唯一来源（这一行以前是一串嵌套三元，与主页卡片各写一遍）
import { gameTitle } from '../games';

interface Props {
  code: string;
  onLeave: () => void;
  onGameStart: (code: string, game?: string) => void;
}

export default function RoomScreen({ code, onLeave, onGameStart }: Props) {
  const { token, user } = useAuth();
  const [room, setRoom] = useState<Room | null>(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const socketRef = useRef<RoomSocket | null>(null);
  // 海战棋的人机难度档（简单/普通/困难）。难度存在每个 bot 自己身上，一房可混搭。
  // **必须放在 `if (loading)` 提前返回之前**——放到下面会让首次渲染少调一个 hook，
  // React 直接报「Rendered more hooks than during the previous render」并白屏。
  const [botDifficulty, setBotDifficulty] = useState('normal');
  // 玩法设置抽屉（v1.15）。同样**必须在 `if (loading)` 提前返回之前**（同上）。
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 换位（v1.16）：`swapFrom` 是「别人请求和我换」待答的那条；`swapWaitTo` 是
  // 「我已向谁发出请求」—— 那一行按钮改成「等待同意」，免得反复点。
  // 同样必须在 `if (loading)` 提前返回之前。
  const [swapFrom, setSwapFrom] = useState<{ id: number; name: string } | null>(null);
  const [swapWaitTo, setSwapWaitTo] = useState<number | null>(null);
  useModalBackGuard(swapFrom !== null, () => setSwapFrom(null));
  // WS sync 到达后 REST 快照即为陈旧数据（如 connected），不再覆盖实时状态
  const syncedRef = useRef(false);
  // Track game type in a ref to avoid stale closure in handleGameStart
  const gameTypeRef = useRef<string | undefined>(undefined);

  // 自愈：主动拉一次房间状态，若「房间在游戏中且自己仍在局中」就进对局页。
  //
  // 为什么需要它：服务端每个 (房间,玩家) 只保留一条连接，漏收一条 `game_start`
  // 广播（僵尸连接）就再没有任何机制能把人拉进对局页——房主一直待在房间页、
  // 焦点从没变过，连「聚焦重连」都不会触发，于是**永远卡在房间页，再点开始也没反应**。
  // 聚焦时和点「开始游戏」后各兜一次，漏广播能自愈。
  const refreshAndEnterGame = useCallback(async () => {
    if (!token) return;
    try {
      const r = await getRoom(token, code);
      setRoom(r);
      gameTypeRef.current = r.game;
      if (r.status === 'playing' && r.in_game !== false) {
        onGameStart(code, r.game);
      }
    } catch {
      // 房间可能已解散：交给 WS 的 room_closed 处理，这里不打扰用户
    }
  }, [token, code, onGameStart]);

  const fetchRoom = useCallback(async () => {
    if (!token) return;
    try {
      const r = await getRoom(token, code);
      if (!syncedRef.current) {
        setRoom(r);
        gameTypeRef.current = r.game;
      }
    } catch (err) {
      notify('加载失败', (err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token, code]);

  // Connect WS on mount
  useEffect(() => {
    if (!token) return;
    fetchRoom();

    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      syncedRef.current = true;
      if (roomData) {
        setRoom(roomData);
        gameTypeRef.current = roomData.game;
      }
      // 游戏进行中才跳对局页。**还要看自己是否仍在局中**：扫雷/飞行棋/2048
      // 允许「本人已出局、其他人继续」，这类玩家回到房间页后不能再被弹回去
      // （否则点「退出」没反应、页面闪一下还在原处）。服务端用 in_game 告知。
      if (msg.game && roomData.status === 'playing' && msg.in_game !== false) {
        onGameStart(code, roomData.game);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      syncedRef.current = true;
      if (roomData) {
        setRoom(roomData);
        gameTypeRef.current = roomData.game;
      }
    };

    const handleGameStart = () => {
      onGameStart(code, gameTypeRef.current);
    };

    const handleRoomClosed = () => {
      notify('房间已解散', '房主已离开');
      ws.close();
      onLeave();
    };

    const handleError = (msg: SocketMessage) => {
      notify('提示', msg.message as string);
    };

    // 换位（v1.16）：`swap_request` 只发给被请求的那个人，弹窗问同意不同意；
    // `swap_result` 只回给发起人（成功/被拒/人机自动同意三种都在这一条里）。
    const handleSwapRequest = (msg: SocketMessage) => {
      setSwapFrom({
        id: msg.from_id as number,
        name: (msg.from_name as string) || '对方',
      });
    };
    const handleSwapResult = (msg: SocketMessage) => {
      // `accepted === null` 是「请求已转达、对方还没答复」那条 ack（服务端对真人目标
      // 立刻回这一条）——**不能拿它清待答态**，否则「等待同意」只存在一帧，
      // 按钮闪回「换位」、玩家以为没发出去又点一次。只有拿到最终答复（同意/拒绝/
      // 人机自动同意）才复位。
      if (msg.accepted !== null) setSwapWaitTo(null);
      notify('换位', (msg.message as string) || '换位请求已处理');
    };

    ws.on('sync', handleSync);
    ws.on('lobby', handleLobby);
    ws.on('game_start', handleGameStart);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);
    ws.on('swap_request', handleSwapRequest);
    ws.on('swap_result', handleSwapResult);

    ws.connect();

    return () => {
      ws.close();
    };
  }, [token, code, fetchRoom, onGameStart, onLeave]);

  // 从对局页返回时本页不会重挂载，其 socket 可能已被对局页连接替换并被服务端弹出；
  // 每次重新聚焦强制重连，保证重新注册能收到 game_start/lobby 广播（跳过首次挂载）。
  // 失焦时暂停自动重连：否则服务端重启后本页与对局页会同时重连并争抢
  // (房间,玩家) 槽位，抢输的一方将永远收不到广播。
  const firstFocusRef = useRef(true);
  // 点「开始游戏」后的自愈兜底定时器（见 handleStart）
  const startFallbackRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 本页当前是否在前台：兜底定时器只在本页还开着时才补查，
  // 已经跳进对局页了就别再多跑一次 REST（navigate 同路由虽是无害的，但没必要）
  const focusedRef = useRef(true);
  useEffect(() => () => {
    if (startFallbackRef.current) clearTimeout(startFallbackRef.current);
  }, []);
  useFocusEffect(useCallback(() => {
    const ws = socketRef.current;
    focusedRef.current = true;
    if (firstFocusRef.current) {
      firstFocusRef.current = false;
    } else {
      syncedRef.current = false;
      ws?.resume();
      ws?.reconnect();
      // 不只是刷新状态：对局已经在跑的话要顺手把自己送进去（自愈漏收的广播）
      refreshAndEnterGame();
    }
    return () => { focusedRef.current = false; ws?.pause(); };
  }, [refreshAndEnterGame]));

  const copyCode = async () => {
    await Clipboard.setStringAsync(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleReady = () => {
    socketRef.current?.ready();
  };

  const handleUnready = () => {
    socketRef.current?.unready();
  };

  const handleStart = () => {
    socketRef.current?.start();
    // 兜底自愈：万一 game_start 广播漏收（僵尸连接），几秒后主动查一次——
    // 房间已在游戏中就自己进对局页，别让房主卡在房间页反复点「开始游戏」
    if (startFallbackRef.current) clearTimeout(startFallbackRef.current);
    startFallbackRef.current = setTimeout(() => {
      if (focusedRef.current) refreshAndEnterGame();
    }, 2500);
  };

  const handleLeave = async () => {
    if (!token) return;
    try {
      await leaveRoom(token, code);
      socketRef.current?.leave();
      socketRef.current?.close();
      onLeave();
    } catch (err) {
      notify('离开失败', (err as Error).message);
    }
  };

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  if (!room) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>房间不存在</Text>
        <TouchableOpacity style={styles.backBtn} onPress={onLeave}>
          <Text style={styles.backBtnText}>返回</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const me = room.players.find((p) => p.id === user?.id);
  const isHost = me?.is_host ?? false;
  const isReady = me?.ready ?? false;
  const allReady = room.players.every((p) => p.ready || p.is_host);
  const isFull = room.players.length >= room.max_players;
  const isMine = room.game === 'minesweeper';
  const isLudo = room.game === 'ludo';
  const is2048 = room.game === '2048';
  const isTetris = room.game === 'tetris';
  const isUno = room.game === 'uno';
  const isStasis = room.game === 'stasis';
  // **房间级设置**（v1.15）：扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋棋盘 /
  // UNO 档位与房规全部收进这一份结构，界面只按 `kind` 通用渲染 —— 客户端不认识
  // 任何 `key` 的语义，一行规则都不写。没有可调项的游戏（五子棋等）服务端不下发它。
  const settings = room.settings ?? [];
  // 深空休眠支持人机（stasis_ai.py 做人机队友，由 tick 循环按 1 秒间隔驱动，不是回合制调度）
  const supportsBots = room.game === 'gomoku' || room.game === 'reversi' || room.game === 'battleship' || room.game === 'dots' || room.game === 'ludo' || room.game === 'xiangqi' || room.game === 'connect4' || room.game === 'uno' || room.game === 'stasis' || room.game === 'luzhanqi';
  // 房间只有两种状态：非游戏中即大厅（对局结束的房间回到等待态，可续开）
  const isLobbyStatus = room.status !== 'playing';
  // 掉线只在游戏中存在：等待房没有「掉线」概念（人不在房里就没有掉线一说）
  const showOffline = room.status === 'playing';
  const canAddBot = isHost && !isFull && isLobbyStatus && supportsBots;
  // 人数要求：扫雷 1 人即可开（单机）；2048 / 俄罗斯方块 1-4 人各自独立盘
  // （1 人即可开）；飞行棋 / UNO 2-4 人、深空休眠 2-6 人（≥2 可开，对齐 max_players）；其余须满员
  const needMorePlayers = isMine || is2048 || isTetris
    ? false
    : (isLudo || isUno || isStasis) ? room.players.length < 2 : !isFull;
  const canStart = allReady && !needMorePlayers;

  // 海战棋的人机有难度档（简单=随机、普通=搜索追击、困难=概率热力图）。
  // botDifficulty 的 useState 在组件顶部（提前返回之前），这里只做取值与展示。
  const isBattleship = room.game === 'battleship';
  const BOT_DIFFICULTIES: Array<{ key: string; label: string }> = [
    { key: 'easy', label: '简单' },
    { key: 'normal', label: '普通' },
    { key: 'hard', label: '困难' },
  ];

  const handleAddBot = () => {
    socketRef.current?.addBot(isBattleship ? botDifficulty : undefined);
  };

  const handleRemoveBot = (botId: number) => {
    socketRef.current?.removeBot(botId);
  };

  // 请求与某人换座位。**不把按钮置灰**（RNW 下 `disabled` 等于 `pointer-events:none`，
  // 点了连提示都没有）—— 重复点由服务端去重（同一对只留一条待答请求），
  // 界面靠 `swapWaitTo` 把文案换成「等待同意」。
  const handleSwapRequest = (p: RoomPlayer) => {
    setSwapWaitTo(p.id);
    socketRef.current?.swapRequest(p.id);
  };

  const answerSwap = (accept: boolean) => {
    const req = swapFrom;
    setSwapFrom(null);
    if (req) socketRef.current?.swapRespond(req.id, accept);
  };

  return (
    <>
      <ScrollView contentContainerStyle={styles.container}>
        {/* Room code */}
        <TouchableOpacity style={styles.codeBox} onPress={copyCode} activeOpacity={0.7}>
          <Text style={styles.codeLabel}>房间号</Text>
          <Text style={styles.codeValue}>{code}</Text>
          <View style={styles.copyHintRow}>
            {copied && <Ionicons name="checkmark" size={12} color={colors.white} />}
            <Text style={styles.copyHint}>{copied ? '已复制' : '点击复制'}</Text>
          </View>
        </TouchableOpacity>

        {/* 上一局已结束提示：按已打完的局数判断，不再依赖第三种房间状态 */}
        {(room.round ?? 0) > 0 && (
          <View style={styles.finishedBanner}>
            <Ionicons name="checkmark-circle-outline" size={16} color={colors.textSecondary} />
            <Text style={styles.finishedBannerText}> 上一局已结束，可继续开始新一局</Text>
          </View>
        )}

        {/* Room info */}
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>游戏类型</Text>
          <View style={styles.infoValueRow}>
            <Text style={styles.infoValue}>
              {gameTitle(room.game)}
            </Text>
            <GameHelpButton game={room.game as GameKey} size={24} />
          </View>
        </View>
        <View style={styles.infoRow}>
          <Text style={styles.infoLabel}>房间名</Text>
          <Text style={styles.infoValue}>{room.name}</Text>
        </View>
        {/* **房间级设置**（v1.15）：扫雷难度 / 军棋玩法 / 深空休眠地图 / 海战棋棋盘 /
            UNO 档位与房规全部收进这一行，点开是从底部升起的抽屉。
            （以前五处各是一排芯片，光这一块就占了两百来行；UNO 的 3 个档位 + 7 条
            房规一铺开，整页就很难看。）非房主也能点开看当前设置，只是抽屉里不能改。 */}
        {settings.length > 0 && (
          <RoomSettingsRow items={settings} onOpen={() => setSettingsOpen(true)} />
        )}
        {room.has_password && (
          <View style={styles.infoRow}>
            <Text style={styles.infoLabel}>密码房</Text>
            <Ionicons name="lock-closed" size={16} color={colors.text} />
          </View>
        )}

        {/* Players */}
        <Text style={styles.sectionTitle}>
          玩家 ({room.players.length}/{room.max_players})
        </Text>
        {room.players.map((p: RoomPlayer) => (
          <View key={p.id} style={styles.playerRow}>
            <View style={styles.playerInfo}>
              <Text style={[styles.playerName, showOffline && !p.connected && styles.playerDisconnected]}>
                {p.nickname || p.username}
              </Text>
              {p.is_host && <Text style={styles.hostBadge}>房主</Text>}
              {p.is_bot && (
                <Text style={styles.botBadge}>
                  人机{p.ai_difficulty
                    ? `·${BOT_DIFFICULTIES.find((d) => d.key === p.ai_difficulty)?.label ?? ''}`
                    : ''}
                </Text>
              )}
            </View>
            <View style={styles.playerBadges}>
              {showOffline && !p.connected && (
                <Text style={[styles.statusPill, styles.disconnectedPill]}>断线</Text>
              )}
              {p.ready && <Text style={[styles.statusPill, styles.readyPill]}>已准备</Text>}
              {!p.ready && !p.is_host && <Text style={[styles.statusPill, styles.unreadyPill]}>未准备</Text>}
              {/* 换位（v1.16）：座位就是玩家列表里的下标，开局时决定先手 / 出牌顺序。
                  **只在大厅里出现**（对局中座位已锁死，服务端也会拒）。 */}
              {isLobbyStatus && p.id !== user?.id && (
                <TouchableOpacity
                  testID={`swap-seat-${p.id}`}
                  style={[styles.rowBtn, swapWaitTo === p.id ? styles.pendingBtn : styles.swapSeatBtn]}
                  onPress={() => handleSwapRequest(p)}
                >
                  <Text style={[styles.rowBtnText, swapWaitTo === p.id ? styles.pendingBtnText : styles.swapSeatBtnText]}>
                    {swapWaitTo === p.id ? '等待同意' : '换位'}
                  </Text>
                </TouchableOpacity>
              )}
              {isHost && p.is_bot && isLobbyStatus && (
                <TouchableOpacity
                  testID={`remove-bot-${p.id}`}
                  style={[styles.rowBtn, styles.removeBotBtn]}
                  onPress={() => handleRemoveBot(p.id)}
                >
                  <Text style={[styles.rowBtnText, styles.removeBotBtnText]}>移除</Text>
                </TouchableOpacity>
              )}
            </View>
          </View>
        ))}

        {/* Actions */}
        <View style={styles.actions}>
          {me && !isHost && (
            isReady ? (
              <TouchableOpacity style={[styles.actionBtn, styles.unreadyBtn]} onPress={handleUnready}>
                <Text style={styles.unreadyBtnText}>取消准备</Text>
              </TouchableOpacity>
            ) : (
              <TouchableOpacity style={[styles.actionBtn, styles.readyBtn]} onPress={handleReady}>
                <Text style={styles.readyBtnText}>准 备</Text>
              </TouchableOpacity>
            )
          )}
          {isHost && (
            <TouchableOpacity
              style={[styles.actionBtn, styles.startBtn, !canStart && styles.disabledBtn]}
              onPress={handleStart}
              disabled={!canStart}
            >
              <Text style={styles.startBtnText}>
                {needMorePlayers ? `等待玩家 (${room.players.length}/${room.max_players})` : !allReady ? '等待准备' : '开始游戏'}
              </Text>
            </TouchableOpacity>
          )}
          {/* 海战棋人机的难度选择：加人机前先选档 */}
          {canAddBot && isBattleship && (
            <View style={styles.difficultyRow}>
              <Text style={styles.difficultyLabel}>人机难度</Text>
              <View style={styles.difficultyChips}>
                {BOT_DIFFICULTIES.map((d) => (
                  <TouchableOpacity
                    key={d.key}
                    testID={`bot-difficulty-${d.key}`}
                    style={[
                      styles.difficultyChip,
                      botDifficulty === d.key && styles.difficultyChipActive,
                    ]}
                    onPress={() => setBotDifficulty(d.key)}
                  >
                    <Text style={[
                      styles.difficultyChipText,
                      botDifficulty === d.key && styles.difficultyChipTextActive,
                    ]}>{d.label}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </View>
          )}
          {canAddBot && (
            <TouchableOpacity style={[styles.actionBtn, styles.addBotBtn]} onPress={handleAddBot}>
              <Text style={styles.addBotBtnText}>添加人机</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity style={[styles.actionBtn, styles.leaveBtn]} onPress={handleLeave}>
            <Text style={styles.leaveBtnText}>退出房间</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
      <RoomSettingsSheet
        visible={settingsOpen}
        items={settings}
        editable={isHost && isLobbyStatus}
        onClose={() => setSettingsOpen(false)}
        onPick={(key, value) => socketRef.current?.roomSetting(key, value)}
      />
      {/* 换位请求（v1.16）：别人要和我换座位，我点头才真的换。
          接 `useModalBackGuard`（在组件顶部）—— 返回键只关这个弹窗。 */}
      <Modal visible={swapFrom !== null} transparent animationType="fade">
        <View style={styles.swapBackdrop}>
          <View style={styles.swapCard} testID="swap-request-modal">
            <Text style={styles.swapTitle}>换位请求</Text>
            <Text style={styles.swapBody}>
              「{swapFrom?.name}」想和你交换位置。
            </Text>
            <Text style={styles.swapHint}>
              座位决定谁先手 / 谁先出牌，同意之后你俩的位置会对调。
            </Text>
            <View style={styles.swapRow}>
              <TouchableOpacity
                testID="swap-reject"
                style={[styles.swapAction, styles.swapReject]}
                onPress={() => answerSwap(false)}
              >
                <Text style={styles.swapRejectText}>拒绝</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="swap-accept"
                style={[styles.swapAction, styles.swapAccept]}
                onPress={() => answerSwap(true)}
              >
                <Text style={styles.swapAcceptText}>同意换位</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  errorText: { fontSize: fontSize.lg, color: colors.danger, marginBottom: spacing.md },
  backBtn: { paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, backgroundColor: colors.primary, borderRadius: radius.md },
  backBtnText: { color: colors.white, fontWeight: '700' },
  container: { padding: spacing.md, paddingBottom: spacing.xxl },
  codeBox: {
    backgroundColor: colors.primary,
    borderRadius: radius.lg,
    padding: spacing.lg,
    alignItems: 'center',
    marginBottom: spacing.md,
  },
  codeLabel: { color: colors.white, fontSize: fontSize.sm, opacity: 0.8 },
  codeValue: {
    color: colors.white,
    fontSize: fontSize.title,
    fontWeight: '800',
    letterSpacing: 4,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    marginVertical: spacing.xs,
  },
  copyHintRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  copyHint: { color: colors.white, fontSize: fontSize.xs, opacity: 0.7 },
  finishedBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.card,
    borderRadius: radius.md,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    marginBottom: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  finishedBannerText: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    fontWeight: '500',
  },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  infoLabel: { color: colors.textSecondary, fontSize: fontSize.md },
  infoValue: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  infoValueRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  sectionTitle: {
    fontSize: fontSize.lg,
    fontWeight: '700',
    color: colors.text,
    marginTop: spacing.lg,
    marginBottom: spacing.sm,
  },
  playerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: colors.card,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  playerInfo: { flexDirection: 'row', alignItems: 'center', flex: 1 },
  playerName: { fontSize: fontSize.md, color: colors.text, fontWeight: '600' },
  playerDisconnected: { textDecorationLine: 'line-through', color: colors.textMuted },
  hostBadge: {
    marginLeft: spacing.sm,
    backgroundColor: colors.warning,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    borderRadius: radius.full,
    fontSize: fontSize.xs,
    color: colors.white,
    fontWeight: '600',
    overflow: 'hidden',
  },
  botBadge: {
    marginLeft: spacing.sm,
    backgroundColor: colors.textSecondary,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    borderRadius: radius.full,
    fontSize: fontSize.xs,
    color: colors.white,
    fontWeight: '600',
    overflow: 'hidden',
  },
  difficultyRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
  },
  difficultyLabel: { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  difficultyChips: { flexDirection: 'row', gap: spacing.xs },
  difficultyChip: {
    minWidth: 56, minHeight: 36, paddingHorizontal: spacing.sm,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.borderStrong,
    backgroundColor: colors.card, alignItems: 'center', justifyContent: 'center',
  },
  difficultyChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  difficultyChipText: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  difficultyChipTextActive: { color: colors.white },
  // 行内动作按钮（换位 / 移除）：**同一颗按钮的两种语义** —— 同高、同圆角、同边框
  // 粗细、同内边距，只有颜色与文字不同。它们并排出现在同一个玩家行里，形状一致才
  // 读得出是一个按钮组（旧版是「一颗 44pt 的空框 + 一颗 17pt 的红药丸」，两码事）。
  // **44pt 高**（HIG 主控件下限）：这是等待大厅里最常用的两条动作，不跟着状态胶囊缩。
  rowBtn: {
    minHeight: 44,
    minWidth: 60,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.sm + 2,
    borderRadius: radius.md,
    borderWidth: 1,
    backgroundColor: colors.bg,
  },
  rowBtnText: { fontSize: fontSize.sm, fontWeight: '600' },
  swapSeatBtn: { borderColor: colors.primary },
  swapSeatBtnText: { color: colors.primary },
  // 「等待同意」是**已发出、还没结果**的待答态：降一档成中性色，不再和可点的
  // 「换位」抢眼（仍然可点 —— 点了就是再发一次请求，不是死按钮）。
  pendingBtn: { borderColor: colors.borderStrong, backgroundColor: colors.card },
  pendingBtnText: { color: colors.textMuted },
  swapBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  swapCard: {
    width: '100%',
    maxWidth: 340,
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.lg,
  },
  swapTitle: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm },
  swapBody: { fontSize: fontSize.md, color: colors.text, marginBottom: spacing.xs },
  swapHint: { fontSize: fontSize.sm, color: colors.textMuted, marginBottom: spacing.md },
  swapRow: { flexDirection: 'row', gap: spacing.sm },
  swapAction: {
    flex: 1,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radius.md,
  },
  swapReject: { borderWidth: 1, borderColor: colors.borderStrong, backgroundColor: colors.bg },
  swapRejectText: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  swapAccept: { backgroundColor: colors.primary },
  swapAcceptText: { color: colors.white, fontSize: fontSize.md, fontWeight: '600' },
  removeBotBtn: { borderColor: colors.danger },
  removeBotBtnText: { color: colors.danger },
  addBotBtn: {
    backgroundColor: colors.bg,
    borderWidth: 1,
    borderColor: colors.primary,
  },
  addBotBtnText: {
    color: colors.primary,
    fontSize: fontSize.md,
    fontWeight: '600',
  },
  // 玩家行右侧那一簇（状态胶囊 + 两颗动作按钮）。
  // **`alignItems: 'center'` 是必须的**：默认的 `stretch` 会把状态胶囊拉满整行高度，
  // 于是「已准备」被撑成一枚 52×44 的绿色大圆饼（截图实测），而旁边的按钮只有 44 高
  // —— 三颗控件三种尺寸，正是这一行难看的根源。
  playerBadges: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  // 状态胶囊：**标签比按钮小一档**（22 高 vs 44 高）是常见的层级表达，所以它只居中、
  // 不跟着按钮一起长高。`lineHeight` 写死是为了让高度不随平台默认行高漂移。
  statusPill: {
    paddingHorizontal: spacing.sm + 2,
    paddingVertical: 3,
    borderRadius: radius.full,
    fontSize: fontSize.xs,
    fontWeight: '600',
    lineHeight: 16,
    overflow: 'hidden',
    color: colors.white,
  },
  readyPill: { backgroundColor: colors.success },
  unreadyPill: { backgroundColor: colors.textMuted },
  hostPill: { backgroundColor: colors.warning },
  disconnectedPill: { backgroundColor: colors.danger },
  actions: { marginTop: spacing.lg, gap: spacing.sm },
  actionBtn: {
    borderRadius: radius.md,
    paddingVertical: spacing.sm + 4,
    alignItems: 'center',
  },
  readyBtn: { backgroundColor: colors.success },
  readyBtnText: { color: colors.white, fontSize: fontSize.lg, fontWeight: '700' },
  unreadyBtn: { backgroundColor: colors.warning },
  unreadyBtnText: { color: colors.white, fontSize: fontSize.lg, fontWeight: '700' },
  startBtn: { backgroundColor: colors.primary },
  startBtnText: { color: colors.white, fontSize: fontSize.lg, fontWeight: '700' },
  disabledBtn: { opacity: 0.5 },
  leaveBtn: { backgroundColor: colors.bg, borderWidth: 1, borderColor: colors.danger },
  leaveBtnText: { color: colors.danger, fontSize: fontSize.md, fontWeight: '600' },
});

