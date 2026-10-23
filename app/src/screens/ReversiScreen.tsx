// Reversi (翻转棋/奥赛罗) game screen — 8×8 board, 2-player online battle.
// Follows GomokuScreen pattern: WS sync, ghost preview, Vibration, Modal confirm.
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, useWindowDimensions,
  Platform, Vibration, Animated,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useAuth } from '../auth';
import { getRoom, leaveRoom, Room } from '../api';
import { RoomSocket, SocketMessage } from '../socket';
import {
  colors, spacing, radius, fontSize, reversiBoard, lastMoveMark, lastMoveMarkEdge,
} from '../theme';
import { notify } from '../utils';
import { useNavigation } from '@react-navigation/native';
import { useModalBackGuard, onConfirmLeave } from '../backGuard';
import { useDisconnectCountdown } from '../disconnect';
import { GameHelpButton } from '../help';

// --- Types ---

interface ReversiState {
  board_size: number;
  board: number[][];
  moves: Array<{ x: number; y: number; color: string; flipped?: number[][] }>;
  next: 'black' | 'white';
  your_color: 'black' | 'white';
  pass_count: number;
  winner: null | 'black' | 'white' | 'draw';
  reason: null | 'score' | 'surrender' | 'disconnect';
  score: { black: number; white: number };
}

interface Props {
  code: string;
  onLeave: () => void;
}

// --- Constants ---

const BOARD_SIZE = 8;
const DIRECTIONS = [
  [-1, -1], [-1, 0], [-1, 1],
  [0, -1],           [0, 1],
  [1, -1],  [1, 0],  [1, 1],
];

const WINNER_LABELS: Record<string, string> = {
  black: '黑棋胜',
  white: '白棋胜',
  draw: '和棋',
};

// 合法着脉冲：限次 3 轮，每轮 1.6s（800ms 放大 + 800ms 缩回）→ 总时长 4.8s < 5s。
// 自动播放且超过 5 秒的运动必须能停（WCAG 2.2.2），跑完停在常亮态。缩放幅度
// 1 → 1.25 属于「小元素的轻微变化」（方法论 §2），不用高对比闪烁（WCAG 2.3.1）。
const LEGAL_PULSE_CYCLES = 3;
const LEGAL_PULSE_MS = 800;

// 连锁翻转动画：错峰 65ms/枚、单枚 200ms（100ms 收 + 100ms 放）。小元素的轻微
// 变化落在方法论 §2 的 200-300ms 区间；错峰是为了让「谁翻了谁」可被眼睛追踪，
// 全盘同时翻会看不出发生了什么。错峰总跨度设上限，避免一次翻十几枚时整段动画
// 拖到 1 秒以上（§2：克制有度）——超过上限的棋子并到同一拍。
const FLIP_STAGGER_MS = 65;
const FLIP_STAGGER_MAX_MS = 400;
const FLIP_HALF_MS = 100;

// 棋盘坐标标注：列 A-H 在顶部、行 1-8 在左侧。复盘时用户可以说「D3」而不是
// 「从左数第四列往下第三行」。字号 fontSize.xs（12px，项目可见文字下限 10px），
// 颜色 colors.textMuted（vs 浅底 4.62:1）——它是「可承载信息的最低一档」；
// textFaint（2.54:1）禁止承载信息。
const COL_LABELS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
const ROW_LABELS = ['1', '2', '3', '4', '5', '6', '7', '8'];
const AXIS_W = 16;  // 左侧行号列宽
const AXIS_H = 16;  // 顶部列号行高

// --- Legal move computation (client-side for preview) ---

function findFlips(board: number[][], x: number, y: number, color: number): number[][] {
  const opp = color === 1 ? 2 : 1;
  const allFlips: number[][] = [];
  for (const [dx, dy] of DIRECTIONS) {
    const dirFlips: number[][] = [];
    let nx = x + dx;
    let ny = y + dy;
    while (nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE && board[ny][nx] === opp) {
      dirFlips.push([nx, ny]);
      nx += dx;
      ny += dy;
    }
    if (dirFlips.length > 0 && nx >= 0 && nx < BOARD_SIZE && ny >= 0 && ny < BOARD_SIZE && board[ny][nx] === color) {
      allFlips.push(...dirFlips);
    }
  }
  return allFlips;
}

function computeLegalMoves(board: number[][], color: number): Set<string> {
  const moves = new Set<string>();
  for (let y = 0; y < BOARD_SIZE; y++) {
    for (let x = 0; x < BOARD_SIZE; x++) {
      if (board[y][x] === 0 && findFlips(board, x, y, color).length > 0) {
        moves.add(`${x},${y}`);
      }
    }
  }
  return moves;
}

function countStones(board: number[][]): { black: number; white: number } {
  let black = 0;
  let white = 0;
  for (let y = 0; y < BOARD_SIZE; y++) {
    for (let x = 0; x < BOARD_SIZE; x++) {
      if (board[y][x] === 1) black++;
      else if (board[y][x] === 2) white++;
    }
  }
  return { black, white };
}

// --- Animated Stone ---

function StoneView({ colorVal, size, isNew, isFlipped, flipDelay = 0 }: {
  colorVal: number; size: number; isNew?: boolean; isFlipped?: boolean; flipDelay?: number;
}) {
  const scale = useRef(new Animated.Value(isNew ? 0.3 : 1)).current;
  const stoneSize = size * 0.82;
  const isBlack = colorVal === 1;

  useEffect(() => {
    if (isNew) {
      Animated.spring(scale, {
        toValue: 1, friction: 5, tension: 120, useNativeDriver: Platform.OS !== 'web',
      }).start();
    }
  }, [isNew]);

  useEffect(() => {
    if (isFlipped) {
      Animated.sequence([
        // 先等自己的错峰拍点，再收一下（翻面）再放回——「收」是让「换色」这件事
        // 有一帧被看见，否则只是颜色突变。
        Animated.delay(flipDelay),
        Animated.timing(scale, {
          toValue: 0.1, duration: FLIP_HALF_MS, useNativeDriver: Platform.OS !== 'web',
        }),
        Animated.timing(scale, {
          toValue: 1, duration: FLIP_HALF_MS, useNativeDriver: Platform.OS !== 'web',
        }),
      ]).start();
    }
  }, [isFlipped]);

  return (
    <Animated.View style={[
      styles.stone,
      isBlack ? styles.stoneBlack : styles.stoneWhite,
      { width: stoneSize, height: stoneSize, transform: [{ scale }] },
    ]} />
  );
}

// --- Main Component ---

export default function ReversiScreen({ code, onLeave }: Props) {
  const { token, user } = useAuth();
  const { width: winWidth } = useWindowDimensions();
  const [room, setRoom] = useState<Room | null>(null);
  const [game, setGame] = useState<ReversiState | null>(null);
  const [opponentDisconnected, setOpponentDisconnected] = useState(false);
  // 断线倒计时的起点：优先后端在事件里给的 timeout，其次房间数据里的剩余值
  const [dcTimeout, setDcTimeout] = useState<number | null>(null);
  const [showGameOver, setShowGameOver] = useState(false);
  const [preview, setPreview] = useState<{ x: number; y: number } | null>(null);
  const [confirm, setConfirm] = useState<'surrender' | 'leave' | null>(null);
  const navigation = useNavigation<any>();
  const skipRemoveRef = useRef(false);
  const gameOverRef = useRef(false);
  gameOverRef.current = showGameOver;
  useModalBackGuard(confirm !== null, () => setConfirm(null));

  // 系统/浏览器返回：对局中弹退出确认框，终局后允许直接返回
  useEffect(() => navigation.addListener('beforeRemove', (e: any) => {
    if (Platform.OS === 'web') return;  // Web 由 backGuard 的 popstate 层统一拦截
    if (skipRemoveRef.current) return;
    if (gameOverRef.current) {
      skipRemoveRef.current = true;
      navigation.dispatch(e.data.action);
      return;
    }
    e.preventDefault();
    setConfirm('leave');
  }), [navigation]);

  // Web 浏览器返回：backGuard 拦截 popstate 后派发此事件，就地弹确认框（原生端 no-op）
  useEffect(() => onConfirmLeave(() => {
    if (gameOverRef.current) {
      navigation.goBack();  // 终局后无需确认
      return;
    }
    setConfirm('leave');
  }), [navigation]);

  const socketRef = useRef<RoomSocket | null>(null);
  const gameOverShownRef = useRef(false);
  // Track cells that were just placed or flipped for animation.
  // animFlipped 用 Map（key → 错峰序号）而非 Set：错峰要按服务端下发的翻转顺序
  // 依次延迟，Set 会丢掉顺序。
  const [animNew, setAnimNew] = useState<string | null>(null);
  const [animFlipped, setAnimFlipped] = useState<Map<string, number>>(new Map());

  // Board sizing — fill available width with padding，并为左侧行号留出 AXIS_W
  const boardEdge = Math.min(winWidth - 24 - AXIS_W, 480);
  const cellSize = Math.floor(boardEdge / BOARD_SIZE);
  const boardPixelSize = cellSize * BOARD_SIZE;

  // Compute legal moves for current player (client-side for hints)
  const legalMoves = useMemo(() => {
    if (!game || game.winner) return new Set<string>();
    const myColor = game.your_color === 'black' ? 1 : 2;
    if (game.next !== game.your_color) return new Set<string>();
    return computeLegalMoves(game.board, myColor);
  }, [game]);

  // WS connection and message handling
  useEffect(() => {
    if (!token) return;
    gameOverShownRef.current = false;
    getRoom(token, code).then(setRoom).catch(() => {});
    const ws = new RoomSocket(code, token);
    socketRef.current = ws;

    const handleSync = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) setRoom(roomData);
      if (msg.game) {
        const g = msg.game as ReversiState;
        setGame(g);
        if (g.winner) setShowGameOver(true);
      }
      if (roomData) {
        const opponent = roomData.players.find((p) => p.id !== user?.id);
        setOpponentDisconnected(opponent ? !opponent.connected : false);
      }
    };

    const handleLobby = (msg: SocketMessage) => {
      const roomData = msg.room as Room;
      if (roomData) {
        setRoom(roomData);
        const opponent = roomData.players.find((p) => p.id !== user?.id);
        setOpponentDisconnected(opponent ? !opponent.connected : false);
      }
    };

    const handleGameStart = (msg: SocketMessage) => {
      const g = msg.game as ReversiState;
      if (g) {
        setGame(g);
        setShowGameOver(false);
        gameOverShownRef.current = false;
        setAnimNew(null);
        setAnimFlipped(new Map());
      }
    };

    const handleMove = (msg: SocketMessage) => {
      const x = msg.x as number;
      const y = msg.y as number;
      const color = msg.color as string;
      const next = msg.next as 'black' | 'white';
      const flipped = (msg.flipped as number[][]) || [];
      setGame((prev) => {
        if (!prev) return prev;
        const newBoard = prev.board.map((row) => [...row]);
        newBoard[y][x] = color === 'black' ? 1 : 2;
        for (const [fx, fy] of flipped) {
          newBoard[fy][fx] = color === 'black' ? 1 : 2;
        }
        const score = countStones(newBoard);
        return {
          ...prev, board: newBoard, next, score, pass_count: 0,
          // 最近一步高亮要用它。原来 handleMove 只更新 board/next/score，
          // moves 一直停在 sync 时的快照（开局是空数组）——渲染端那段
          // `lastMove = game.moves[last]` 因此永远是 null，标记从来没画出来过。
          moves: [...prev.moves, { x, y, color, flipped }],
        };
      });
      // Trigger animations — 按服务端给的翻转顺序排错峰序号
      setAnimNew(`${x},${y}`);
      setAnimFlipped(new Map(flipped.map(([fx, fy], i) => [`${fx},${fy}`, i])));
      // 动画跑完再清标记（错峰跨度有上限，所以总时长有界，不会拖到 1s 以上）
      const span = Math.min(Math.max(flipped.length - 1, 0) * FLIP_STAGGER_MS, FLIP_STAGGER_MAX_MS);
      setTimeout(() => { setAnimNew(null); setAnimFlipped(new Map()); }, span + FLIP_HALF_MS * 2 + 80);
    };

    const handlePass = (msg: SocketMessage) => {
      const passedColor = msg.color as string;
      const label = passedColor === 'black' ? '黑棋' : '白棋';
      notify('跳过回合', `${label}无合法着法，自动跳过`);
      setGame((prev) => prev ? { ...prev, pass_count: (prev.pass_count || 0) + 1 } : prev);
    };

    const handleGameOver = (msg: SocketMessage) => {
      const winner = msg.winner as string;
      const reason = msg.reason as string;
      const score = msg.score as { black: number; white: number } | undefined;
      setGame((prev) => prev ? {
        ...prev,
        winner: winner as ReversiState['winner'],
        reason: reason as ReversiState['reason'],
        score: score || prev.score,
      } : prev);
      if (!gameOverShownRef.current) {
        gameOverShownRef.current = true;
        setShowGameOver(true);
      }
      if (token) getRoom(token, code).then(setRoom).catch(() => {});
    };

    const handlePlayerDisconnected = (msg: SocketMessage) => {
      if ((msg.user_id as number) !== user?.id) {
        setOpponentDisconnected(true);
        setDcTimeout((msg.timeout as number) ?? null);
      }
    };

    const handlePlayerReconnected = (msg: SocketMessage) => {
      if ((msg.user_id as number) !== user?.id) setOpponentDisconnected(false);
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
    ws.on('move', handleMove);
    ws.on('pass', handlePass);
    ws.on('game_over', handleGameOver);
    ws.on('player_disconnected', handlePlayerDisconnected);
    ws.on('player_reconnected', handlePlayerReconnected);
    ws.on('room_closed', handleRoomClosed);
    ws.on('error', handleError);
    ws.connect();
    return () => { ws.close(); };
  }, [token, code, user?.id, onLeave]);

  const handleCellPress = useCallback((x: number, y: number) => {
    if (!game || !user || game.winner) return;
    if (game.next !== game.your_color) return;
    if (!legalMoves.has(`${x},${y}`)) return;
    if (Platform.OS !== 'web') Vibration.vibrate(10);
    socketRef.current?.move(x, y);
  }, [game, user, legalMoves]);

  const handleSurrender = () => setConfirm('surrender');
  const doSurrender = () => { setConfirm(null); socketRef.current?.surrender(); };

  const handleLeaveRoom = async () => {
    if (!token) return;
    try { await leaveRoom(token, code); } catch { /* continue */ }
    socketRef.current?.leave();
    socketRef.current?.close();
    skipRemoveRef.current = true;
    onLeave();
  };

  // 结算弹窗「返回」：关弹窗 + 关闭本页 socket + goBack，不发 REST leave、不发 socket leave
  // 房间保留（finished 状态），玩家仍留在房间里，回大厅后可直接开始下一局
  const handleBackToRoom = useCallback(() => {
    setShowGameOver(false);
    skipRemoveRef.current = true;
    socketRef.current?.close();
    navigation.goBack();
  }, [navigation]);

  const isMyTurn = game ? game.your_color === game.next && !game.winner : false;

  // 合法着脉冲：所有提示点绑到同一个 Animated.Value，同步脉冲（比每个点各跑一条
  // 动画省得多，视觉上也整齐）。只在轮到自己且有合法着时跑，限次后停在常亮态。
  const legalPulse = useRef(new Animated.Value(1)).current;
  const legalPulseRunning = useRef(false);
  const hasLegalMove = legalMoves.size > 0;
  useEffect(() => {
    if (isMyTurn && hasLegalMove) {
      if (!legalPulseRunning.current) {
        legalPulseRunning.current = true;
        Animated.loop(Animated.sequence([
          Animated.timing(legalPulse, {
            toValue: 1.25, duration: LEGAL_PULSE_MS, useNativeDriver: Platform.OS !== 'web',
          }),
          Animated.timing(legalPulse, {
            toValue: 1, duration: LEGAL_PULSE_MS, useNativeDriver: Platform.OS !== 'web',
          }),
        ]), { iterations: LEGAL_PULSE_CYCLES }).start();
      }
    } else if (legalPulseRunning.current) {
      legalPulseRunning.current = false;
      legalPulse.stopAnimation();
      legalPulse.setValue(1);
    }
  }, [isMyTurn, hasLegalMove, legalPulse]);

  const lastMove = game && game.moves.length > 0 ? game.moves[game.moves.length - 1] : null;
  const score = game ? game.score : { black: 0, white: 0 };
  const myColorLabel = game ? (game.your_color === 'black' ? '黑棋' : '白棋') : '';
  const myColorIconColor = game
    ? (game.your_color === 'black' ? colors.stoneBlack : '#ccc')
    : colors.textMuted;

  const turnText = (() => {
    if (!game || game.winner) return '';
    if (isMyTurn) return '轮到你落子';
    // 人机回合有 0.8-1.2 秒的思考延迟（服务端故意留的节奏）。只写「等待对方…」
    // 用户既不知道在等谁、也不知道要等多久，会以为卡住了——方法论 §1 响应时间
    // / §5 状态可见性。真人对手维持原文案。
    const opp = room?.players?.find((p) => p.id !== user?.id);
    if (opp?.is_bot) return `${opp.nickname || opp.username} 思考中…`;
    return '等待对方...';
  })();

  // 断线倒计时：事件里的 timeout 优先，其次房间数据里的剩余秒数（重连后是剩余值）
  const oppDcLeft = room?.players.find(
    (p) => p.id !== user?.id && !p.connected,
  )?.reconnect_left ?? null;
  const dcLeft = useDisconnectCountdown(opponentDisconnected, dcTimeout ?? oppDcLeft);

  return (
    <View style={styles.container}>
      {/* Top bar: color, turn, score */}
      <View style={styles.topBar}>
        <View style={styles.topLeft}>
          {game && <Ionicons name="ellipse" size={16} color={myColorIconColor} />}
          <Text style={styles.myColor}>{myColorLabel}</Text>
          <Text style={[styles.turnHint, isMyTurn && styles.turnActive]}>{turnText}</Text>
        </View>
        <View style={styles.scoreRow}>
          <View style={styles.scoreItem}>
            <View style={[styles.scoreDot, styles.scoreDotBlack]} />
            <Text style={styles.scoreNum}>{score.black}</Text>
          </View>
          <Text style={styles.scoreSep}>:</Text>
          <View style={styles.scoreItem}>
            <View style={[styles.scoreDot, styles.scoreDotWhite]} />
            <Text style={styles.scoreNum}>{score.white}</Text>
          </View>
        </View>
        <GameHelpButton game="reversi" size={24} />
        <Text style={styles.roomCode}>#{code}</Text>
      </View>

      {/* Disconnected banner */}
      {opponentDisconnected && (
        <View style={styles.banner}>
          <Ionicons name="warning" size={14} color={colors.white} />
          <Text style={styles.bannerText}> 对方已断线，等待重连（{dcLeft}秒）</Text>
        </View>
      )}

      {/* Board + 坐标标注（列 A-H 在顶、行 1-8 在左） */}
      <View style={styles.boardArea}>
        <View style={styles.axisRow}>
          <View style={{ width: AXIS_W }} />
          {COL_LABELS.map((label) => (
            <View key={label} style={{ width: cellSize, height: AXIS_H, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={styles.axisText}>{label}</Text>
            </View>
          ))}
        </View>
        <View style={styles.boardRow}>
          <View style={{ width: AXIS_W }}>
            {ROW_LABELS.map((label) => (
              <View key={label} style={{ height: cellSize, alignItems: 'center', justifyContent: 'center' }}>
                <Text style={styles.axisText}>{label}</Text>
              </View>
            ))}
          </View>
          <View style={[styles.boardWrap, { width: boardPixelSize, height: boardPixelSize }]}>
            {/* Grid lines */}
            {Array.from({ length: BOARD_SIZE + 1 }).map((_, i) => (
              <React.Fragment key={`lines-${i}`}>
                {i < BOARD_SIZE && (
                  <View style={[styles.hLine, { top: cellSize * i, left: 0, width: boardPixelSize }]} />
                )}
                {i < BOARD_SIZE && (
                  <View style={[styles.vLine, { left: cellSize * i, top: 0, height: boardPixelSize }]} />
                )}
              </React.Fragment>
            ))}
            {/* Last move marker */}
            {lastMove && (
              <View
                testID="reversi-last-mark"
                style={[
                  styles.lastMarker,
                  { left: cellSize * lastMove.x + cellSize * 0.5 - 4.5,
                    top: cellSize * lastMove.y + cellSize * 0.5 - 4.5 },
                ]}
              />
            )}
            {/* Cells */}
            {Array.from({ length: BOARD_SIZE }).map((_, y) =>
              Array.from({ length: BOARD_SIZE }).map((_, x) => {
                const val = game ? game.board[y][x] : 0;
                const key = `${x},${y}`;
                const isLegal = legalMoves.has(key);
                const isPreview = !!preview && preview.x === x && preview.y === y && val === 0;
                const isNew = animNew === key;
                const flipIdx = animFlipped.get(key);
                const isFlipped = flipIdx !== undefined;
                const flipDelay = flipIdx !== undefined
                  ? Math.min(flipIdx * FLIP_STAGGER_MS, FLIP_STAGGER_MAX_MS) : 0;
                return (
                  <TouchableOpacity
                    key={`cell-${x}-${y}`}
                    testID={`cell-${x}-${y}`}
                    style={[styles.cell, { left: cellSize * x, top: cellSize * y, width: cellSize, height: cellSize }]}
                    onPressIn={() => { if (val === 0 && isMyTurn && isLegal) setPreview({ x, y }); }}
                    onPressOut={() => setPreview(null)}
                    onPress={() => { setPreview(null); handleCellPress(x, y); }}
                    activeOpacity={isLegal ? 0.7 : 1}
                    disabled={!isLegal || !isMyTurn}
                  >
                    {val !== 0 && (
                      <StoneView
                        colorVal={val} size={cellSize}
                        isNew={isNew} isFlipped={isFlipped} flipDelay={flipDelay}
                      />
                    )}
                    {val === 0 && isLegal && (
                      // 合法着提示：**空心环**而非实心点。
                      // 原先用半透明实心点（rgba 0.35/0.5），压在棋盘绿 #2E7D32 上
                      // 只有 1.51:1，远低于图形 3:1；改成不透明描边的空心环后实测
                      // 黑 3.39:1 / 白 4.70:1。空心还顺带给了形状通道——环 ≠ 实心棋子。
                      <Animated.View style={[styles.hintDot, {
                        width: cellSize * 0.30, height: cellSize * 0.30,
                        borderRadius: cellSize * 0.15,
                        borderWidth: Math.max(2, Math.round(cellSize * 0.055)),
                        borderColor: game?.your_color === 'black'
                          ? colors.stoneBlack : colors.stoneWhite,
                        transform: [{ scale: legalPulse }],
                      }]} />
                    )}
                    {isPreview && (
                      <View style={[
                        styles.stone,
                        game?.your_color === 'black' ? styles.stoneBlack : styles.stoneWhite,
                        { width: cellSize * 0.82, height: cellSize * 0.82, opacity: 0.4 },
                      ]} />
                    )}
                  </TouchableOpacity>
                );
              })
            )}
          </View>
        </View>
      </View>

      {/* Action buttons */}
      <View style={styles.bottomBar}>
        {game && !game.winner && (
          <TouchableOpacity style={styles.surrenderBtn} onPress={handleSurrender}>
            <Ionicons name="flag-outline" size={16} color={colors.white} />
            <Text style={styles.surrenderBtnText}> 认输</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity style={styles.leaveBtn} onPress={() => setConfirm('leave')}>
          <Ionicons name="exit-outline" size={16} color={colors.textSecondary} />
          <Text style={styles.leaveBtnText}> 退出</Text>
        </TouchableOpacity>
      </View>

      {/* Confirm modal */}
      <Modal visible={confirm !== null} transparent animationType="fade" onRequestClose={() => setConfirm(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Ionicons
              name={confirm === 'surrender' ? 'flag-outline' : 'exit-outline'}
              size={36} color={colors.danger}
            />
            <Text style={styles.modalTitle}>
              {confirm === 'surrender' ? '确认认输？' : '确认退出？'}
            </Text>
            <Text style={styles.modalReason}>
              {confirm === 'surrender' ? '认输后本局判负，无法撤销' : '对局中退出将判负并离开房间'}
            </Text>
            <TouchableOpacity
              style={[styles.modalBtn, { backgroundColor: colors.danger }]}
              onPress={() => {
                if (confirm === 'surrender') doSurrender();
                else { setConfirm(null); handleLeaveRoom(); }
              }}
            >
              <Text style={styles.modalBtnPrimaryText}>
                {confirm === 'surrender' ? '确认认输' : '确认退出'}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.modalBtn, styles.modalBtnSecondary]}
              onPress={() => setConfirm(null)}
            >
              <Text style={styles.modalBtnSecondaryText}>再想想</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* Game over modal */}
      <Modal visible={showGameOver} transparent animationType="fade" onRequestClose={handleBackToRoom}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>对局结束</Text>
            {game && (
              <>
                <Text style={styles.modalResult}>
                  {WINNER_LABELS[game.winner || ''] || game.winner}
                </Text>
                <Text style={styles.modalScore}>
                  {score.black} : {score.white}
                </Text>
              </>
            )}
            <TouchableOpacity style={[styles.modalBtn, styles.modalBtnPrimary]} onPress={handleBackToRoom}>
              <Text style={styles.modalBtnPrimaryText}>返回房间</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// --- Styles ---

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, alignItems: 'center' },
  topBar: {
    width: '100%', flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', padding: spacing.md, backgroundColor: colors.card,
    borderBottomWidth: 1, borderBottomColor: colors.border,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  myColor: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  turnHint: { fontSize: fontSize.sm, color: colors.textSecondary },
  turnActive: { color: colors.primary, fontWeight: '600' },
  scoreRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs },
  scoreItem: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  scoreDot: { width: 12, height: 12, borderRadius: 6 },
  scoreDotBlack: { backgroundColor: colors.stoneBlack },
  scoreDotWhite: { backgroundColor: colors.stoneWhite, borderWidth: 1, borderColor: '#ccc' },
  scoreNum: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  scoreSep: { fontSize: fontSize.md, fontWeight: '700', color: colors.textMuted },
  roomCode: {
    fontSize: fontSize.sm, color: colors.textSecondary,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  banner: {
    width: '100%', backgroundColor: colors.warning, paddingVertical: spacing.sm,
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
  },
  bannerText: { color: colors.white, fontSize: fontSize.sm, fontWeight: '600' },
  boardArea: { marginTop: spacing.md },
  boardRow: { flexDirection: 'row', alignItems: 'flex-start' },
  axisRow: { flexDirection: 'row' },
  // 坐标标注文字：fontSize.xs（12px，≥ 项目 10px 下限），textMuted（vs 浅底 4.62:1）
  axisText: { fontSize: fontSize.xs, color: colors.textMuted, fontWeight: '600' },
  boardWrap: {
    position: 'relative', backgroundColor: reversiBoard,
    borderRadius: 8, overflow: 'hidden',
    shadowColor: colors.black, shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.18, shadowRadius: 6, elevation: 3,
  },
  hLine: { position: 'absolute', height: 1, backgroundColor: 'rgba(0,0,0,0.2)' },
  vLine: { position: 'absolute', width: 1, backgroundColor: 'rgba(0,0,0,0.2)' },
  lastMarker: {
    position: 'absolute', width: 9, height: 9, borderRadius: 5,
    backgroundColor: lastMoveMark,
    // 深墨描边是关键：黄点压在绿底 3.63:1，但压在白子上只有 1.29:1——
    // 靠这圈描边保证「黑子/白子/绿底」三种情况都看得见。
    borderWidth: 1.5, borderColor: lastMoveMarkEdge, zIndex: 1,
  },
  cell: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  stone: { borderRadius: 9999 },
  stoneBlack: {
    backgroundColor: colors.stoneBlack,
    shadowColor: colors.black, shadowOffset: { width: 1, height: 1 },
    shadowOpacity: 0.4, shadowRadius: 2, elevation: 3,
  },
  stoneWhite: {
    backgroundColor: colors.stoneWhite, borderWidth: 1, borderColor: '#ccc',
    shadowColor: colors.black, shadowOffset: { width: 1, height: 1 },
    shadowOpacity: 0.2, shadowRadius: 2, elevation: 2,
  },
  hintDot: { borderRadius: 9999 },
  bottomBar: { flexDirection: 'row', gap: spacing.md, padding: spacing.md },
  surrenderBtn: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.danger, borderRadius: radius.md,
  },
  surrenderBtnText: { color: colors.white, fontWeight: '700', fontSize: fontSize.md },
  leaveBtn: {
    flexDirection: 'row', alignItems: 'center',
    paddingHorizontal: spacing.lg, paddingVertical: spacing.sm + 6,
    backgroundColor: colors.bg, borderRadius: radius.md,
    borderWidth: 1, borderColor: colors.borderStrong,
  },
  leaveBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.md },
  // Modal
  modalOverlay: {
    flex: 1, backgroundColor: colors.overlay,
    justifyContent: 'center', alignItems: 'center', padding: spacing.md,
  },
  modalCard: {
    width: '100%', maxWidth: 360, backgroundColor: colors.card,
    borderRadius: radius.xl, padding: spacing.lg, alignItems: 'center',
  },
  modalTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.md },
  modalResult: { fontSize: fontSize.xxl, fontWeight: '800', color: colors.primary, marginBottom: spacing.xs },
  modalScore: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text, marginBottom: spacing.lg },
  modalReason: { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.lg },
  modalBtn: {
    width: '100%', borderRadius: radius.md,
    paddingVertical: spacing.sm + 4, alignItems: 'center', marginTop: spacing.sm,
  },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryText: { color: colors.white, fontSize: fontSize.md, fontWeight: '700' },
  modalBtnSecondary: { backgroundColor: colors.bg },
  modalBtnSecondaryText: { color: colors.textSecondary, fontSize: fontSize.md, fontWeight: '600' },
});
