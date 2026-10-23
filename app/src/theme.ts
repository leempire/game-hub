// Theme constants - clean modern style
//
// 改色板前必读 docs/移动端体验设计方法论.md 第 9 节：这里的每个颜色都要过 WCAG
// 对比度（正文 4.5:1，大字与图形 3:1）。**注释里的比值是实测值**，改色后必须重算，
// 别凭感觉挑色。饱和色同时充当「浅底上的文字」和「白字压在底色上」两种角色，
// 所以每一档都要求正反两个方向都达标——这就是 warning/success 比常见取值深的原因。
export const colors = {
  primary: '#4F46E5',       // indigo-600    白字 6.29 ｜ 浅底文字 6.29 ✓
  primaryLight: '#818CF8',  // 仅装饰（浅底 2.6，不可承载文字）
  primaryDark: '#3730A3',
  accent: '#047857',        // emerald-700   白字 5.48 ｜ 文字 5.48 ✓
  danger: '#DC2626',        // red-600       白字 4.83 ｜ 文字 4.83 ✓
  warning: '#B45309',       // amber-700     白字 5.02 ｜ 文字 5.02 ✓
  success: '#047857',       // emerald-700   白字 5.48 ｜ 文字 5.48 ✓
  bg: '#F9FAFB',
  card: '#FFFFFF',
  border: '#E5E7EB',        // 仅作分隔线/装饰（1.24:1）—— 不可作为控件的唯一边界
  borderStrong: '#8A90A0',  // 输入框等控件的可见边界 3.19 ✓
  text: '#111827',          // 17.74 ✓
  textSecondary: '#4B5563', // 7.56 ✓
  textMuted: '#6B7280',     // 4.83 ✓ —— 可承载信息的最低一档
  textFaint: '#9CA3AF',     // 2.54 ✗ —— **仅占位符/禁用态/纯装饰，禁止承载信息**
  white: '#FFFFFF',
  black: '#000000',
  overlay: 'rgba(0,0,0,0.5)',
  boardBg: '#DEB887',       // gomoku board
  boardLine: '#6B4F0C',     // 棋盘线 vs 木色 4.12 ✓（原 #8B6914 只有 2.74）
  stoneBlack: '#1a1a1a',    // vs 木色 9.38 ✓
  stoneWhite: '#f5f5f5',    // vs 木色 1.70 ✗ —— 必须靠 stoneEdge 描边，见 GomokuScreen
  stoneEdge: '#6B4F0C',     // 白子描边，与棋盘线同色（4.12 ✓）
};

/**
 * 五子棋的落点提示：**这一手直接连五**（实心点）/ **不堵就输**（空心环）。
 * 木色棋盘 `boardBg #DEB887` 上，绿只有 1.78、琥珀只有 0.87 —— **两个自己都不达标**，
 * 所以边界一律靠 `stoneEdge` 深墨描边成形（白子 1.70、飞行棋黄子 1.35 是同一条教训）。
 * 第二通道是形状：赢点实心、堵点空心，棋盘下方另有一行图例说明。
 */
export const gomokuHintWin = '#16A34A';
export const gomokuHintBlock = '#F59E0B';

/**
 * 玩家身份色（同一局的「谁是谁」）。
 *
 * **单一声明**：点格棋的蓝/红、2048 的分色点、海战棋的双方色都取这里，
 * 不要再各屏写死 hex——曾出现「引擎叫 black/white、界面画蓝红、文案写黑白」
 * 的三方错位（方法论 §8 命名一致性）。
 *
 * 颜色永远配第二通道（名次数字 / 座位方位 / 昵称），单靠颜色对色觉障碍不友好。
 */
export const playerColors: Record<string, string> = {
  blue: '#4F46E5',   // = primary
  red: '#DC2626',    // = danger
  green: '#047857',  // = success
  yellow: '#B45309', // = warning
};

/** 飞行棋四家棋色。黄子 vs 米色棋盘只有 1.30:1，必须靠深色描边成形。 */
export const ludoColors: Record<string, string> = {
  // 取值对齐标准飞行棋棋盘（tmp/飞行棋.md 视觉规格），vs 棋盘底 #F1F1F1：
  red: '#D8332B',    // 4.20 ✓
  blue: '#2E7FD6',   // 3.63 ✓
  green: '#2E9E44',  // 3.05 ✓
  // 黄子/黄格天然做不到 3:1——任何够亮的黄压在浅灰底上都只有 1.3~1.5:1，
  // 想达标就得暗成土黄，那就不再是棋盘上的那个黄了。所以这里的第二通道
  // **不是**加深底色，而是给每个格心的奶油圆加深色描边（见 LudoScreen 的
  // CREAM_EDGE）：格子边界靠圆环成形，颜色只负责区分是哪一色。
  yellow: '#F2D024', // 1.35 ✗ —— 依赖奶油圆的深色描边
};

/** 翻转棋棋盘绿（对局屏与战绩回放共用——两处曾各写一个绿：#2E7D32 / #157A46）。 */
export const reversiBoard = '#2E7D32';
/** 蜘蛛纸牌桌面；白字对比度 12.18:1（牌面沿用 colors.card / text）。 */
export const spiderTable = '#123C35';

/**
 * UNO 四色牌面。**与飞行棋同款 hex**（跨游戏同色系不该再写第二组值，
 * 项目踩过「同一个棋盘绿写成两个值」）。实测 vs 页面底 #F9FAFB：
 */
export const unoColors: Record<string, string> = {
  R: '#D8332B',  // 4.54 ✓
  G: '#2E9E44',  // 3.30 ✓
  B: '#2E7FD6',  // 3.92 ✓
  Y: '#F2D024',  // 1.45 ✗ —— 与 ludoColors.yellow 同一个坑：够亮的黄压在浅底上
                 // 永远到不了 3:1。这里**不靠加深底色**，靠 unoInk 的牌边成形
                 // （深墨 vs 黄 11.68 ✓），颜色只负责区分是哪一色。
};
/** 变色 / 变色+4 的牌面（黑牌）。vs 页面底 14.05 ✓ */
export const unoWild = '#1F2937';
/**
 * UNO 的深墨单色：**牌边 + 四色牌上的字形 + 可出牌外环**。
 *
 * 牌边 vs 各牌面：红 3.74 / 绿 5.14 / 蓝 4.33 / 黄 11.68 —— 全 ≥3 ✓。
 * 字形只用它：四色牌上深墨字全部达标，而**白字在黄牌上只有 1.52**（2048 黄格
 * 1.42:1 是同一个坑——深色字要配浅底，把底色调暗只会更低）。
 */
export const unoInk = '#111827';
/**
 * 黑牌上的字形 / 可出牌双环的内圈。
 *
 * 黑牌恰好相反：深墨字只有 1.21 ✗，白字 14.68 ✓ —— 所以**只有黑牌用白字**。
 * 双环的理由：没有任何单色环能在四色+黑牌上都可见（深墨压黑牌 1.21、
 * 白色压黄牌 1.52），内白外墨成对出现则任何底色上都至少有一环可见。
 * 另外可出牌还会**上移一档**——位移是不依赖颜色的第二通道。
 */
export const unoOnWild = '#FFFFFF';
/**
 * UNO 牌桌毡面 + 桌面上的文字色（**单一来源**，屏幕里不要写死 hex）。
 *
 * 桌面是深勃艮第——UNO 的品牌色本来就是深红，同时它也是本页唯一的「深色游戏面」
 * （海战棋深蓝 `battleshipWater`、俄罗斯方块深墨 `tetrisBoard`、四子棋深蓝
 * `connect4Frame` 都是同一个做法：**游戏面比页面暗，牌/格才跳得出来**）。
 *
 * 四色牌面实测 vs 毡底：红 3.55 / 绿 4.88 / 蓝 4.11 / 黄 11.08 —— **全 ≥3**，
 * 所以牌压在桌上不需要额外的成形通道（牌自己的深墨边仍在，那是为了在浅色页面
 * 背景上也成立）。
 *
 * **桌面上只能写浅色字**：白 16.83 ✓、unoOnTableMuted 11.98 ✓；
 * 深墨压毡底只有 1.13，一个字都看不清。
 */
export const unoTable = '#3A0D14';
export const unoOnTable = '#FFFFFF';
export const unoOnTableMuted = '#E8D5D8';  // 次要信息（张数 / 方向 / 提示）
/**
 * 深色毡面上的「谁是谁」身份色 —— `playerColors` 的**提亮一档**。
 *
 * 原色（`#4F46E5` 等）压在深勃艮第上只有 2.2~2.9，座位环会糊进桌面；
 * 提亮后蓝 8.44 / 红 8.87 / 绿 11.04 / 黄 13.52，都远超 4.5。
 * 与 `battleshipShipColors` 同一个理由：**同一身份在深色底上要专门配一档**，
 * 而不是另起一套语义。座位另有昵称与「我」标注当第二通道，颜色不单独承载信息。
 */
export const unoSeatColors: Record<string, string> = {
  blue: '#A5B4FC', red: '#FCA5A5', green: '#6EE7B7', yellow: '#FDE68A',
};
/**
 * 牌背的白色描边 + 倾斜红椭圆（UNO 牌背的招牌图案）。
 *
 * 牌背本体是 `unoWild`（`#1F2937`），压在深色毡面上只有 1.29——**牌背在桌上
 * 是「隐身」的，全靠这条白边成形**（vs 毡底 16.83 ✓，和五子棋白子靠描边、
 * 象棋棋子靠色环是同一条教训）。红椭圆 vs 牌背黑 3.10 ✓，是装饰性的辨识图案。
 */
export const unoBackEdge = '#FFFFFF';
/**
 * 活跃座位的浅色垫底（白 14% 压在毡面上）。
 *
 * **纯装饰**（vs 毡底 1.49:1，不达标也不承载信息）：「轮到谁」由身份色描边 +
 * 昵称变色 + 「出牌中」三个通道给出，这块垫底只是把视线引过去。
 */
export const unoSeatActiveBg = 'rgba(255,255,255,0.14)';

/**
 * 「最近一步」标记 —— 五子棋 / 翻转棋 / 点格棋 / 中国象棋**共用一套视觉语言**。
 *
 * 黄点 + 深墨描边。黄点单独用在这几个游戏的所有背景上都不达标（vs 白子 1.29:1、
 * vs 木色棋盘 ~1.9:1、vs 绿底 3.63:1），描边才是让它在任何底色上都成形的那条通道
 * ——和五子棋白子描边、象棋棋子色环是同一条教训。
 *
 * 象棋不能把标记画在棋子正中心（会盖住汉字），那边画成右上角的小方块，
 * 颜色与描边仍取这两个常量，保证「黄 = 最近一步」在四个游戏里含义一致。
 */
export const lastMoveMark = '#FDE047';
export const lastMoveMarkEdge = '#1F1F1F';

/** 点格棋的点与边线色（对局屏与回放共用）。 */
export const dotsInk = '#374151';

/**
 * 中国象棋棋盘与棋子色（**单一来源**：对局屏与战绩回放共用）。
 *
 * 棋子是「奶油圆盘 + 该方颜色的圆环 + 同色汉字」，圆环同时承担两个职责：
 * 一是传统形制，二是**棋子边界**——奶油盘面压在木色底上只有 1.36:1，
 * 光靠盘面根本看不出棋子在哪（和五子棋白子一个毛病）。所以红环 4.47:1、
 * 黑环 11.27:1 才是棋子的可见轮廓，改色时必须保住这两条。
 */
export const xiangqiBoard = '#EBD3A0';    // 木色棋盘底
export const xiangqiLine = '#6B4F0C';     // 棋盘线：vs 棋盘 5.22 ✓
export const xiangqiFace = '#FDF6E3';     // 棋子盘面（奶油色，1.36 ✗ 靠圆环成形）
export const xiangqiRed = '#B3261E';      // 红方：vs 棋盘 4.47 ｜ vs 棋子面 6.06 ✓
export const xiangqiBlack = '#1F1F1F';    // 黑方：vs 棋盘 11.27 ｜ vs 棋子面 15.28 ✓
/** 选中环 / 可落点 / 最后一步标记：棋盘 3.61 ｜ 棋子面 4.89，两处都 ✓ */
export const xiangqiAccent = '#0B6BCB';

/**
 * 军棋棋盘与棋子色（**单一来源**：对局屏与战绩回放共用）。
 *
 * 棋盘是一张木色底 + 三类线：**铁路用粗双线、公路用细线**（形状通道，不靠颜色
 * 区分——这两条线的颜色差只有 1.35:1，靠色相是分不出来的）。行营是圆圈、
 * 大本营是圆角方块、山界是双层折线、前线是虚线，全部靠**形状**承载地形信息。
 *
 * 棋子是「奶油小方块 + 深墨/红描边 + 汉字」，汉字本身就是信息通道（WCAG 1.4.1）。
 * 奶油盘面压在木色底上只有 1.30:1 —— 和象棋一样，**那圈描边才是棋子的轮廓**。
 * 暗棋下对手的棋子画成深色牌背 + 五角星，与「汉字正面」形成形状 + 文字双通道。
 */
export const luzhanqiBoard = '#E3D3AE';     // 木色棋盘底
export const luzhanqiRail = '#6B5B3E';      // 铁路（粗双线）：vs 棋盘 4.45 ✓
export const luzhanqiRoad = '#836E4A';      // 公路（细线）：vs 棋盘 3.30 ✓
export const luzhanqiCamp = '#D8C79C';      // 行营填充（1.13，纯装饰，成形靠描边）
export const luzhanqiHq = '#C9A96A';        // 大本营填充（装饰，成形靠描边）
export const luzhanqiFace = '#FDF6E3';      // 棋子盘面（1.30 ✗ 靠描边成形）
export const luzhanqiFaceEdge = '#1F1F1F';  // 棋子描边：vs 棋盘 11.14 ✓
export const luzhanqiRed = '#B3261E';       // 红方：vs 棋盘 4.42 ｜ vs 棋子面 6.06 ✓
export const luzhanqiBlack = '#1F1F1F';     // 黑方：vs 棋盘 11.14 ｜ vs 棋子面 15.28 ✓
export const luzhanqiBack = '#3F4756';      // 暗棋牌背：vs 棋盘 6.32 ✓
export const luzhanqiBackStar = '#D8DEE9';  // 牌背上的五角星：vs 牌背 6.92 ✓
/** 选中环 / 可落点 / 目标格高亮：vs 棋盘 3.57 ✓ */
export const luzhanqiAccent = '#0B6BCB';
/** 战报条里「我方阵亡」用 danger、「敌方阵亡」用 success，文字与底色都从这里取。 */

/**
 * 重力四子棋棋盘与棋子色（**单一来源**：对局屏与战绩回放共用）。
 *
 * 造型是「深蓝棋盘 + 浅色空位圆槽 + 圆盘棋子」。空位槽用**浅色**而不是经典
 * 四子棋的深色洞，是因为深洞会让黑盘彻底消失：深洞 `#0B1B3F` 与黑盘只有
 * 1.03:1，而浅槽 14.04:1。棋子因此不需要靠描边救场，只有白盘需要——
 * 白盘压在浅槽上仅 1.24:1，边界全靠那圈深墨盘沿（16.90:1）。
 */
export const connect4Frame = '#1E3A8A';   // 棋盘底（深蓝）
export const connect4Slot = '#DCE7FF';    // 空位圆槽：vs 棋盘底 8.34 ✓
export const connect4Black = '#151A24';   // 黑方盘：vs 空位槽 14.04 ✓
export const connect4White = '#FFFFFF';   // 白方盘：vs 空位槽 1.24 ✗ 靠盘沿成形
export const connect4DiscEdge = '#0B1B3F'; // 盘沿：vs 空位槽 13.61 ｜ vs 白盘 16.90 ✓
/** 提示标底：都是浅底配深墨字（14.6 / 14.2 ✓），且各自带文字标签当第二通道 */
export const connect4WinHint = '#BBF7D0';   // 「连四」：vs 棋盘底 8.55 ✓
export const connect4BlockHint = '#FDE68A'; // 「堵」：vs 棋盘底 8.32 ✓
/** 最后落子标记（黄点 + 深墨描边）：vs 黑盘 13.22 ✓ ｜ vs 白盘 1.32 ✗ 靠描边 */
export const connect4Last = '#FDE047';
/** 上一手的「连线」：只在棋子之间的空隙里露出来（压在圆槽之下），所以它的
 *  对比度是对着**棋盘底**算的 —— vs 深蓝底 5.74 ✓。刻意不用 `connect4WinHint`
 *  那支绿：绿色在这一屏已经等于「连四」，同一屏里一个颜色只能有一个含义。 */
export const connect4LineHint = '#93C5FD';

/**
 * 俄罗斯方块配色（**单一来源**：对局屏与战绩回放共用，不要再各写一份）。
 *
 * 盘面是深墨 `#111827`，七个方块的**填充色实测 vs 盘面 ≥4.48:1**（图形元素阈值
 * 3:1）：I 青 9.82 ｜ O 黄 11.58 ｜ T 紫 4.48 ｜ S 绿 7.79 ｜ Z 红 4.71 ｜ J 蓝 4.82
 * ｜ L 橙 6.33。攻击行灰块 `#6B7280` 3.67（它是障碍不是自己的块，刻意压暗）。
 *
 * **每格都描一圈墨边 `TETRIS_EDGE`**（vs 各填充色 3.87~12.23 ✓）：两个同色方块
 * 紧挨着（I 横条四格、两块 L 并排）不描边就糊成一大块，看不出「一格一格」——
 * 这是形状信息，不是装饰。
 *
 * 空位槽 `#1F2937`（vs 盘面 1.21）是**纯装饰**：空位不承载任何状态，块的位置
 * 由方块自身的亮色与墨边确定。**列靠 `tetrisGrid` 那道细线引出来**，不靠填充色
 * ——把空位填亮（试过）只会让整块盘面变成一块浅灰，格子之间依旧没有分界，
 * 「方块会落在第几列」还是看不出来。
 */
export const tetrisBoard = '#111827';
export const tetrisSlot = '#1F2937';
/**
 * 空位的格线（每格描 1px）。vs 盘面 1.71 —— 「看得出格子、又不跟落定的方块
 * 抢眼」的那一档（实测对比见 tmp/slot.py 那轮取值）。这条线把 10 列引出来，
 * 瞄准时不必只看幽灵框；它**不承载状态**，状态仍由方块亮色 + 墨边给出。
 */
export const tetrisGrid = '#334155';
export const tetrisEdge = '#0B1220';
export const tetrisPieceColors: Record<string, string> = {
  I: '#22D3EE', O: '#FACC15', T: '#A855F7', S: '#22C55E',
  Z: '#EF4444', J: '#3B82F6', L: '#F97316',
};
export const tetrisGarbage = '#6B7280';
/**
 * 落点幽灵框（虚线的描边色）。用亮一档的 `#64748B`（vs 盘面 3.73 ✓）而不是
 * 空位槽那种暗色——幽灵是**要看见的信息**（这一块会落在哪儿），暗色在深盘上
 * 根本看不出边框。虚线 + 空心底再补一层「不是实体块」的通道。
 */
export const tetrisGhost = '#64748B';
/**
 * 街机面板底色（HUD 与棋盘井都挂在它上面）——**单一来源**。
 *
 * 二十行的盘面很高，HUD 若散在页面浅底上会像一列调试读数；把「分数/行数/等级/
 * 被顶 + 下一块 + 棋盘」收进一块深色面板，整体才像一台游戏机（和 UNO 的牌桌、
 * 海战棋的水面同一个做法：**游戏面比页面暗**）。
 *
 * 实测 vs 井底 `tetrisBoard` 1.45（够看出井是凹进去的）、vs 页面底 8.24；
 * 面板上写白字 12.21 ✓、`tetrisOnPanelMuted` 8.22 ✓、更暗的 `#94A3B8` 4.76 ✓。
 * **下一块预览必须自己带一块 `tetrisBoard` 底**：紫 T / 红 Z / 蓝 J 直接压在
 * 面板上只有 2.1~2.3，达不到图形阈值 3:1。
 */
export const tetrisPanel = '#2A3648';
export const tetrisOnPanel = '#FFFFFF';
export const tetrisOnPanelMuted = '#CBD5E1';

/**
 * 海战棋船只配色（单一来源：对局屏与战绩回放共用），按舰种 1-5。
 *
 * 全部在深蓝棋盘 `#1E3A5F` 上 ≥4.1:1（原 #6366F1 只有 2.58:1，几乎沉进水里）。
 * 舰种还有「长度 + 舰名」作为第二通道，颜色只是辅助识别。
 */
export const battleshipShipColors: Record<number, string> = {
  1: '#60A5FA', 2: '#A78BFA', 3: '#34D399', 4: '#FBBF24', 5: '#F87171',
};

/** 海战棋盘底色（船色、格子都压在它上面）。 */
export const battleshipWater = '#1E3A5F';

/**
 * 深空休眠（stasis）配色 —— 暗色扁平矢量 + 科幻。
 *
 * **用「暗」造氛围，用「亮」传信息**：底子压暗，凡是承载信息的元素（气密门、
 * 静默体、建筑、资源）一律提亮；舱壁/格线/警戒光保持低对比，纯装饰。
 * 下面每个值都是实测过的（对比度口径同本文件其它调色板）。
 *
 * **v2 把地板整体提亮了一档**（`#1E2A3C` → `#2A3A50`，格线同步提亮）。原因：
 * v2 的地图是整层楼的俯视平面图，**「哪块是舱室、哪条是通道」全靠明暗关系读**，
 * 而旧值下地板与通道只差 **1.16:1**，屏幕上糊成一片（样张实测）。
 * 提亮后地板/通道 1.41、地板/深空 1.62。代价是压在地板上的元素对比度整体下降
 * 一档（哨戒炮 6.75→5.39、民用门 4.96→3.96、静默体 3.84→3.07），**仍全部 ≥3:1**。
 * 光靠明度还不够 —— 通道另有一层**暖色砖纹**（色相分开，原型也是暖褐砖 vs 冷灰瓷砖），
 * 舱室另有描边。三条通道叠加才读得出来。
 *
 * 四个实测踩过的坑（改色前先看）：
 * 1. **木门原候选 `#8B5E34` 只有 2.58:1**（vs 舱室地板），提亮两档到 `#C98A45`
 *    才达标 —— 暗色调下「深色元素压深色底」永远是对比度更低，不是更高。
 * 2. **三级门的相邻色差只有 1.16 / 1.76**，远低于 3:1 → **门等级不能只靠颜色区分**，
 *    必须配材质纹理（铆接/加强筋/发光镶边）+ 门上的等级数字 + HP 条（WCAG 1.4.1）。
 * 3. **格线原候选 `#334155` 只有 1.40:1**，格子根本看不出来；引格靠线不靠填充
 *    （同 tetris 的教训），提亮到 `#556A85`，v2 再提到 `#6B82A0`。
 * 4. **静默体 `#EF4444` 压在新地板上只有 3.07:1** —— 卡在图形阈值线上。
 *    它平时在通道里（4.31），但**它也会走到门口贴着脸**，所以另配了形状与
 *    「LV.n 静默体」文字作第二通道，颜色不单独承载信息。
 */
export const stasisSpace = '#0B1220';      // 页面底（深空黑）
export const stasisCorridor = '#17212F';   // 主通道：比舱室暗，通道是「危险区」
export const stasisFloor = '#2A3A50';      // 舱室地板（建筑图标压在这上面）
export const stasisGrid = '#6B82A0';       // 格线：vs 地板 2.93 ｜ vs 通道 4.11
/**
 * 舱壁：**一格厚**的实地墙体（用户要求「和猛鬼宿舍一样」）。
 *
 * 参考图里墙是「房间被从一整块深色砖体里挖出来」——地板格比周围亮，墙本身是
 * 更暗的砖。本作的地板已经压得很暗（`#2A3A50`，L≈0.041），再暗一档根本分不开
 * （纯黑的墙对地板也只有 1.82:1），所以这里**反过来**：墙比地板**亮**一档，
 * 读作「实心结构 / 不通行的金属舱壁」，通道（`#17212F`）仍是最暗的一层。
 * 明度阶梯变成 通道 < 地板 < 舱壁，三级都拉得开。
 *
 * 实测：**vs 地板 2.24 ｜ vs 通道 3.15 ｜ vs 深空 3.63**（图形阈值 3:1 对通道成立；
 * 对地板那一档靠**外框深描边**与**斜面高光**补足形状通道，同 tetris 的「引格靠线」）。
 *
 * ⚠️ 它和军用门 `stasisDoorMil` 只差 **1.52:1** —— 门不能只靠底色成形，
 * 所以门的深墨描边（`stasisDoorEdge` vs 墙 3.63）与门上的等级数字是必须的，别删。
 */
export const stasisWall = '#5E6E8A';
/** 舱壁斜面高光（上/左边）：做出一格厚的「砖块」立体感。 */
export const stasisWallBevel = '#8FA3BF';

/** 休眠舱舱体：vs 地板 12.31。**舱体上只能用深墨字**（白字压它只有 1.18）。 */
export const stasisPod = '#E8EDF5';
export const stasisInk = '#0B1220';        // 压在所有亮底上的深墨字

/**
 * 三级气密门（民用 → 军用 → 合金）。
 * 三级的**深墨字全部 ≥4.5**（6.42 / 5.52 / 9.70），所以文字色不需要按等级反色。
 */
export const stasisDoorCivil = '#C98A45';  // vs 地板 4.96
export const stasisDoorMil = '#7C8DA3';    // vs 地板 4.27
export const stasisDoorAlloy = '#AEBCCD';  // vs 地板 7.49
export const stasisDoorEdge = '#0B1220';   // 门框描边，让门在同色底上成形

/** 三种机器（vs 舱室地板全部 ≥5.4）。 */
export const stasisTurret = '#38BDF8';     // 哨戒炮 6.75
export const stasisRefinery = '#C084FC';   // 精炼机 5.47
export const stasisRepair = '#4ADE80';     // 纳米修复器 8.30

/**
 * 四座矿（v5：四个**独立的建造条目**，见 `增强调研.md` §10.1）。
 *
 * 四个色相是一档档推上去的（铜橙 → 银灰 → 金黄 → 钻青），**但颜色不是唯一的
 * 区分通道** —— 晶体的**数量与大小**同时随档位增长（3 → 3+1 → 4 → 5 根，
 * 且逐档变高），加上建筑面板上的名字，满足 WCAG 1.4.1。
 *
 * 实测 vs 舱室地板：4.71 / 7.68 / 6.91 / 8.30，全部 ≥4.5（正文阈值）。
 */
export const stasisCopper = '#E0954A';     // 铜矿 4.71
export const stasisSilver = '#C8D4E3';     // 银矿 7.68
export const stasisGold = '#FBBF24';       // 金矿 6.91（= 原 `stasisCulture` 的值）
export const stasisDiamond = '#8BE9F7';    // 钻石矿 8.30

/**
 * **控场四件**（v13，见 `增强调研.md` §16）。
 *
 * 四个色相刻意离已有的六种机器/矿远一点（炮台天蓝、精炼机紫、修复器绿、
 * 矿是橙/银/金/青）：力场玫红 / 束缚青绿 / 低温阱冰蓝 / 加速器品红。
 * **颜色同样不是唯一通道** —— 四张贴图的轮廓彼此完全不同（六边罩 / 双叉 /
 * 冰晶 / 环形轨道），加上建造菜单上的名字，满足 WCAG 1.4.1。
 *
 * 实测 vs 舱室地板：6.36 / 6.20 / 8.70 / 6.56，**全部 ≥4.5**（正文阈值）。
 * 低温阱的冰蓝与钻石矿的青（8.30）色相接近，但两者的形状差得远
 * （冰晶 vs 晶体簇），而且它们不会同时出现在同一间舱室的同一格上。
 */
export const stasisShield = '#F9A8D4';     // 应急力场 6.36
export const stasisBinder = '#2DD4BF';     // 束缚弹射器 6.20
export const stasisCryo = '#BAE6FD';       // 低温阱 8.70
export const stasisAccel = '#F0ABFC';      // 粒子加速器 6.56

/**
 * 弹道与命中反馈（v5 美术轮，见 `增强调研.md` §10.3）。
 *
 * 三个都是**瞬时元素**，压在深色地图上要够亮才抓得住眼睛：
 * 枪口焰 10.36 / 弹丸 9.27 / 冲击环用 `stasisTurret`（5.39）—— 全部远超图形阈值 3。
 * 它们**不承载任何状态**（伤害是连续 DPS，弹丸只是表现层），所以没有第二通道
 * 的必要，但也**不能**拿它们当「打中了」的唯一凭据：静默体的掉血与血条才是。
 */
export const stasisMuzzle = '#FEF3C7';     // 枪口焰 vs 地板 10.36
export const stasisTracer = '#FDE68A';     // 弹丸与拖尾 vs 地板 9.27

/** 静默体：本体 / 撤退再生态 / 过载态（三者都配了形状+文案的第二通道）。 */
export const stasisEntity = '#EF4444';         // vs 地板 3.84 ｜ vs 通道 4.45
export const stasisEntityRetreat = '#7DD3FC';  // 8.68
export const stasisEntityOverload = '#FB923C'; // 6.39

/** 两种资源（资源也走图标 + 数字，不靠颜色）。 */
export const stasisBio = '#FCD34D';        // 生物能 10.03
export const stasisAlloy = '#67E8F9';      // 合金 9.98

/** 通道警戒光：vs 通道只有 1.37~2.11，**纯装饰** —— 没灯时静默体也必须看得见。 */
export const stasisAlert = '#FB923C';

/** 门血条三档（另有长度 + 数字作第二通道）。 */
export const stasisDoorOk = '#4ADE80';
export const stasisDoorWarn = '#FBBF24';
export const stasisDoorLow = '#F87171';

/**
 * 六名玩家的身份色（v2 从 4 人扩到 6 人）。
 *
 * 同 `unoSeatColors` 的理由：**同一身份在深色底上要专门配一档**，不能直接复用
 * `playerColors`（原色压在 `stasisSpace` 上只有 2.98~3.88）。这一档实测
 * vs 页面底 9.39 / 9.86 / 12.28 / 15.03 / 10.59 / 12.92，vs 舱室地板 5.79~9.27，
 * 全远超 4.5。身份另有昵称与「你」标注当第二通道，颜色不单独承载信息。
 */
export const stasisSeatColors: Record<string, string> = {
  blue: '#A5B4FC', red: '#FCA5A5', green: '#6EE7B7',
  yellow: '#FDE68A', purple: '#D8B4FE', cyan: '#67E8F9',
};

/**
 * 2048 格子配色（**单一来源**：对局屏与战绩回放共用，不要再各写一份）。
 *
 * 经典 2048 调色板是「近白字压在黄橙底上」——#f9f6f2 on #edcf72 只有 1.42:1，
 * 128 以上的数字基本看不清。这里改成**统一深墨字 + 把深橙格调亮**，
 * 全部格子 ≥3.6:1（格内数字是 24px 以上的粗体，按 WCAG 大字阈值 3:1 计）。
 * 超过 2048 的兜底格是深底，仍用浅字。
 */
export const G2048_INK = '#4A4239';
export const G2048_TILES: Record<number, string> = {
  0: '#cdc1b4', 2: '#eee4da', 4: '#ede0c8', 8: '#f2b179', 16: '#f59563',
  32: '#f67c5f', 64: '#f87555', 128: '#edcf72', 256: '#edcc61',
  512: '#edc850', 1024: '#edc53f', 2048: '#edc22e',
};

/** 取某数值格子的底色与字色（未收录的大数走深底浅字兜底）。 */
export function g2048Tile(value: number): { bg: string; text: string } {
  const bg = G2048_TILES[value];
  return bg ? { bg, text: G2048_INK } : { bg: '#3c3a32', text: '#f9f6f2' };
}

/**
 * 把 `#RRGGBB` 转成 `rgba(r,g,b,a)`。
 *
 * 用于「同一身份的色块要半透明」的场景（点格棋归属格、飞行棋基地底）——
 * 透明色直接写 rgba 字面量会绕过上面的单一来源，改一次主色就要满仓库找。
 */
export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 48,
};

export const radius = {
  sm: 6,
  md: 10,
  lg: 16,
  xl: 24,
  full: 9999,
};

export const fontSize = {
  xs: 12,
  sm: 14,
  md: 16,
  lg: 18,
  xl: 22,
  xxl: 28,
  title: 32,
};
