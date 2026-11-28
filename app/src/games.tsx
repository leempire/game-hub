import React from 'react';
import { Ionicons } from '@expo/vector-icons';
import {
  GomokuIcon, MineIcon, ReversiIcon, BattleshipIcon, DotsIcon, LudoIcon,
  Game2048Icon, XiangqiIcon, ConnectFourIcon, TetrisIcon, UnoIcon, StasisIcon,
  LuzhanqiIcon, SpiderIcon, UltimateTicTacToeIcon, AbyssIcon, UndercoverIcon, DoudizhuIcon, GuandanIcon, FishIcon, ConcentrationIcon,
} from './icons';

/**
 * 游戏目录 —— **游戏名的唯一来源**。
 *
 * 在这之前，同一个游戏的名字散在**七处**：`GamesTab`（卡片标题）、`RoomsTab`
 * （建房芯片 + 房间卡片徽标）、`navigation.tsx`（对局页标题）、`RoomScreen`
 * （游戏类型行）、`HistoryScreen.GAME_LABELS`（战绩列表）、`docs/使用指南.md`、
 * `docs/接口协议.md`。**漏一处就是同一个游戏两个名字** —— 2048 当初正是这样：
 * 卡片 / 导航 / 战绩叫「2048 对战」，帮助标题与文档叫「2048 竞速」。
 *
 * 现在客户端这一侧只剩这一份（两份文档仍要手工同步，但那是给人看的说明，
 * 不是会漂移的代码）。**加游戏 = 在这里加一行**（另外三处：`icons.tsx` 的图标、
 * `help.tsx` 的帮助文案、服务端 `create_room` 的白名单）。
 */

/** 服务端的 `game` 值。帮助系统 `GAME_HELP` 也拿它当键（少一个游戏会编译不过）。 */
export type GameKey =
  | 'gomoku' | 'minesweeper' | 'reversi' | 'battleship' | 'dots' | 'ludo'
  | '2048' | 'xiangqi' | 'connect4' | 'tetris' | 'uno' | 'stasis' | 'luzhanqi' | 'spider' | 'uttt' | 'abyss'
  | 'undercover' | 'doudizhu' | 'guandan' | 'fish' | 'concentration';

export type GameCategoryKey = 'board' | 'table' | 'solo' | 'coop' | 'single';

export interface GameEntry {
  key: GameKey;
  /** **全名**。主页卡片、对局页标题、房间页顶栏、战绩列表都用它。 */
  title: string;
  /**
   * **短名**。只给「一行排不下、必须压到两三个字」的地方用：建房芯片、
   * 房间列表的徽标胶囊。与 `title` 相同的游戏就不必另起一个名字。
   */
  short: string;
  /** 一句话说明「这游戏是什么」，**不写「这版改了什么」**（见 CLAUDE.md）。 */
  desc: string;
  /** 徽标只放人数 + 玩法，**不写「可人机」**（十个游戏都支持，标了反而不一致）。 */
  badge: string;
  category: GameCategoryKey;
  /** 建房芯片与房间徽标上的 Ionicons 图标名（`icons.tsx` 那套矢量图是给主页大卡片用的）。
   *  类型直接借 Ionicons 自己的联合类型 —— 写错一个图标名当场编译不过。 */
  ionicon: React.ComponentProps<typeof Ionicons>['name'];
  Icon: ({ size }: { size?: number }) => React.ReactElement;
}

/** 分类顺序与说明（v1.16）。按**玩法形态**分，不按题材分 —— 找游戏时想的是
 *  「我要一局棋」/「想拉几个人围一桌」，而不是「我想玩个海上的」。 */
export const GAME_CATEGORIES: Array<{ key: GameCategoryKey; name: string; blurb: string }> = [
  { key: 'board', name: '棋类对战', blurb: '两人轮流落子，一局定胜负' },
  { key: 'table', name: '多人桌游', blurb: '2–8 人围一桌，出牌、掷骰或推理' },
  { key: 'solo', name: '各玩各的', blurb: '同时开打，各自算成绩' },
  { key: 'coop', name: '合作', blurb: '一起扛同一局，输赢同担' },
];

export const GAMES: GameEntry[] = [
  { key: 'gomoku', title: '五子棋', short: '五子棋', desc: '经典对战，先连五子者胜',
    badge: '2 人 · 对战', category: 'board', ionicon: 'ellipse', Icon: GomokuIcon },
  { key: 'xiangqi', title: '中国象棋', short: '中国象棋', desc: '车马炮相仕帅，将死对方主帅',
    badge: '2 人 · 对战', category: 'board', ionicon: 'shield-half', Icon: XiangqiIcon },
  { key: 'luzhanqi', title: '军棋', short: '军棋', desc: '司令军长工兵地雷，明棋或暗棋夺军旗',
    badge: '2 人 · 对战', category: 'board', ionicon: 'flag', Icon: LuzhanqiIcon },
  { key: 'reversi', title: '翻转棋', short: '翻转棋', desc: '经典奥赛罗，夹住翻转对方棋子',
    badge: '2 人 · 对战', category: 'board', ionicon: 'disc', Icon: ReversiIcon },
  { key: 'connect4', title: '重力四子棋', short: '重力四子棋', desc: '选列落子，先连四子者胜',
    badge: '2 人 · 对战', category: 'board', ionicon: 'apps', Icon: ConnectFourIcon },
  { key: 'uttt', title: '终极井字棋', short: '终极井字棋', desc: '落子指定对手战场，赢下三盘连成一线',
    badge: '2 人 · 对战', category: 'board', ionicon: 'apps-outline', Icon: UltimateTicTacToeIcon },
  { key: 'dots', title: '点格棋', short: '点格棋', desc: '画边封格，格子多者胜',
    badge: '2 人 · 对战', category: 'board', ionicon: 'git-network-outline', Icon: DotsIcon },
  { key: 'battleship', title: '海战棋', short: '海战棋', desc: '布阵炮击，击沉对方全部舰船',
    badge: '2 人 · 对战', category: 'board', ionicon: 'boat', Icon: BattleshipIcon },

  { key: 'uno', title: 'UNO 卡牌', short: 'UNO', desc: '经典卡牌对战，先出完手牌者胜',
    badge: '2–4 人 · 轮流行动', category: 'table', ionicon: 'albums', Icon: UnoIcon },
  { key: 'doudizhu', title: '斗地主', short: '斗地主', desc: '叫分定地主，两名农民联手争先出完手牌',
    badge: '3 人 · 轮流出牌', category: 'table', ionicon: 'layers-outline', Icon: DoudizhuIcon },
  { key: 'guandan', title: '掼蛋', short: '掼蛋', desc: '四人两副牌，和对家配合升级过 A',
    badge: '4 人 · 组队出牌', category: 'table', ionicon: 'copy-outline', Icon: GuandanIcon },
  { key: 'concentration', title: '精神衰弱', short: '精神衰弱', desc: '记住牌的位置，翻出相同点数的扑克牌',
    badge: '2–4 人 · 记忆配对', category: 'table', ionicon: 'grid-outline', Icon: ConcentrationIcon },
  { key: 'ludo', title: '飞行棋', short: '飞行棋', desc: '掷骰起飞，撞敌回基地抢先终点',
    badge: '2–4 人 · 对战', category: 'table', ionicon: 'airplane', Icon: LudoIcon },
  { key: 'undercover', title: '谁是卧底', short: '谁是卧底', desc: '偷偷看词，轮流给线索，投票找卧底',
    badge: '4–8 人 · AI 人机', category: 'table', ionicon: 'search-outline', Icon: UndercoverIcon },

  { key: '2048', title: '2048 竞速', short: '2048', desc: '同序列公平竞速，比谁先合成 2048',
    badge: '1–4 人 · 竞速', category: 'solo', ionicon: 'calculator-outline', Icon: Game2048Icon },
  { key: 'tetris', title: '俄罗斯方块对战', short: '俄罗斯方块',
    desc: '同序列公平竞速，消多行给对手顶攻击行',
    badge: '1–4 人 · 竞速', category: 'solo', ionicon: 'cube', Icon: TetrisIcon },
  { key: 'minesweeper', title: '扫雷', short: '扫雷', desc: '同盘竞速排雷，比谁挖得多',
    badge: '1–4 人 · 竞速', category: 'solo', ionicon: 'warning-outline', Icon: MineIcon },
  { key: 'spider', title: '蜘蛛纸牌', short: '蜘蛛纸牌', desc: '各自整理黑桃顺牌，收齐八组 K 到 A',
    badge: '1–4 人 · 独立牌桌', category: 'solo', ionicon: 'layers', Icon: SpiderIcon },

  { key: 'fish', title: '潮汐鱼群', short: '潮汐鱼群', desc: '同游一片海，吞小鱼、躲大鱼，比谁长得快',
    badge: '1–4 人 · 同图竞速', category: 'solo', ionicon: 'fish-outline', Icon: FishIcon },
  { key: 'abyss', title: '深渊回声', short: '深渊回声', desc: '潜入沉没回收站，合力打退深海怪潮',
    badge: '1–4 人 · 合作', category: 'coop', ionicon: 'water-outline', Icon: AbyssIcon },
  { key: 'stasis', title: '深空休眠', short: '深空休眠', desc: '实时合作守舱，打死静默体',
    badge: '2–6 人 · 合作', category: 'coop', ionicon: 'planet', Icon: StasisIcon },
];

const BY_KEY: Record<string, GameEntry> = Object.fromEntries(GAMES.map((g) => [g.key, g]));

/** 全名。未知 key 原样返回 —— 服务端加了新游戏而客户端还没更新时，
 *  显示原始 key 总好过显示「五子棋」（旧代码的兜底正是这样，会张冠李戴）。 */
export function gameTitle(key: string): string {
  return BY_KEY[key]?.title ?? key;
}

/** 短名。同上，未知 key 退回全名。 */
export function gameShort(key: string): string {
  return BY_KEY[key]?.short ?? key;
}

/** 建房芯片与房间徽标上的 Ionicons 图标名。 */
export function gameIonicon(key: string): React.ComponentProps<typeof Ionicons>['name'] {
  return BY_KEY[key]?.ionicon ?? 'ellipse';
}

export const HOME_GAME_CATEGORIES: typeof GAME_CATEGORIES = [
  ...GAME_CATEGORIES,
  { key: 'single', name: '单机游戏', blurb: '一个人玩，随时续玩' },
];

export type HomeGameEntry = Omit<GameEntry, 'key'> & { key: GameKey | 'lighthouse' };

export const HOME_GAMES: HomeGameEntry[] = [
  ...GAMES,
  {
    key: 'lighthouse', title: '潮汐灯塔', short: '潮汐灯塔',
    desc: '修复海岛灯塔，经营群岛航线', badge: '1 人 · 放置经营',
    category: 'single', ionicon: 'boat-outline',
    Icon: ({ size = 44 }) => <Ionicons name="boat-outline" size={size} color="#b17b35" />,
  },
];
