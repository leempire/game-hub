import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, ScrollView, Vibration,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, fetchMyStats, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import { colors, spacing, radius, fontSize } from '../theme';
import { notify } from '../utils';
import { pushToast } from '../toast';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';
import { settingValueLabel } from '../roomSettings';
import {
  MineGameState, MineRankingEntry, RevealedCell, NUMBER_COLORS,
  formatMineElapsed,
} from '../minesweeper';

// 旗子压在覆盖格 #CBD5E1 上，需 ≥3:1（原 #EF4444 只有 2.53）
const MINE_FLAG = '#B91C1C';
// 踩中的雷格：上面压着白色警示图标，底色需让白字 ≥4.5:1（原 #EF4444 只有 3.76）
const MINE_CELL = '#B91C1C';

interface Props {
  code: string;
  onLeave: () => void;
}

// Internal grid state: each cell tracks revealed value and flag status
interface CellState {
  value: number | null;   // null = unrevealed, -1 = mine, 0-8 = count
  flagged: boolean;
  /** 问号笔记（v1.18.1）：不参与 chord / 通关判定，翻开时自动清掉 */
  questioned?: boolean;
}

/** 问号笔记的颜色：压在未翻开格上要够清楚，与旗子的红刻意区分开。 */
const MINE_QUESTION = '#475569';

export default function MineScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth, height: winHeight } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<MineGameState | null>(null);
  const [grid, setGrid] = useState<CellState[][] | null>(null);
  /** 各难度历史最佳用时（毫秒）；从战绩聚合接口拿，进屏时读取一次 */
  const [bestTimes, setBestTimes] = useState<Record<string, number>>({});
  const [showGameOver, setShowGameOver] = useState(false);
  const [hasAnyDisconnect, setHasAnyDisconnect] = useState(false);
  // 断线倒计时（多人局：取所有断线玩家里**最紧**的那个剩余时间）
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const dcLefts = (room?.players ?? [])
    .filter((p) => p.id !== user?.id && !p.connected)
    .map((p) => p.reconnect_left)
    .filter((v): v is number => typeof v === 'number');
  const dcLeft = useDisconnectCountdown(
    hasAnyDisconnect,
    dcTimeout ?? (dcLefts.length ? Math.min(...dcLefts) : null),
  );
  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);

  useEffect(() => {
    if (!token) return;
    fetchMyStats(token)
      .then((s) => setBestTimes(s.best_times || {}))
      .catch(() => {});
  }, [token]);
  // Track whether we're in flag mode (tap to flag instead of reveal)
  const [flagMode, setFlagMode] = useState(false);
  // Player stats bar: collapsed by default (only my chip), expandable to all
  // 默认**展开**：竞技淘汰制里「谁还活着、谁挖了多少」是主要信息，默认藏起来
  // 就失去了意义。`playerRow` 有 flexWrap，2 人一行、4 人自动折成两行（这正是
  // 原来「4 人时横向挤」想要的两行布局）；嫌占地方再点右侧 chevron 收起成一行。
  const [playersExpanded, setPlayersExpanded] = useState(true);
  // Exit / surrender confirmation modals
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [confirmSurrender, setConfirmSurrender] = useState(false);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  // Use ref for room to avoid stale closures in WS handlers
  const roomRef = useRef<Room | null>(null);
  roomRef.current = room;
  // Ref for my seat to avoid stale closures
  const mySeatRef = useRef<number>(-1);
  // 长按插旗后置位，消费随后的 onPress（否则一次长按会「插旗 + 翻开」双动作）
  const longPressConsumedRef = useRef(false);

  // Derive game-over flag for back-guard (must be set before useModalBackGuard)
  const isFinished = game?.finished ?? false;
  gameOverRef.current = isFinished;
  useModalBackGuard(confirmLeave, () => setConfirmLeave(false));
  useModalBackGuard(confirmSurrender, () => setConfirmSurrender(false));
  useModalBackGuard(showGameOver, () => {});

  // System / browser back: confirm if game in progress
  useEffect(() => navigation.addListener('beforeRemove', (e: any) => {
    if (Platform.OS === 'web') return;
    if (skipRemoveRef.current) return;
    if (gameOverRef.current) {
      skipRemoveRef.current = true;
      navigation.dispatch(e.data.action);
      return;
    }
    e.preventDefault();
    setConfirmLeave(true);
  }), [navigation]);

  // Web browser back: backGuard dispatches gh:confirm-leave (no-op on native)
  useEffect(() => onConfirmLeave(() => {
    if (gameOverRef.current) {
      navigation.goBack();
      return;
    }
    setConfirmLeave(true);
  }), [navigation]);

  const [elapsed, setElapsed] = useState(0);

  // Derive grid dimensions from game state
  const width = game?.width ?? 9;
  const height = game?.height ?? 9;
  const totalMines = game?.mines ?? 10;

  // 格子尺寸：三个难度**统一 44px**（Apple HIG 44pt / WCAG 2.5.5 AAA）。
  //
  // 原来是「按屏宽缩放、下限 22px」：初级 39px、中级和高级都被压到 22px ——
  // 22px 连 WCAG AA 的 24px 底线都不到，手指点不准，这是用户报的「容易误触」
  // 的根源。现在不再为了塞进屏幕而缩格子，棋盘放不下就**双向滑动**看全
  // （初级 9 列 = 396px，390 屏上只是轻微横滑；中级 704²、高级 1320×640）。
  const PAGE_H_PADDING = 24;
  const CELL_SIZE = 44;
  const cellSize = CELL_SIZE;

  const boardWidth = cellSize * width;
  const boardHeight = cellSize * height;
  // 棋盘是否真的超出可视区（决定要不要显示滑动提示）
  const overflowX = boardWidth > winWidth - PAGE_H_PADDING;
  const overflowY = boardHeight > winHeight - 260;

  // 顶栏那颗难度胶囊的文字。**由服务端下发**（房间级设置的 `difficulty` 项）——
  // 客户端不再自己抄一份「初级/中级/高级」的映射表（原先这里与 RoomsTab 各有一份）。
  const difficultyLabel = settingValueLabel(room, 'difficulty');

  // My seat index (position in room.players array)
  const mySeat = useMemo(() => {
    if (!room || !user) return -1;
    return room.players.findIndex((p) => p.id === user.id);
  }, [room, user]);
  mySeatRef.current = mySeat;

  // Am I eliminated?
  const isEliminated = useMemo(() => {
    if (!game || mySeat < 0) return false;
    return game.players[String(mySeat)]?.alive === false;
  }, [game, mySeat]);

  // Flag count computed from grid
  const flagCount = useMemo(() => {
    if (!grid) return 0;
    let count = 0;
    for (const row of grid) {
      for (const cell of row) {
        if (cell.flagged) count++;
      }
    }
    return count;
  }, [grid]);

  // My revealed count from game.players
  const myRevealedCount = useMemo(() => {
    if (!game || mySeat < 0) return 0;
    return game.players[String(mySeat)]?.revealed_count ?? 0;
  }, [game, mySeat]);

  // Build or rebuild the grid from game state
  const buildGrid = useCallback((g: MineGameState): CellState[][] => {
    const g2: CellState[][] = [];
    for (let y = 0; y < g.height; y++) {
      const row: CellState[] = [];
      for (let x = 0; x < g.width; x++) {
        row.push({ value: null, flagged: false });
      }
      g2.push(row);
    }
    // Apply revealed cells
    for (const [x, y, v] of g.revealed) {
      if (g2[y] && g2[y][x] !== undefined) {
        g2[y][x] = { value: v, flagged: false };
      }
    }
    // Apply flagged cells
    for (const [x, y] of g.flagged) {
      if (g2[y] && g2[y][x] !== undefined && g2[y][x].value === null) {
        g2[y][x] = { ...g2[y][x], flagged: true };
      }
    }
    // Apply question marks（老对局态没有这个字段）
    for (const [x, y] of g.questioned || []) {
      if (g2[y] && g2[y][x] !== undefined && g2[y][x].value === null) {
        g2[y][x] = { ...g2[y][x], questioned: true };
      }
    }
    return g2;
  }, []);

  // Reset transient UI state on game start / sync
  const resetUI = useCallback(() => {
    gameOverShownRef.current = false;
    setShowGameOver(false);
    setFlagMode(false);
    setElapsed(0);
  }, []);

  // Fetch room and connect WS
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;

    getRoom(token, code).then(setRoom).catch(() => {});

    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        roomRef.current = roomData;
      }
      if (msg.game) {
        const g = msg.game as MineGameState;
        setGame(g);
        setGrid(buildGrid(g));
        resetUI();
        if (g.finished) {
          gameOverShownRef.current = true;
          setShowGameOver(true);
        }
      }
      if (roomData) {
        const anyDc = roomData.players.some(
          (p) => p.id !== user?.id && !p.connected
        );
        setHasAnyDisconnect(anyDc);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        roomRef.current = roomData;
        const anyDc = roomData.players.some(
          (p) => p.id !== user?.id && !p.connected
        );
        setHasAnyDisconnect(anyDc);
      }
    };

    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as MineGameState;
      if (g) {
        setGame(g);
        setGrid(buildGrid(g));
        resetUI();
      }
    };

    const handleRevealed = (msg: SocketMessage) => {
      const cells = msg.cells as RevealedCell[];
      const seat = msg.seat as number;
      const revealedCount = msg.revealed_count as number;
      const eliminated = msg.eliminated as boolean;
      const hitMine = msg.hit_mine as boolean;
      const lostLife = (msg.lost_life as boolean) ?? false;
      const livesLeft = msg.lives_left as number | undefined;
      setGrid((prev) => {
        if (!prev) return prev;
        const next = prev.map((row) => row.map((c) => ({ ...c })));
        for (const [x, y, v] of cells) {
          if (next[y] && next[y][x]) {
            next[y][x] = { value: v, flagged: false, questioned: false };
          }
        }
        return next;
      });
      setGame((prev) => {
        if (!prev) return prev;
        const players = { ...prev.players };
        const before = players[String(seat)];
        players[String(seat)] = {
          alive: !eliminated,
          // 服务端逐条下发剩余命数；老消息没带就按「扣一条 / 清零」兜底，
          // 不能让命数显示停在旧值上
          lives_left: livesLeft ?? (eliminated ? 0
            : lostLife ? Math.max(0, (before?.lives_left ?? 1) - 1)
              : (before?.lives_left ?? prev.lives ?? 1)),
          revealed_count: revealedCount,
        };
        return { ...prev, players, started: true };
      });
      // Vibration: mine hit double-pulse, normal reveal light pulse
      if (Platform.OS !== 'web') {
        Vibration.vibrate(hitMine ? [0, 40, 60, 40] : 10);
      }
      // Extra vibration if this is me getting eliminated
      if (eliminated && seat === mySeatRef.current && Platform.OS !== 'web') {
        Vibration.vibrate([0, 60, 80, 60]);
      }
    };

    const handleFlagged = (msg: SocketMessage) => {
      const x = msg.x as number;
      const y = msg.y as number;
      const isFlagged = msg.flagged as boolean;
      // 三态：flag / question / none。旧服务端只给 flagged，按两态兜底。
      const state = (msg.state as string) || (isFlagged ? 'flag' : 'none');
      setGrid((prev) => {
        if (!prev) return prev;
        const next = prev.map((row) => row.map((c) => ({ ...c })));
        if (next[y] && next[y][x]) {
          next[y][x] = {
            ...next[y][x],
            flagged: state === 'flag',
            questioned: state === 'question',
          };
        }
        return next;
      });
    };

    const handlePlayerEliminated = (msg: SocketMessage) => {
      const message = msg.message as string;
      pushToast(message);
    };

    // 踩雷但命没扣完：只扣命、不淘汰，单独一条提示（否则玩家只看到棋盘闪一下）
    const handleLifeLost = (msg: SocketMessage) => {
      pushToast(msg.message as string);
    };

    const handleGameOver = (msg: SocketMessage) => {
      const reason = msg.reason as 'cleared' | 'eliminated';
      const ranking = msg.ranking as MineRankingEntry[];
      setGame((prev) =>
        prev ? { ...prev, finished: true, reason, ranking } : prev
      );
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setShowGameOver(true);
      }
      if (token) getRoom(token, code).then(setRoom).catch(() => {});
    };

    const handlePlayerDisconnected = (msg: SocketMessage) => {
      const uid = msg.user_id as number;
      if (uid !== user?.id) {
        setHasAnyDisconnect(true);
        setDcTimeout((msg.timeout as number) ?? null);
      }
    };

    const handlePlayerReconnected = (msg: SocketMessage) => {
      const uid = msg.user_id as number;
      if (uid !== user?.id) {
        if (token) {
          getRoom(token, code).then((r) => {
            setRoom(r);
            roomRef.current = r;
            const anyDc = r.players.some(
              (p) => p.id !== user?.id && !p.connected
            );
            setHasAnyDisconnect(anyDc);
          }).catch(() => {});
        }
      }
    };

    const handleRoomClosed = () => {
      notify('房间已解散');
      ws.close();
      onLeave();
    };

    const handleError = (msg: SocketMessage) => {
      notify('提示', msg.message as string);
    };

    ws.on('sync', handleSync);
    ws.on('lobby', handleLobby);
    ws.on('game_start', handleGameStart);
    ws.on('revealed', handleRevealed);
    ws.on('flagged', handleFlagged);
    ws.on('player_eliminated', handlePlayerEliminated);
    ws.on('life_lost', handleLifeLost);
    ws.on('game_over', handleGameOver);
    ws.on('player_disconnected', handlePlayerDisconnected);
    ws.on('player_reconnected', handlePlayerReconnected);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);

    ws.connect();

    return () => {
      ws.close();
    };
  }, [token, code, user?.id, onLeave, buildGrid, resetUI]);

  // Cell press: reveal / chord / flag depending on cell state and mode
  const handleCellPress = useCallback((x: number, y: number) => {
    // 长按已消费本次触摸：跳过随后的 click，避免「插旗 + 翻开」双动作
    if (longPressConsumedRef.current) {
      longPressConsumedRef.current = false;
      return;
    }
    if (!game || game.finished) return;
    if (isEliminated) return;
    if (!grid || !grid[y] || !grid[y][x]) return;
    const cell = grid[y][x];
    // 已翻开的数字格：翻开模式下触发 chord（周围旗数与数字相符时一次揭开其余邻格）。
    // 条件不满足由服务端静默忽略，这里不做前端判定，保持单一裁决源。
    if (cell.value !== null) {
      if (!flagMode && cell.value > 0) {
        socketRef.current?.send({ type: 'chord', x, y });
      }
      return;
    }
    if (flagMode) {
      // 插旗模式：空 → 旗 → 问号 → 空（服务端裁决，客户端只发动作）
      socketRef.current?.send({ type: 'flag', x, y });
      return;
    }
    // 翻开模式下，插旗格点击无效（防误触）；问号格可以直接翻开（问号只是笔记）
    if (cell.flagged) return;
    socketRef.current?.send({ type: 'reveal', x, y });
  }, [game, grid, flagMode, isEliminated]);

  // Long press (or web right-click): cycle 空 → 旗 → 问号 → 空.
  // v1.18.1 起不再是「只能插旗」：三态轮换是桌面扫雷的通行做法，也省掉「切模式再点」
  // 才能拔旗的一步。长按后消费随后的 click，避免一次长按触发两个动作。
  const handleCellLongPress = useCallback((x: number, y: number) => {
    if (!game || game.finished) return;
    if (isEliminated) return;
    if (!grid || !grid[y] || !grid[y][x]) return;
    const cell = grid[y][x];
    if (cell.value !== null) return;
    longPressConsumedRef.current = true;
    socketRef.current?.send({ type: 'flag', x, y });
    if (Platform.OS !== 'web') Vibration.vibrate(10);
  }, [game, grid, isEliminated]);

  // Web right-click handler for flagging
  const handleContextMenu = useCallback((e: any, x: number, y: number) => {
    if (Platform.OS !== 'web') return;
    if (e && e.preventDefault) e.preventDefault();
    handleCellLongPress(x, y);
  }, [handleCellLongPress]);

  const handleLeaveRoom = async () => {
    if (!token) return;
    try {
      await leaveRoom(token, code);
    } catch {
      // continue even if REST fails
    }
    socketRef.current?.leave();
    socketRef.current?.close();
    skipRemoveRef.current = true;
    onLeave();
  };

  const handleSurrender = () => {
    setConfirmSurrender(false);
    socketRef.current?.surrender();
  };

  // 终局返回：关闭 socket 后直接回房间。Android 返回键（Modal 的
  // onRequestClose）与「返回房间」按钮走同一条路径。
  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.goBack();
  }, [navigation]);

  // Player status chips: one per player showing alive/eliminated + revealed count.
  // 4 人局横排放不下，默认收起（只显示自己），点右侧 chevron 展开全部。
  const renderPlayerChip = (idx: number) => {
    if (!room || !game) return null;
    const p = room.players[idx];
    if (!p) return null;
    const seat = String(idx);
    const ps = game.players[seat];
    const alive = ps?.alive !== false;
    const isMe = p.id === user?.id;
    const name = p.nickname || p.username;
    const totalLives = game.lives ?? 1;
    const livesLeft = alive ? Math.max(0, ps?.lives_left ?? totalLives) : 0;
    return (
      <View
        key={`pc-${seat}`}
        style={[
          styles.playerChip,
          isMe && styles.playerChipMe,
          !alive && styles.playerChipDead,
        ]}
      >
        <View
          style={[
            styles.statusDot,
            { backgroundColor: alive ? colors.success : colors.danger },
          ]}
        />
        <Text
          style={[
            styles.playerName,
            !alive && styles.playerNameDead,
          ]}
          numberOfLines={1}
        >
          {name}
        </Text>
        {/* 命数：心形图标 + 数字，颜色之外还有形状与数字两条通道 */}
        {!alive ? (
          <Text style={styles.deadTag}>已淘汰</Text>
        ) : (
          <View
            style={styles.livesTag}
            testID={`mine-lives-${seat}`}
            accessibilityLabel={`${name} 剩余 ${livesLeft} 条命`}
          >
            <Ionicons
              name={livesLeft > 1 ? 'heart' : 'heart-outline'}
              size={12}
              color={livesLeft > 1 ? colors.danger : colors.warning}
            />
            <Text
              style={[
                styles.livesText,
                livesLeft > 1 ? styles.livesTextFull : styles.livesTextLow,
              ]}
            >
              {livesLeft}/{totalLives}
            </Text>
          </View>
        )}
        <Text style={styles.playerStat}>{ps?.revealed_count ?? 0} 格</Text>
      </View>
    );
  };

  const renderPlayerChips = () => {
    if (!room || !game) return null;
    const total = room.players.length;
    const showToggle = total > 1;
    const visibleIdx = playersExpanded
      ? room.players.map((_, i) => i)
      : [mySeat >= 0 ? mySeat : 0];
    return (
      <View style={styles.playerBar}>
        <View style={styles.playerRow}>
          {visibleIdx.map((i) => renderPlayerChip(i))}
          {!playersExpanded && showToggle && (
            <Text style={styles.playerMore}>共 {total} 人</Text>
          )}
        </View>
        {showToggle && (
          <TouchableOpacity
            testID="mine-players-toggle"
            style={styles.playerToggle}
            onPress={() => setPlayersExpanded((v) => !v)}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            accessibilityRole="button"
            accessibilityLabel={playersExpanded ? '收起玩家列表' : '展开玩家列表'}
          >
            <Ionicons
              name={playersExpanded ? 'chevron-up' : 'chevron-down'}
              size={18}
              color={colors.textSecondary}
            />
          </TouchableOpacity>
        )}
      </View>
    );
  };

  // Compute cell content node (flag, mine icon, or number)
  const renderCellContent = (cell: CellState, isRevealed: boolean) => {
    const isMine = cell.value === -1;
    const numVal = cell.value !== null && cell.value > 0 ? cell.value : 0;
    if (cell.flagged && !isRevealed) {
      return <Ionicons name="flag" size={cellSize * 0.55} color={MINE_FLAG} />;
    }
    if (cell.questioned && !isRevealed) {
      // 问号是「还不确定」的笔记：形状与颜色都和旗子分开（WCAG 1.4.1）
      return (
        <Text style={[styles.cellQuestion, { fontSize: cellSize * 0.6 }]}>?</Text>
      );
    }
    if (isRevealed && isMine) {
      return <Ionicons name="warning" size={cellSize * 0.55} color="#FFF" />;
    }
    if (isRevealed && numVal > 0) {
      return (
        <Text
          style={[
            styles.cellNumber,
            { fontSize: cellSize * 0.55, color: NUMBER_COLORS[numVal] || colors.text },
          ]}
        >
          {numVal}
        </Text>
      );
    }
    return null;
  };

  // Render a single cell with press handlers
  const renderCell = (x: number, y: number) => {
    if (!grid || !grid[y]) return null;
    const cell = grid[y][x];
    const isRevealed = cell.value !== null;
    const isMine = cell.value === -1;
    const bgColor = isRevealed
      ? (isMine ? MINE_CELL : '#EDEFF2')
      : colors.card;

    return (
      <TouchableOpacity
        key={`c-${x}-${y}`}
        testID={`mine-cell-${x}-${y}`}
        style={[
          styles.cell,
          {
            left: cellSize * x,
            top: cellSize * y,
            width: cellSize,
            height: cellSize,
            backgroundColor: bgColor,
            borderRadius: 2,
          },
          !isRevealed && styles.cellUnrevealed,
        ]}
        // 已翻开的数字格要保持可点（chord），所以只按淘汰禁用
        disabled={isEliminated}
        onPressIn={() => { longPressConsumedRef.current = false; }}
        onPress={() => handleCellPress(x, y)}
        onLongPress={() => handleCellLongPress(x, y)}
        delayLongPress={400}
        // @ts-ignore Web-specific prop
        onContextMenu={Platform.OS === 'web'
          ? (e: any) => handleContextMenu(e, x, y)
          : undefined}
        activeOpacity={isRevealed ? 1 : 0.7}
      >
        {renderCellContent(cell, isRevealed)}
      </TouchableOpacity>
    );
  };

  // 棋盘：三个难度统一「外层横向 + 内层纵向」的双向滑动。
  //
  // 格子放大到 44px 后，中级(704²)和高级(1320×640)都远超屏幕，必须两个方向都能滑；
  // 初级在窄屏上也轻微超出。棋盘比可视区小时居中显示（平板上不会贴在左上角）。
  const renderBoard = () => {
    const boardContent = (
      <View testID="mine-board" style={[styles.boardWrap, { width: boardWidth, height: boardHeight }]}>
        {grid && Array.from({ length: height }).map((_, y) =>
          Array.from({ length: width }).map((_, x) => renderCell(x, y))
        )}
      </View>
    );

    return (
      <View style={styles.advancedOuter}>
        {(overflowX || overflowY) && (
          <Text style={styles.scrollHint}>
            {overflowX && overflowY ? '棋盘较大，可左右 / 上下滑动'
              : overflowX ? '棋盘较宽，可左右滑动'
                : '棋盘较高，可上下滑动'}
          </Text>
        )}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={overflowX}
          style={styles.boardHScroll}
          contentContainerStyle={styles.boardHScrollContent}
        >
          <ScrollView
            showsVerticalScrollIndicator={overflowY}
            contentContainerStyle={styles.boardVScrollContent}
          >
            {boardContent}
          </ScrollView>
        </ScrollView>
      </View>
    );
  };

  // Game state flags
  const gameStarted = game?.started ?? false;
  const safeTotal = width * height - totalMines;

  // Reason labels for game-over modal
  const reasonLabel = game?.reason === 'cleared'
    ? '所有安全格已翻开'
    : game?.reason === 'eliminated' ? '全员淘汰' : '';

  // 个人最佳用时（按难度）：只用于展示与「新纪录」提示，裁决仍在服务端
  const bestTime = game?.difficulty ? bestTimes[game.difficulty] : undefined;
  const myElapsedMs = game?.ranking?.find((r) => r.username === user?.username)?.elapsed_ms ?? null;

  return (
    <View style={styles.container}>
      {/* Top info bar */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          <View style={styles.statItem}>
            <Ionicons name="flag" size={14} color={MINE_FLAG} />
            <Text style={styles.statText}>{Math.max(totalMines - flagCount, 0)}</Text>
          </View>
          <View style={styles.statItem}>
            <Ionicons name="time-outline" size={14} color={colors.textSecondary} />
            <Text style={styles.statText}>{Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}</Text>
          </View>
          {bestTime != null && (
            <View style={styles.statItem}>
              <Ionicons name="trophy-outline" size={14} color={colors.warning} />
              <Text style={styles.statText}>{formatMineElapsed(bestTime)}</Text>
            </View>
          )}
        </View>
        <View style={styles.topRight}>
          <GameHelpButton game="minesweeper" size={24} />
          {difficultyLabel ? (
            // 难度名由服务端下发（`room.settings`）—— 客户端不再自己抄一份档位表
            <Text style={styles.diffChip}>
              {difficultyLabel}{game?.daily ? ' · 今日盘' : ''}
            </Text>
          ) : null}
          <Text style={styles.roomCode}>#{code}</Text>
        </View>
      </View>

      {/* Player status bar */}
      {renderPlayerChips()}

      {/* Spectator banner when I am eliminated */}
      {isEliminated && !isFinished && (
        <View style={styles.elimBanner}>
          <Text style={styles.elimBannerText}>你已被淘汰，观战中</Text>
        </View>
      )}

      {/* Disconnect banner */}
      {hasAnyDisconnect && (
        <View style={styles.banner}>
          <View style={styles.bannerRow}>
            <Ionicons name="warning" size={14} color={colors.white} />
            <Text style={styles.bannerText}> 有玩家断线，等待重连（{dcLeft}秒）</Text>
          </View>
        </View>
      )}

      {/* Flag mode toggle */}
      <View style={styles.modeBar}>
        <TouchableOpacity
          style={[styles.modeBtn, flagMode && styles.modeBtnActive]}
          onPress={() => setFlagMode(!flagMode)}
        >
          <Ionicons
            name="flag"
            size={16}
            color={flagMode ? colors.white : colors.textSecondary}
          />
          <Text style={[styles.modeBtnText, flagMode && styles.modeBtnTextActive]}>
            {flagMode ? '插旗模式' : '翻开模式'}
          </Text>
        </TouchableOpacity>
        {!gameStarted && (
          <Text style={styles.hintText}>等待游戏开始...</Text>
        )}
        {gameStarted && !isFinished && !isEliminated && myRevealedCount === 0 && (
          <Text style={styles.hintText}>点击任意格开始，首翻必定安全</Text>
        )}
        {gameStarted && !isFinished && !isEliminated && myRevealedCount > 0 && (
          <Text style={styles.hintText}>
            长按{Platform.OS === 'web' ? '/右键' : ''}插旗 · 点数字格快开
          </Text>
        )}
      </View>

      {/* Board */}
      <View style={styles.boardContainer}>
        {renderBoard()}
      </View>

      {/* Bottom actions */}
      <View style={styles.bottomBar}>
        <TouchableOpacity style={styles.leaveBtn} onPress={() => setConfirmLeave(true)}>
          <Text style={styles.leaveBtnText}>退出</Text>
        </TouchableOpacity>
        {gameStarted && !isFinished && !isEliminated && (
          <TouchableOpacity
            style={styles.surrenderBtn}
            onPress={() => setConfirmSurrender(true)}
          >
            <Text style={styles.surrenderBtnText}>放弃本局</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Leave confirmation modal */}
      <Modal visible={confirmLeave} transparent animationType="fade" onRequestClose={() => setConfirmLeave(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Ionicons name="exit-outline" size={36} color={colors.danger} />
            <Text style={styles.modalTitle}>确认退出？</Text>
            <Text style={styles.modalReason}>
              {gameStarted && !isFinished
                ? '对局进行中，退出后其他玩家将继续本局'
                : '退出将离开当前房间'}
            </Text>
            <TouchableOpacity
              style={[styles.modalBtn, { backgroundColor: colors.danger }]}
              onPress={() => { setConfirmLeave(false); handleLeaveRoom(); }}
            >
              <Text style={styles.modalBtnPrimaryText}>确认退出</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setConfirmLeave(false)}
            >
              <Text style={styles.modalBtnSecondaryText}>再想想</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Surrender confirmation modal */}
      <Modal visible={confirmSurrender} transparent animationType="fade" onRequestClose={() => setConfirmSurrender(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Ionicons name="flag-outline" size={36} color={colors.warning} />
            <Text style={styles.modalTitle}>放弃本局？</Text>
            <Text style={styles.modalReason}>
              认输后将被淘汰，可以观战到本局结束
            </Text>
            <TouchableOpacity
              style={[styles.modalBtn, { backgroundColor: colors.warning }]}
              onPress={handleSurrender}
            >
              <Text style={styles.modalBtnPrimaryText}>确认放弃</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setConfirmSurrender(false)}
            >
              <Text style={styles.modalBtnSecondaryText}>继续游戏</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Game over / ranking modal */}
      <Modal visible={showGameOver} transparent animationType="fade" onRequestClose={handleBackToRoom}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>游戏结束</Text>
            {game && (
              <>
                <Ionicons
                  name={game.reason === 'cleared' ? 'trophy' : 'close-circle'}
                  size={36}
                  color={game.reason === 'cleared' ? colors.warning : colors.danger}
                />
                <Text style={styles.modalReason}>{reasonLabel}</Text>
                {myElapsedMs != null && (
                  <Text style={styles.modalBest}>
                    {bestTime == null
                      ? `本难度首次通关：${formatMineElapsed(myElapsedMs)}`
                      : myElapsedMs < bestTime
                        ? `新纪录！最佳用时 ${formatMineElapsed(myElapsedMs)}`
                        : `本难度最佳用时 ${formatMineElapsed(bestTime)}`}
                  </Text>
                )}
              </>
            )}
            {game?.ranking && game.ranking.length > 0 && (
              <ScrollView
                style={styles.rankingScroll}
                nestedScrollEnabled
              >
                {game.ranking.map((r, i) => (
                  <View
                    key={`r-${r.seat}-${i}`}
                    style={[
                      styles.rankingRow,
                      r.rank === 1 && styles.rankingFirst,
                    ]}
                  >
                    <Text style={styles.rankNum}>#{r.rank}</Text>
                    <Text
                      style={styles.rankingName}
                      numberOfLines={1}
                    >
                      {r.nickname || r.username}
                    </Text>
                    <Text style={[
                      styles.rankingStatus,
                      { color: r.alive ? colors.success : colors.danger },
                    ]}>
                      {r.alive ? '存活' : '淘汰'}
                    </Text>
                    <View style={styles.rankingStats}>
                      <Text style={styles.rankingStatText}>
                        标雷 {r.correct_flags}
                      </Text>
                      {r.wrong_flags > 0 && (
                        <Text style={styles.rankingWrong}>
                          错 {r.wrong_flags}
                        </Text>
                      )}
                      <Text style={styles.rankingStatText}>
                        翻 {r.revealed_count}
                      </Text>
                      {formatMineElapsed(r.elapsed_ms) ? (
                        <Text style={styles.rankingStatText}>
                          用时 {formatMineElapsed(r.elapsed_ms)}
                        </Text>
                      ) : null}
                    </View>
                  </View>
                ))}
              </ScrollView>
            )}
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnPrimary]}
              onPress={handleBackToRoom}
            >
              <Text style={styles.modalBtnPrimaryText}>返回房间</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: 'center',
  },
  topBar: {
    width: '100%',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: spacing.md,
    backgroundColor: colors.card,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  topRight: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  diffChip: {
    fontSize: fontSize.xs,
    color: colors.primary,
    backgroundColor: 'rgba(79,70,229,0.1)',
    paddingHorizontal: spacing.xs + 2,
    paddingVertical: 2,
    borderRadius: radius.full,
    fontWeight: '700',
    overflow: 'hidden',
  },
  statItem: { flexDirection: 'row', alignItems: 'center', gap: 2 },
  statText: { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  roomCode: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  // Player status chips (collapsible bar: collapsed = my chip only)
  playerBar: {
    width: '100%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    backgroundColor: colors.card,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  playerRow: {
    flex: 1,
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: spacing.xs,
  },
  playerToggle: {
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
    borderRadius: radius.full,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.bg,
  },
  playerMore: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    alignSelf: 'center',
  },
  playerChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
    borderRadius: radius.full,
    backgroundColor: colors.bg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  playerChipMe: {
    borderColor: colors.primary,
    backgroundColor: 'rgba(79,70,229,0.06)',
  },
  playerChipDead: {
    opacity: 0.6,
    borderColor: colors.danger,
    backgroundColor: 'rgba(239,68,68,0.06)',
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  playerName: {
    fontSize: fontSize.xs,
    color: colors.text,
    fontWeight: '600',
    maxWidth: 72,
  },
  playerNameDead: {
    textDecorationLine: 'line-through',
    color: colors.textMuted,
  },
  deadTag: {
    fontSize: 10,
    color: colors.danger,
    fontWeight: '700',
  },
  // 剩余命数：图标 + 「剩/总」数字。颜色只是辅助，形状（实心/空心心）与数字
  // 才是主通道——只靠颜色区分「还剩 1 条」和「满命」不符合 WCAG 1.4.1。
  livesTag: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 2,
  },
  livesText: {
    fontSize: 10,
    fontWeight: '700',
  },
  livesTextFull: { color: colors.textSecondary },
  livesTextLow: { color: colors.warning },
  playerStat: {
    fontSize: 10,
    color: colors.textSecondary,
  },
  // Eliminated spectator banner
  elimBanner: {
    width: '100%',
    backgroundColor: colors.danger,
    paddingVertical: spacing.sm,
    alignItems: 'center',
  },
  elimBannerText: {
    color: colors.white,
    fontSize: fontSize.sm,
    fontWeight: '700',
  },
  // Disconnect banner
  banner: {
    width: '100%',
    backgroundColor: colors.warning,
    paddingVertical: spacing.sm,
    alignItems: 'center',
  },
  bannerRow: { flexDirection: 'row', alignItems: 'center' },
  bannerText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '600' },
  // Mode bar
  modeBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    width: '100%',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  modeBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    borderRadius: radius.full,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    backgroundColor: colors.card,
  },
  modeBtnActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  modeBtnText: { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  modeBtnTextActive: { color: colors.white },
  hintText: { fontSize: fontSize.xs, color: colors.textMuted },
  // Board
  boardContainer: {
    flex: 1,
    width: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.sm,
  },
  // Advanced: horizontal scroll with hint
  advancedOuter: {
    flex: 1,
    width: '100%',
  },
  scrollHint: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
    textAlign: 'center',
    paddingVertical: spacing.xs,
  },
  // 双向滑动：外层横向（撑满 boardContainer），内层纵向。
  //
  // 外层 contentContainer **不能**加 `alignItems: 'center'`：横向 ScrollView 的
  // 内容容器是 row，alignItems 管的是交叉轴（高度）。一旦居中，内层 ScrollView
  // 就按内容高度（棋盘全高）撑开，等于没有高度约束，`overflow-y` 永远不触发
  // ——纵向滑动直接失效（E2E 抓到：横向能滑、纵向 y 一动不动）。
  // 用默认的 stretch 让内层撑满可视高，内层再靠 contentContainer 的 flexGrow 居中。
  boardHScroll: {
    flex: 1,
  },
  boardHScrollContent: {
    flexGrow: 1,
    justifyContent: 'center',
  },
  boardVScrollContent: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  boardWrap: {
    position: 'relative',
    backgroundColor: colors.border,
    borderRadius: radius.sm,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: colors.border,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.18,
    shadowRadius: 6,
    elevation: 3,
  },
  cell: {
    position: 'absolute',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 0.5,
    borderColor: '#DDE1E6',
  },
  cellUnrevealed: {
    backgroundColor: '#CBD5E1',
    borderTopWidth: 2,
    borderLeftWidth: 2,
    borderBottomWidth: 2,
    borderRightWidth: 2,
    borderTopColor: '#E4EAF1',
    borderLeftColor: '#E4EAF1',
    borderBottomColor: '#9AA7B8',
    borderRightColor: '#9AA7B8',
  },
  cellNumber: {
    fontWeight: '800',
  },
  cellQuestion: { fontWeight: '800', color: MINE_QUESTION },
  // Bottom bar
  bottomBar: {
    flexDirection: 'row',
    gap: spacing.md,
    padding: spacing.md,
  },
  leaveBtn: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  leaveBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  surrenderBtn: {
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.warning,
  },
  surrenderBtnText: {
    color: colors.warning,
    fontWeight: '600',
    fontSize: fontSize.md,
  },
  // Modal
  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay, justifyContent: 'center',
    alignItems: 'center', padding: spacing.md,
  },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card,
    borderRadius: radius.xl, padding: spacing.lg, alignItems: 'center',
  },
  modalTitle: {
    fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.md,
  },
  modalReason: {
    fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.sm,
    textAlign: 'center',
  },
  modalBest: {
    fontSize: fontSize.sm, color: colors.warning, fontWeight: '700',
    textAlign: 'center', marginBottom: spacing.sm,
  },
  modalBtn: {
    width: '100%', borderRadius: radius.md, paddingVertical: spacing.sm + 4,
    alignItems: 'center', marginTop: spacing.sm,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: {
    color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600',
  },
  // Ranking list in game-over modal
  rankingScroll: {
    width: '100%',
    maxHeight: 200,
    marginVertical: spacing.sm,
  },
  rankingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    gap: spacing.sm,
  },
  rankingFirst: {
    backgroundColor: 'rgba(245,158,11,0.1)',
    borderRadius: radius.sm,
    borderBottomWidth: 0,
    marginBottom: spacing.xs,
  },
  rankNum: {
    fontSize: fontSize.sm,
    fontWeight: '800',
    color: colors.textSecondary,
    width: 28,
  },
  rankingName: {
    fontSize: fontSize.sm,
    fontWeight: '600',
    color: colors.text,
    flex: 1,
  },
  rankingStatus: {
    fontSize: fontSize.xs,
    fontWeight: '700',
  },
  rankingStats: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  rankingStatText: {
    fontSize: 10,
    color: colors.textSecondary,
  },
  rankingWrong: {
    fontSize: 10,
    color: colors.danger,
    fontWeight: '600',
  },
});
