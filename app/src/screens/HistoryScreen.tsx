// 历史战绩页面：列表 + 点击展开棋盘快照详情。
import React, { useState, useEffect, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
  ActivityIndicator,
} from 'react-native';
import { useAuth } from '../auth';
import { fetchMyHistory, GameRecord, HistoryResult, ConnectFourSnapshot, LuzhanqiSnapshot } from '../api';
import { GomokuIcon, MineIcon, ReversiIcon, BattleshipIcon, DotsIcon, LudoIcon, Game2048Icon, XiangqiIcon, ConnectFourIcon, TetrisIcon, UnoIcon, StasisIcon, LuzhanqiIcon } from '../icons';
import { UnoCardView } from '../UnoCardView';
import LuzhanqiBoardView from './LuzhanqiBoardView';
import { LzqView } from '../luzhanqi';
import { makeLayout } from '../luzhanqiBoard';
import {
  colors, spacing, radius, fontSize, g2048Tile, ludoColors,
  playerColors, battleshipShipColors, battleshipWater, withAlpha,
  reversiBoard, dotsInk,
  xiangqiBoard, xiangqiLine, xiangqiFace, xiangqiRed, xiangqiBlack,
  connect4Frame, connect4Slot, connect4Black, connect4White, connect4DiscEdge,
  tetrisBoard, tetrisSlot, tetrisEdge, tetrisPieceColors, tetrisGarbage,
} from '../theme';
import {
  XIANGQI_ROWS, XIANGQI_COLS, isRedPiece, pieceGlyph,
} from '../xiangqiBoard';
import { NUMBER_COLORS } from '../minesweeper';
import { ID_TO_PIECE, GARBAGE_ID } from '../tetris';
import { notify, formatDate } from '../utils';
// 游戏名的唯一来源（以前这里另有一份 GAME_LABELS，与主页卡片各写一遍）
import { gameTitle } from '../games';

const PAGE_SIZE = 20;

const RESULT_LABELS: Record<string, string> = {
  win: '胜',
  lose: '负',
  draw: '平',
  // 2048 专用：跑分竞速没有胜负，没合出 2048 就是「结束」
  end: '结束',
};

const RESULT_COLORS: Record<string, string> = {
  win: colors.success,
  lose: colors.danger,
  draw: colors.warning,
  end: colors.textSecondary,
};

/** 2048 胜利线（与服务端 game2048.WIN_TILE 一致）。 */
const WIN_TILE = 2048;

export default function HistoryScreen() {
  const { token, user } = useAuth();
  const [records, setRecords] = useState<GameRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const load = useCallback(async (p: number, append: boolean) => {
    if (!token) return;
    setLoading(true);
    try {
      const result = await fetchMyHistory(token, p, PAGE_SIZE);
      setRecords((prev) => append ? [...prev, ...result.items] : result.items);
      setTotal(result.total);
      setPage(p);
    } catch (err) {
      notify('加载失败', (err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load(1, false);
  }, [load]);

  const handleToggle = (id: number) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const handleLoadMore = () => {
    if (loading) return;
    if (records.length >= total) return;
    load(page + 1, true);
  };

  const myResult = (rec: GameRecord): HistoryResult | undefined => {
    if (!user) return undefined;
    return rec.results.find((r) => r.username === user.username);
  };

  const renderGameIcon = (game: string) => {
    if (game === 'gomoku') return <GomokuIcon size={36} />;
    if (game === 'reversi') return <ReversiIcon size={36} />;
    if (game === 'minesweeper') return <MineIcon size={36} />;
    if (game === 'battleship') return <BattleshipIcon size={36} />;
    if (game === 'dots') return <DotsIcon size={36} />;
    if (game === 'ludo') return <LudoIcon size={36} />;
    if (game === '2048') return <Game2048Icon size={36} />;
    if (game === 'xiangqi') return <XiangqiIcon size={36} />;
    if (game === 'connect4') return <ConnectFourIcon size={36} />;
    if (game === 'tetris') return <TetrisIcon size={36} />;
    if (game === 'uno') return <UnoIcon size={36} />;
    if (game === 'stasis') return <StasisIcon size={36} />;
    if (game === 'luzhanqi') return <LuzhanqiIcon size={36} />;
    return null;
  };

  if (!user || !token) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>未登录</Text>
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {records.length === 0 && !loading && (
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>暂无对局记录</Text>
          <Text style={styles.emptySub}>完成一局游戏后，战绩将显示在这里</Text>
        </View>
      )}
      {records.map((rec) => {
        const mr = myResult(rec);
        const resultKey = mr?.result || 'draw';
        const expanded = expandedId === rec.id;
        return (
          <TouchableOpacity
            key={rec.id}
            style={styles.card}
            onPress={() => handleToggle(rec.id)}
            activeOpacity={0.8}
          >
            <View style={styles.row}>
              <View style={styles.iconWrap}>{renderGameIcon(rec.game)}</View>
              <View style={styles.info}>
                <View style={styles.topLine}>
                  <Text style={styles.gameLabel}>{gameTitle(rec.game)}</Text>
                  <View style={[styles.badge, { backgroundColor: RESULT_COLORS[resultKey] }]}>
                    <Text style={styles.badgeText}>{RESULT_LABELS[resultKey]}</Text>
                  </View>
                </View>
                <Text style={styles.timeText}>{formatDate(rec.ended_at)}</Text>
                <Text style={styles.playersText}>
                  {rec.players.map((p) => p.nickname || p.username).join(' vs ')}
                </Text>
              </View>
              <Text style={styles.arrow}>{expanded ? '∨' : '›'}</Text>
            </View>
            {expanded && (
              <View style={styles.detail}>
                <View style={styles.resultRow}>
                  {rec.results.map((r) => (
                    <View key={r.username} style={styles.resultItem}>
                      <Text style={styles.resultName}>{r.username}</Text>
                      <Text style={[styles.resultValue, { color: RESULT_COLORS[r.result] }]}>
                        {RESULT_LABELS[r.result]}
                      </Text>
                    </View>
                  ))}
                </View>
                <BoardSnapshot rec={rec} />
              </View>
            )}
          </TouchableOpacity>
        );
      })}
      {records.length > 0 && records.length < total && (
        <TouchableOpacity
          style={styles.loadMore}
          onPress={handleLoadMore}
          disabled={loading}
        >
          <Text style={styles.loadMoreText}>
            {loading ? '加载中...' : '加载更多'}
          </Text>
        </TouchableOpacity>
      )}
      {loading && records.length === 0 && (
        <ActivityIndicator size="large" color={colors.primary} style={styles.loader} />
      )}
    </ScrollView>
  );
}

// Inline board snapshot renderer
function BoardSnapshot({ rec }: { rec: GameRecord }) {
  const snap = rec.snapshot;
  if (snap.game === 'gomoku') {
    return <GomokuBoard board={snap.board} size={snap.board_size} />;
  }
  if (snap.game === 'reversi') {
    return <ReversiBoard board={snap.board} size={snap.board_size} />;
  }
  if (snap.game === 'xiangqi') {
    return <XiangqiBoard snap={snap} />;
  }
  if (snap.game === 'battleship') {
    return <BattleshipSnapshot snap={snap} players={rec.players} />;
  }
  if (snap.game === '2048') {
    return <Snapshot2048 snap={snap} />;
  }
  if (snap.game === 'minesweeper') {
    // Detect new format (has ranking) vs old format (has lives/winner)
    // Use 'as any' for forward-compat with new snapshot shape
    const s = snap as any;
    const isNewFormat = 'ranking' in s && !('lives' in s);
    return (
      <View>
        {isNewFormat && s.ranking && (
          <MineRanking ranking={s.ranking} reason={s.reason} />
        )}
        <MineBoard
          width={snap.width}
          height={snap.height}
          revealed={snap.revealed}
          flagged={snap.flagged}
        />
      </View>
    );
  }
  if (snap.game === 'dots') {
    return <DotsSnapshot snap={snap} />;
  }
  if (snap.game === 'ludo') {
    return <LudoSnapshot snap={snap} players={rec.players} />;
  }
  if (snap.game === 'connect4') {
    return <ConnectFourBoard snap={snap} />;
  }
  if (snap.game === 'luzhanqi') {
    return <LuzhanqiBoardSnap snap={snap} />;
  }
  if (snap.game === 'tetris') {
    return <SnapshotTetris snap={snap as any} />;
  }
  if (snap.game === 'uno') {
    return <UnoSnapshotView snap={snap as any} />;
  }
  // stasis 快照类型尚未进 GameSnapshot 联合（api.ts），故用 as any 比较与传参。
  if ((snap as any).game === 'stasis') {
    return <StasisSnapshot snap={snap as any} players={rec.players} />;
  }
  return null;
}

/**
 * 深空休眠终局回放：每人一间舱室 + 关键等级 + 存活时长 + 静默体最终等级。
 *
 * 快照里 `players` 以 uid 为键，**不含用户名**——用户名要用 `seat_uids`
 * （座位 → uid）反查 `rec.players`（座位 → 用户名），同 LudoSnapshot 的做法。
 */
function StasisSnapshot({ snap, players }: {
  snap: any;
  players: Array<{ username: string; nickname: string | null; seat: number }>;
}) {
  const seatUids: number[] = snap.seat_uids || [];
  const people: Record<string, any> = snap.players || {};
  const entity = snap.entity || { level: 1, killed: false };
  const uids = Object.keys(people);
  if (uids.length === 0) return null;

  const nameOf = (uid: string) => {
    const seat = seatUids.indexOf(Number(uid));
    const p = players.find((x) => x.seat === seat);
    return p ? (p.nickname || p.username) : `玩家 ${uid}`;
  };
  // 毫秒 → m:ss（存活时长与游戏时钟都用它）
  const fmt = (ms: number) => {
    const s = Math.max(0, Math.floor((ms || 0) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  };
  const fateOf = (p: any) => {
    if (p.surrendered) return '弃权';
    if (p.eliminated_at_ms != null) return `失压 ${fmt(p.eliminated_at_ms)}`;
    if (p.alive) return `存活到 ${fmt(snap.clock_ms)}`;
    return '失压';
  };

  return (
    <View>
      <Text style={stasisSnapStyles.title}>
        {snap.winner === 'entity' ? '全员失压' : '船员生还'}
        {STASIS_REASON[snap.reason] ? `（${STASIS_REASON[snap.reason]}）` : ''}
      </Text>
      <Text style={stasisSnapStyles.entity}>
        静默体 Lv {entity.level}{entity.killed ? ' · 已被击杀' : ' · 未被击杀'}
        {/* 地图名由服务端随快照下发（v9 起有多张图）——**不要在客户端再抄一张
            名称表**：几何与名字都只有服务端一份。老战绩里没有这个字段，就不显示。 */}
        {snap.map?.name ? ` · ${snap.map.name}` : ''}
      </Text>
      {uids.map((uid) => {
        const p = people[uid] || {};
        const alive = p.alive && !p.surrendered;
        return (
          <View key={uid} style={stasisSnapStyles.row}>
            <View style={[stasisSnapStyles.dot, { backgroundColor: alive ? colors.success : colors.textMuted }]} />
            <View style={stasisSnapStyles.info}>
              <Text style={stasisSnapStyles.name} numberOfLines={1}>
                {nameOf(uid)}{p.room ? ` · ${p.room} 舱` : ''}
              </Text>
              <Text style={stasisSnapStyles.meta}>
                休眠舱 Lv{p.bed_level ?? 1} · 气密门 Lv{p.door_level ?? 1} · 建筑 {p.buildings?.length ?? 0} 座
              </Text>
            </View>
            <Text style={[stasisSnapStyles.fate, !alive && stasisSnapStyles.fateOut]}>
              {fateOf(p)}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

// 只列对玩家有意义的结局：击杀（唯一的胜利）/ 超时 / 全员失压。
// **`rescue` 已在 v8 删除**（救援抵达不再是胜利条件），但这里保留对老战绩的
// 兜底：v8 之前落库的记录里还写着 `rescue`，删掉这条历史页会显示成空白。
const STASIS_REASON: Record<string, string> = {
  killed: '击杀静默体',
  timeout: '没能击杀',
  wiped: '全员失压',
  rescue: '救援舰抵达',
};

const stasisSnapStyles = StyleSheet.create({
  title: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: 2 },
  entity: { fontSize: fontSize.sm, color: colors.textSecondary, marginBottom: spacing.xs },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: spacing.sm },
  info: { flex: 1 },
  name: { fontSize: fontSize.sm, color: colors.text, fontWeight: '600' },
  meta: { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  fate: { fontSize: fontSize.xs, color: colors.textSecondary, marginLeft: spacing.sm },
  fateOut: { color: colors.danger },
});

/**
 * UNO 终局回放：最后打出的牌 + 每人剩余手牌与名次。
 *
 * 牌面渲染直接复用对局屏那份 `UnoCardView`——分开写两份的话，同一张牌在
 * 战绩页和对局页会长得不一样（飞行棋棋盘踩过这个坑）。
 */
function UnoSnapshotView({ snap }: { snap: any }) {
  const ranking: any[] = snap.ranking || [];
  const hands: Record<string, string[]> = snap.hands || {};
  const cw = 26;
  const ch = 39;
  return (
    <View>
      <Text style={snapUnoStyles.title}>
        最终排名{RANK_REASON[snap.reason] ? `（${RANK_REASON[snap.reason]}）` : ''}
      </Text>
      {/* 本局打的是哪套房规（v1.14）。名字由服务端翻译好随快照下发 —— 同一个 UNO
          在「热闹」档和「经典」档下完全是两种牌局，只写一个游戏名等于什么都没说。
          老战绩没有这个字段，缺了就不显示，不留一行「玩法：undefined」。 */}
      {snap.rules_name ? (
        <Text style={snapUnoStyles.rulesName}>{`玩法：${snap.rules_name}`}</Text>
      ) : null}
      {/* 组队档（v1.16）：胜负是**队伍**，所以名次前面标出谁和谁一队 ——
          否则回放里两个并列第一看起来像「两个人都赢了」。老战绩没有这两个
          字段，缺了就不显示。 */}
      {snap.seat_teams && snap.winner_team != null ? (
        <Text style={snapUnoStyles.rulesName}>
          {`队伍：${(snap.seat_teams as (number | null)[])
            .map((t, s) => (t === null ? null : `${'AB'[t]}${s + 1}`))
            .filter(Boolean).join(' · ')}`}
        </Text>
      ) : null}
      {snap.discard_top ? (
        <View style={snapUnoStyles.topRow}>
          <Text style={snapUnoStyles.topLabel}>最后打出的牌</Text>
          <UnoCardView card={snap.discard_top} width={cw * 1.5} height={ch * 1.5} />
        </View>
      ) : null}
      {ranking.map((row: any) => {
        const cards: string[] = hands[String(row.seat)] || [];
        return (
          <View key={row.seat} style={snapUnoStyles.row}>
            <Text style={snapUnoStyles.rank}>{row.rank}.</Text>
            <View style={snapUnoStyles.who}>
              <Text style={snapUnoStyles.name} numberOfLines={1}>
                {row.nickname || row.username}
                {snap.seat_teams && row.team != null ? `（${'AB'[row.team] ?? '?'}队）` : ''}
                {row.surrendered ? '（弃权）' : ''}
              </Text>
              <Text style={snapUnoStyles.meta}>{`${row.cards} 张 · ${row.points} 分`}</Text>
            </View>
            <View style={snapUnoStyles.cards}>
              {cards.length === 0 ? (
                <Text style={snapUnoStyles.empty}>已出完</Text>
              ) : (
                cards.map((c, i) => (
                  <View key={`${c}-${i}`} style={snapUnoStyles.cardWrap}>
                    <UnoCardView card={c} width={cw} height={ch} />
                  </View>
                ))
              )}
            </View>
          </View>
        );
      })}
    </View>
  );
}

const snapUnoStyles = StyleSheet.create({
  title: {
    fontSize: fontSize.md,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.sm,
  },
  rulesName: {
    fontSize: fontSize.sm,
    color: colors.textMuted,
    marginTop: -spacing.xs,
    marginBottom: spacing.sm,
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: spacing.sm,
  },
  topLabel: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    marginRight: spacing.sm,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.xs,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  rank: {
    width: 22,
    fontSize: fontSize.sm,
    fontWeight: '700',
    color: colors.textSecondary,
  },
  who: { width: 104 },
  name: {
    fontSize: fontSize.sm,
    fontWeight: '600',
    color: colors.text,
  },
  meta: {
    fontSize: fontSize.xs,
    color: colors.textMuted,
  },
  cards: {
    flex: 1,
    flexDirection: 'row',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'flex-end',
  },
  cardWrap: { marginLeft: 3, marginTop: 3 },
  empty: {
    fontSize: fontSize.sm,
    color: colors.success,
    fontWeight: '600',
  },
});

function MineRanking({ ranking, reason }: {
  ranking: Array<{
    seat: number; username: string; nickname: string | null;
    alive: boolean; correct_flags: number; wrong_flags: number;
    revealed_count: number; rank: number;
  }>;
  reason: string | null;
}) {
  const reasonText = reason === 'cleared' ? '全部安全格翻开' : '全员淘汰';
  return (
    <View style={mineRankStyles.container}>
      <Text style={mineRankStyles.title}>结算排名（{reasonText}）</Text>
      {ranking.map((entry) => (
        <View key={entry.seat} style={mineRankStyles.row}>
          <Text style={mineRankStyles.rank}>#{entry.rank}</Text>
          <Text style={mineRankStyles.name}>
            {entry.nickname || entry.username}
          </Text>
          <Text style={[mineRankStyles.status,
            { color: entry.alive ? colors.success : colors.danger }
          ]}>
            {entry.alive ? '幸存' : '淘汰'}
          </Text>
          <Text style={mineRankStyles.stat}>
            标雷{entry.correct_flags} 翻开{entry.revealed_count}
          </Text>
        </View>
      ))}
    </View>
  );
}

const mineRankStyles = StyleSheet.create({
  container: { marginBottom: spacing.sm },
  title: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: 4 },
  row: {
    flexDirection: 'row', alignItems: 'center', paddingVertical: 2,
  },
  rank: { width: 28, fontSize: fontSize.sm, fontWeight: '700', color: colors.text },
  name: { flex: 1, fontSize: fontSize.sm, color: colors.text },
  status: { fontSize: fontSize.xs, fontWeight: '600', marginRight: spacing.sm },
  stat: { fontSize: fontSize.xs, color: colors.textSecondary },
});

// 重力四子棋终局盘面（7×6）。棋子色与对局屏同源（theme.ts），
// 白盘压在浅色空位槽上只有 1.24:1 —— 边界靠深墨盘沿，和棋盘上一致。
function ConnectFourBoard({ snap }: { snap: ConnectFourSnapshot }) {
  const cell = 26;
  const disc = Math.round(cell * 0.78);
  return (
    <ScrollView horizontal style={styles.boardScroll}>
      <View
        style={{
          width: snap.cols * cell + 8,
          height: snap.rows * cell + 8,
          padding: 4,
          borderRadius: radius.sm,
          backgroundColor: connect4Frame,
        }}
      >
        {snap.board.map((row, r) =>
          row.map((v, c) => (
            <View
              key={`${r}-${c}`}
              style={{
                position: 'absolute',
                left: 4 + c * cell,
                top: 4 + r * cell,
                width: cell,
                height: cell,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <View
                style={{
                  width: disc,
                  height: disc,
                  borderRadius: disc / 2,
                  backgroundColor: connect4Slot,
                }}
              />
              {v !== 0 && (
                <View
                  style={{
                    position: 'absolute',
                    width: disc,
                    height: disc,
                    borderRadius: disc / 2,
                    backgroundColor: v === 1 ? connect4Black : connect4White,
                    borderWidth: 1.5,
                    borderColor: connect4DiscEdge,
                  }}
                />
              )}
            </View>
          )),
        )}
      </View>
    </ScrollView>
  );
}

function GomokuBoard({ board, size }: { board: number[][]; size: number }) {
  return (
    <ScrollView horizontal style={styles.boardScroll}>
      <View style={[styles.gomokuWrap, { width: size * CELL + 1, height: size * CELL + 1 }]}>
        <View style={styles.boardBg} />
        {board.map((row, y) =>
          row.map((cell, x) => (
            <View
              key={`${x}-${y}`}
              style={[
                styles.cell,
                { left: x * CELL, top: y * CELL, width: CELL, height: CELL },
              ]}
            >
              <View style={styles.hLine} />
              <View style={styles.vLine} />
              {cell !== 0 && (
                <View
                  style={[
                    styles.stone,
                    { backgroundColor: cell === 1 ? colors.stoneBlack : colors.stoneWhite },
                    cell === 2 && styles.stoneWhiteBorder,
                  ]}
                />
              )}
            </View>
          ))
        )}
      </View>
    </ScrollView>
  );
}

function ReversiBoard({ board, size }: { board: number[][]; size: number }) {
  const rCell = Math.min(CELL, 28);
  return (
    <ScrollView horizontal style={styles.boardScroll}>
      <View
        style={[
          styles.revWrap,
          { width: size * rCell, height: size * rCell, backgroundColor: reversiBoard },
        ]}
      >
        {board.map((row, y) =>
          row.map((cell, x) => (
            <View
              key={`${x}-${y}`}
              style={[
                styles.revCell,
                {
                  left: x * rCell, top: y * rCell,
                  width: rCell, height: rCell,
                },
              ]}
            >
              {cell !== 0 && (
                <View
                  style={[
                    styles.revStone,
                    { backgroundColor: cell === 1 ? colors.stoneBlack : colors.stoneWhite },
                    cell === 2 && styles.stoneWhiteBorder,
                    { width: rCell * 0.8, height: rCell * 0.8, borderRadius: rCell * 0.4 },
                  ]}
                />
              )}
            </View>
          ))
        )}
      </View>
    </ScrollView>
  );
}

/**
 * 中国象棋终局盘面回放。
 *
 * 画法与对局屏一致（棋子落在交叉点上、外圈留 0.6 单位给边缘棋子出界），
 * 只是尺寸固定小一号——回放只要看清局面，不需要点。
 */
function XiangqiBoard({ snap }: { snap: any }) {
  const cell = 20;
  const pad = cell * 0.6;
  const rows = snap.rows || XIANGQI_ROWS;
  const cols = snap.cols || XIANGQI_COLS;
  const w = (cols - 1) * cell + 2 * pad;
  const h = (rows - 1) * cell + 2 * pad;
  const pieceSize = cell * 0.86;
  const board: (string | null)[][] = snap.board || [];
  return (
    <ScrollView horizontal style={styles.boardScroll}>
      <View style={[styles.xqWrap, { width: w, height: h, backgroundColor: xiangqiBoard }]}>
        {/* 横线（贯通全宽） */}
        {Array.from({ length: rows }).map((_, j) => (
          <View
            key={`h${j}`}
            style={[styles.xqLine, {
              left: pad, top: pad + j * cell - 0.5, width: (cols - 1) * cell, height: 1,
            }]}
          />
        ))}
        {/* 竖线：最外两条贯通，中间在河界处断开 */}
        {Array.from({ length: cols }).flatMap((_, i) => {
          const x = pad + i * cell - 0.5;
          if (i === 0 || i === cols - 1) {
            return [<View key={`v${i}`} style={[styles.xqLine, {
              left: x, top: pad, width: 1, height: (rows - 1) * cell,
            }]} />];
          }
          return [
            <View key={`v${i}a`} style={[styles.xqLine, {
              left: x, top: pad, width: 1, height: 4 * cell,
            }]} />,
            <View key={`v${i}b`} style={[styles.xqLine, {
              left: x, top: pad + 5 * cell, width: 1, height: 4 * cell,
            }]} />,
          ];
        })}
        {/* 棋子 */}
        {board.flatMap((row, r) => row.map((piece, c) => {
          if (!piece) return null;
          const red = isRedPiece(piece);
          const ink = red ? xiangqiRed : xiangqiBlack;
          return (
            <View
              key={`p${r}-${c}`}
              style={[styles.xqPiece, {
                left: pad + c * cell - pieceSize / 2,
                top: pad + r * cell - pieceSize / 2,
                width: pieceSize, height: pieceSize, borderRadius: pieceSize / 2,
                borderColor: ink, borderWidth: 1.4,
              }]}
            >
              <Text style={{ color: ink, fontSize: Math.round(pieceSize * 0.56), fontWeight: '700' }}>
                {pieceGlyph(piece)}
              </Text>
            </View>
          );
        }))}
      </View>
    </ScrollView>
  );
}

/**
 * 军棋战绩回放 —— **复用对局屏那一份棋盘画法**（`LuzhanqiBoardView`），
 * 所以只需要把快照拼成一个只读的 `LzqView`。地形随快照一起存下来了，
 * 不必在客户端再写一份拓扑（同「几何只有一份实现」）。
 */
function LuzhanqiBoardSnap({ snap }: { snap: LuzhanqiSnapshot }) {
  const cellW = 26;
  const cellH = 17;
  const layout = makeLayout(snap.topology, cellW, cellH, cellH * 0.5);
  const view: LzqView = {
    rows: snap.rows,
    cols: snap.cols,
    cells: snap.board,
    topology: snap.topology,
    phase: 'playing',
    mode: snap.mode,
    reveal: true,
    next: '',
    your_color: '',
    winner: snap.winner,
    reason: snap.reason,
    finished: true,
    last_move: null,
    flag_revealed: {},
    ready: {},
    alive: {},
    battles: [],
    legal: [],
  };
  return (
    <ScrollView horizontal style={styles.boardScroll}>
      <LuzhanqiBoardView view={view} layout={layout} flipped={false} interactive={false} />
    </ScrollView>
  );
}

function BattleshipSnapshot({ snap, players }: {
  snap: any;
  players: Array<{ username: string; color?: string }>;
}) {
  const bsCell = 16;
  const placements = snap.placements || {};
  const uids = Object.keys(placements);
  if (uids.length === 0) return null;
  return (
    <View>
      <Text style={bsStyles.title}>
        {snap.winner ? `${snap.winner === 'blue' ? '蓝方' : '红方'}胜` : '对局结束'}
        {snap.reason ? ` (${REASON_LABELS[snap.reason] || snap.reason})` : ''}
      </Text>
      <View style={bsStyles.boards}>
        {uids.map((uid) => {
          const pdata = placements[uid];
          const board: number[][] = pdata.board || [];
          const player = players.find(
            (p) => p.color === snap.player_colors?.[uid]
          );
          const colorLabel = snap.player_colors?.[uid] === 'blue' ? '蓝方' : '红方';
          return (
            <View key={uid} style={bsStyles.boardWrap}>
              <Text style={bsStyles.label}>
                {player?.username || colorLabel}
              </Text>
              <View style={[bsStyles.board, {
                width: 10 * bsCell, height: 10 * bsCell,
              }]}>
                {board.map((row, y) =>
                  row.map((cell, x) => (
                    <View
                      key={`${x}-${y}`}
                      style={[bsStyles.cell, {
                        left: x * bsCell, top: y * bsCell,
                        width: bsCell, height: bsCell,
                        backgroundColor: cell > 0
                          ? BS_SHIP_COLORS[cell] || '#666'
                          : battleshipWater,
                      }]}
                    />
                  ))
                )}
              </View>
            </View>
          );
        })}
      </View>
    </View>
  );
}

const BS_SHIP_COLORS = battleshipShipColors;

const REASON_LABELS: Record<string, string> = {
  all_sunk: '全部击沉', surrender: '认输', disconnect: '断线判负',
};

const bsStyles = StyleSheet.create({
  title: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: 4 },
  boards: { flexDirection: 'row', gap: spacing.md },
  boardWrap: { alignItems: 'center' },
  label: { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: 2 },
  board: { position: 'relative', backgroundColor: battleshipWater, borderRadius: 4, overflow: 'hidden' },
  cell: { position: 'absolute', borderWidth: 0.5, borderColor: 'rgba(255,255,255,0.1)' },
});

function DotsSnapshot({ snap }: { snap: any }) {
  const dCell = 18;
  const dDot = 3;
  const dots = snap.dots || 5;
  const rows = dots - 1;
  const cols = dots - 1;
  const bw = cols * dCell + dDot * 2;
  const bh = rows * dCell + dDot * 2;
  const pColors: Record<number, string> = { 1: playerColors.blue, 2: playerColors.red };
  const boxFills: Record<number, string> = {
    1: withAlpha(playerColors.blue, 0.25), 2: withAlpha(playerColors.red, 0.25),
  };
  // 与 DotsScreen 一致：引擎标识是 black/white，实际渲染是蓝/红
  const winnerLabel = snap.winner === 'black' ? '蓝方胜'
    : snap.winner === 'white' ? '红方胜' : '平局';
  const scoreP1 = snap.scores?.[1] || snap.scores?.['1'] || 0;
  const scoreP2 = snap.scores?.[2] || snap.scores?.['2'] || 0;
  return (
    <View>
      <Text style={bsStyles.title}>
        {winnerLabel} ({scoreP1}:{scoreP2})
      </Text>
      <ScrollView horizontal style={styles.boardScroll}>
        <View style={[dotsStyles.board, { width: bw, height: bh }]}>
          {/* Box fills */}
          {snap.boxes && snap.boxes.map((row: number[], by: number) =>
            row.map((owner: number, bx: number) => owner !== 0 ? (
              <View key={`b${by}-${bx}`} style={[dotsStyles.boxFill, {
                left: dDot + bx * dCell + 1, top: dDot + by * dCell + 1,
                width: dCell - 2, height: dCell - 2,
                backgroundColor: boxFills[owner] || 'transparent',
              }]} />
            ) : null)
          )}
          {/* Horizontal edges */}
          {snap.h && snap.h.map((row: number[], y: number) =>
            row.map((val: number, x: number) => val !== 0 ? (
              <View key={`h${y}-${x}`} style={[dotsStyles.hEdge, {
                left: dDot + x * dCell + dDot, top: dDot + y * dCell - 1,
                width: dCell - dDot, height: 2,
                backgroundColor: pColors[val] || '#666',
              }]} />
            ) : null)
          )}
          {/* Vertical edges */}
          {snap.v && snap.v.map((row: number[], y: number) =>
            row.map((val: number, x: number) => val !== 0 ? (
              <View key={`v${y}-${x}`} style={[dotsStyles.vEdge, {
                left: dDot + x * dCell - 1, top: dDot + y * dCell + dDot,
                width: 2, height: dCell - dDot,
                backgroundColor: pColors[val] || '#666',
              }]} />
            ) : null)
          )}
          {/* Dots */}
          {Array.from({ length: dots }).map((_, y) =>
            Array.from({ length: dots }).map((_, x) => (
              <View key={`d${y}-${x}`} style={[dotsStyles.dot, {
                left: dDot + x * dCell - dDot, top: dDot + y * dCell - dDot,
                width: dDot * 2, height: dDot * 2, borderRadius: dDot,
              }]} />
            ))
          )}
        </View>
      </ScrollView>
    </View>
  );
}

function Snapshot2048({ snap }: { snap: any }) {
  const boards = snap.boards || {};
  const scores = snap.scores || {};
  const maxTiles = snap.max_tiles || {};
  const people: Record<string, { username?: string; nickname?: string }> = snap.players || {};
  const uids = Object.keys(boards);
  if (uids.length === 0) return null;
  // 一律显示用户名：2048 的颜色是按座位顺序分的，既不是身份也不是名次
  // （1-4 人都可能拿到任意一色），拿颜色当「红方/蓝方」既不对也无从分辨。
  const nameOf = (uid: string) =>
    people[uid]?.nickname || people[uid]?.username || `玩家 ${uid}`;
  // 只有真的合出 2048 才算赢家；否则这局就是「游戏结束」，不分胜负。
  const champion = uids.find((uid) => (maxTiles[uid] || 0) >= WIN_TILE);
  const title = champion
    ? `${nameOf(champion)} 合成 2048`
    : '游戏结束 · 无人合成 2048';
  return (
    <View>
      <Text style={snap2048Styles.title}>
        {title}{G2048_REASON[snap.reason] ? ` (${G2048_REASON[snap.reason]})` : ''}
      </Text>
      <View style={snap2048Styles.boards}>
        {uids.map((uid) => (
          <View key={uid} style={snap2048Styles.boardWrap}>
            <Text style={snap2048Styles.label}>
              {nameOf(uid)}
              {' '}分数 {scores[uid] || 0} · 最大 {maxTiles[uid] || 0}
            </Text>
            <View style={[snap2048Styles.board, { width: 4 * 28 + 3 * 4, height: 4 * 28 + 3 * 4 }]}>
              {(boards[uid] || []).map((row: number[], r: number) =>
                row.map((val: number, c: number) => {
                  const tileColor = g2048Tile(val);
                  return (
                    <View
                      key={`${r}-${c}`}
                      style={[
                        snap2048Styles.cell,
                        {
                          left: c * 32, top: r * 32, width: 28, height: 28,
                          backgroundColor: val === 0 ? '#cdc1b4' : tileColor.bg,
                        },
                      ]}
                    >
                      {val > 0 && (
                        <Text style={[
                          snap2048Styles.cellText,
                          { color: tileColor.text, fontSize: val >= 1024 ? 8 : val >= 128 ? 10 : 12 },
                        ]}>
                          {val}
                        </Text>
                      )}
                    </View>
                  );
                })
              )}
            </View>
          </View>
        ))}
      </View>
    </View>
  );
}

/** 俄罗斯方块战绩：名次表 + 每人一张小盘面（20×10，格子压到 9px）。 */
function SnapshotTetris({ snap }: { snap: any }) {
  const boards: Record<string, number[][]> = snap.boards || {};
  const people: Record<string, { username?: string; nickname?: string }> = snap.players || {};
  const ranking: any[] = snap.ranking || [];
  const uids = Object.keys(boards);
  if (uids.length === 0) return null;
  const nameOf = (uid: string) => people[uid]?.nickname || people[uid]?.username || `玩家 ${uid}`;
  const rankOf = (uid: string) => ranking.find((r) => String(r.user_id) === uid);
  const cell = 9;
  return (
    <View>
      <Text style={snapTetrisStyles.title}>
        最终排名{RANK_REASON[snap.reason] ? `（${RANK_REASON[snap.reason]}）` : ''}
      </Text>
      {(ranking.length > 0 ? ranking : uids.map((uid) => ({ user_id: uid, rank: 0 }))).map((row: any) => {
        const uid = String(row.user_id);
        const info = rankOf(uid);
        return (
          <Text key={uid} style={snapTetrisStyles.rankRow}>
            {info ? `${info.rank}. ` : ''}{nameOf(uid)}
            {snap.scores?.[uid] ?? 0} 分 · {snap.lines?.[uid] ?? 0} 行 · {snap.levels?.[uid] ?? 1} 级
            {snap.garbage_received?.[uid] ? ` · 被顶 ${snap.garbage_received[uid]} 行` : ''}
          </Text>
        );
      })}
      <View style={snapTetrisStyles.boards}>
        {uids.map((uid) => (
          <View key={uid} style={snapTetrisStyles.boardWrap}>
            <Text style={snapTetrisStyles.label}>{nameOf(uid)}</Text>
            <View style={[
              snapTetrisStyles.board,
              { width: 10 * cell + 4, height: 20 * cell + 4 },
            ]}>
              {(boards[uid] || []).map((rowArr: number[], r: number) => (
                <View key={r} style={snapTetrisStyles.row}>
                  {rowArr.map((value: number, c: number) => {
                    const piece = ID_TO_PIECE[value];
                    const bg = value === 0 || value === GARBAGE_ID
                      ? (value === 0 ? tetrisSlot : tetrisGarbage)
                      : (piece && tetrisPieceColors[piece]) || tetrisGarbage;
                    return (
                      <View
                        key={c}
                        style={{
                          width: cell, height: cell,
                          backgroundColor: bg,
                          borderWidth: value === 0 ? 0 : 1,
                          borderColor: tetrisEdge,
                        }}
                      />
                    );
                  })}
                </View>
              ))}
            </View>
          </View>
        ))}
      </View>
    </View>
  );
}

const RANK_REASON: Record<string, string> = {
  ranking: '全员结束', surrender: '认输', disconnect: '掉线',
  // UNO：单局定胜负；组队档多一条「一队两人都出完」
  emptied: '有人出完手牌', last_remaining: '其余人已出局',
  team_emptied: '一队两人都出完了手牌',
};

const snapTetrisStyles = StyleSheet.create({
  title: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  rankRow: { fontSize: fontSize.sm, color: colors.textSecondary, paddingVertical: 1 },
  boards: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md, marginTop: spacing.sm },
  boardWrap: { alignItems: 'center' },
  label: { fontSize: fontSize.xs, color: colors.textMuted, marginBottom: 2 },
  board: {
    flexDirection: 'column', backgroundColor: tetrisBoard,
    borderWidth: 2, borderColor: tetrisEdge, borderRadius: 3, overflow: 'hidden',
  },
  row: { flexDirection: 'row' },
});

const dotsStyles = StyleSheet.create({
  board: { position: 'relative', backgroundColor: '#FAFAF8', borderRadius: 4, overflow: 'hidden' },
  dot: { position: 'absolute', backgroundColor: dotsInk, zIndex: 2 },
  boxFill: { position: 'absolute', zIndex: 0 },
  hEdge: { position: 'absolute', zIndex: 1 },
  vEdge: { position: 'absolute', zIndex: 1 },
});

// 只列**对用户有意义**的结束原因；服务端还会下发 "ranking"（全员结束），
// 那是内部结算标记，标题里已经说清名次了，不要漏出去当术语显示。
const G2048_REASON: Record<string, string> = {
  reach_2048: '合成 2048', both_stuck: '双方无法移动',
  surrender: '认输', disconnect: '断线判负',
};


const snap2048Styles = StyleSheet.create({
  title: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: 4 },
  boards: { flexDirection: 'row', gap: spacing.md },
  boardWrap: { alignItems: 'center' },
  label: { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: 2 },
  board: { position: 'relative', backgroundColor: '#bbada0', borderRadius: 4, overflow: 'hidden' },
  cell: { position: 'absolute', alignItems: 'center', justifyContent: 'center', borderRadius: 3 },
  cellText: { fontWeight: '800' },
});

function MineBoard({
  width, height, revealed, flagged,
}: {
  width: number; height: number;
  revealed: number[][]; flagged: number[][];
}) {
  const mCell = width > 16 ? 16 : 22;
  const revMap = new Map<string, number>();
  revealed.forEach(([x, y, v]) => revMap.set(`${x},${y}`, v));
  const flagSet = new Set(flagged.map(([x, y]) => `${x},${y}`));
  const rows = [];
  for (let y = 0; y < height; y++) {
    const row = [];
    for (let x = 0; x < width; x++) {
      const key = `${x},${y}`;
      const v = revMap.get(key);
      const isFlag = flagSet.has(key);
      const isRevealed = v !== undefined;
      row.push(
        <View
          key={key}
          style={[
            styles.mineCell,
            {
              width: mCell, height: mCell,
              backgroundColor: isRevealed ? '#d4c29a' : '#b8b8b8',
            },
          ]}
        >
          {isRevealed && v === -1 && <Text style={styles.mineText}>*</Text>}
          {isRevealed && v > 0 && (
            <Text style={[styles.mineNum, { color: NUMBER_COLORS[v] || colors.text }]}>
              {v}
            </Text>
          )}
          {!isRevealed && isFlag && <Text style={styles.mineFlag}>F</Text>}
        </View>
      );
    }
    rows.push(<View key={y} style={styles.mineRow}>{row}</View>);
  }
  return (
    <ScrollView horizontal style={styles.boardScroll}>
      <View>{rows}</View>
    </ScrollView>
  );
}

const CELL = 22;

function LudoSnapshot({ snap, players }: {
  snap: any;
  players: Array<{ username: string; seat: number; color?: string }>;
}) {
  const ranking: Array<{
    seat: number; username: string; nickname: string | null;
    color: string; finished_count: number; rank: number;
  }> = snap.ranking || [];
  const ludoPlayers = snap.players || {};

  return (
    <View style={ludoSnapStyles.container}>
      <Text style={ludoSnapStyles.title}>终局排名</Text>
      {ranking.map((r) => (
        <View key={r.seat} style={[ludoSnapStyles.row, r.rank === 1 && ludoSnapStyles.first]}>
          <Text style={ludoSnapStyles.rank}>#{r.rank}</Text>
          <View style={[ludoSnapStyles.colorDot, { backgroundColor: ludoColors[r.color] || '#999' }]} />
          <Text style={ludoSnapStyles.name}>{r.nickname || r.username}</Text>
          <Text style={ludoSnapStyles.count}>{r.finished_count}/4 到达</Text>
        </View>
      ))}
      {/* Mini board showing final positions */}
      <View style={ludoSnapStyles.boardWrap}>
        {Object.entries(ludoPlayers).map(([seat, p]: [string, any]) => (
          <View key={seat} style={ludoSnapStyles.playerRow}>
            <View style={[ludoSnapStyles.colorDot, { backgroundColor: ludoColors[p.color] || '#999' }]} />
            <Text style={ludoSnapStyles.pieces}>
              {p.pieces.map((v: number) => v === 57 ? '✓' : v === -1 ? '·' : v).join(' ')}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const ludoSnapStyles = StyleSheet.create({
  container: { padding: spacing.sm },
  title: { fontSize: fontSize.sm, fontWeight: '700', color: colors.text, marginBottom: spacing.xs },
  row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 3 },
  first: { backgroundColor: 'rgba(79,70,229,0.08)', borderRadius: radius.sm },
  rank: { width: 28, fontSize: fontSize.xs, fontWeight: '700', color: colors.textSecondary },
  colorDot: { width: 10, height: 10, borderRadius: 5, marginRight: 6 },
  name: { flex: 1, fontSize: fontSize.xs, color: colors.text },
  count: { fontSize: fontSize.xs, color: colors.textSecondary },
  boardWrap: { marginTop: spacing.sm, paddingTop: spacing.xs, borderTopWidth: 1, borderTopColor: colors.border },
  playerRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 2 },
  pieces: { fontSize: fontSize.xs, color: colors.text, fontFamily: 'monospace' },
});

const styles = StyleSheet.create({
  container: { padding: spacing.md, paddingBottom: spacing.xxl },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: colors.bg },
  errorText: { color: colors.danger, fontSize: fontSize.lg },
  empty: { alignItems: 'center', marginTop: spacing.xxl * 2 },
  emptyTitle: { fontSize: fontSize.xl, fontWeight: '700', color: colors.text },
  emptySub: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: spacing.sm },
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.lg,
    padding: spacing.md,
    marginBottom: spacing.md,
    shadowColor: colors.black,
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 2,
  },
  row: { flexDirection: 'row', alignItems: 'center' },
  iconWrap: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  info: { flex: 1, marginLeft: spacing.md },
  topLine: { flexDirection: 'row', alignItems: 'center', marginBottom: 2 },
  gameLabel: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  badge: {
    marginLeft: spacing.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
    borderRadius: radius.full,
  },
  badgeText: { color: colors.white, fontSize: fontSize.xs, fontWeight: '700' },
  timeText: { fontSize: fontSize.xs, color: colors.textMuted, marginTop: 2 },
  playersText: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  arrow: { fontSize: fontSize.xl, color: colors.textFaint, marginLeft: spacing.sm },
  detail: { marginTop: spacing.md, paddingTop: spacing.md, borderTopWidth: 1, borderTopColor: colors.border },
  resultRow: { flexDirection: 'row', justifyContent: 'space-around', marginBottom: spacing.md },
  resultItem: { alignItems: 'center' },
  resultName: { fontSize: fontSize.sm, color: colors.textSecondary, marginBottom: 2 },
  resultValue: { fontSize: fontSize.lg, fontWeight: '700' },
  boardScroll: { marginTop: spacing.sm },
  gomokuWrap: { position: 'relative', backgroundColor: colors.boardBg, borderRadius: radius.sm, overflow: 'hidden' },
  boardBg: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: colors.boardBg },
  cell: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  hLine: {
    position: 'absolute', left: 0, right: 0, top: '50%',
    height: 1, backgroundColor: colors.boardLine,
  },
  vLine: {
    position: 'absolute', top: 0, bottom: 0, left: '50%',
    width: 1, backgroundColor: colors.boardLine,
  },
  stone: { width: CELL * 0.82, height: CELL * 0.82, borderRadius: CELL * 0.41, zIndex: 1 },
  stoneWhiteBorder: { borderWidth: 1, borderColor: colors.stoneEdge },
  revWrap: { position: 'relative', borderRadius: radius.sm, overflow: 'hidden' },
  revCell: { position: 'absolute', alignItems: 'center', justifyContent: 'center' },
  revStone: { zIndex: 1 },
  xqWrap: { position: 'relative', borderRadius: radius.sm },
  xqLine: { position: 'absolute', backgroundColor: xiangqiLine },
  xqPiece: {
    position: 'absolute', alignItems: 'center', justifyContent: 'center',
    backgroundColor: xiangqiFace,
  },
  mineRow: { flexDirection: 'row' },
  mineCell: { alignItems: 'center', justifyContent: 'center', borderWidth: 0.5, borderColor: '#999' },
  mineText: { color: colors.danger, fontWeight: '800', fontSize: 14 },
  mineNum: { fontWeight: '800', fontSize: 13 },
  mineFlag: { color: colors.primary, fontWeight: '800', fontSize: 13 },
  loadMore: { alignItems: 'center', paddingVertical: spacing.md },
  loadMoreText: { color: colors.primary, fontSize: fontSize.sm, fontWeight: '600' },
  loader: { marginTop: spacing.lg },
});
