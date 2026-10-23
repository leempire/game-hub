"""深空休眠（stasis）引擎 —— 《猛鬼宿舍》式的 2-6 人合作塔防。

**纯逻辑，无 IO、不认识时间**：全部推进都走 `advance(gs, dt_ms, rng)` 的显式
`dt_ms`，不碰 asyncio、不读墙钟。三个收益见设计文档 §2：单测能毫秒级跑完整局、
热重启不需要恢复 tick 状态、全员离线时「暂停」就是不调 advance。

地图几何在 `stasis_map.py`（纯函数、无状态），本模块只持游戏状态。

# 与 v1 的区别

v1 是「单间舱室的抽象格子 + 环形通道上的一维索引」，没有空间。v2 按原型复刻成
**整层楼的俯视平面图**：25×30 格、12 间不规则舱室（1 格厚舱壁 + 5×4 内部）、
网格状通道、可操控的角色、
相机跟随与拖动。所以：
- 格子从抽象下标 `0..N-1` 变成 **`(x, y)` 坐标**，建筑键是 `"x,y"`
- 静默体从「环形索引 + 段内进度」变成 **(x, y) 连续坐标 + 通道网格 BFS 寻路**
- 玩家有了 **`pos`（浮点格子坐标）**，开局是「跑动抢舱室」而不是「点选舱室」
"""
from __future__ import annotations

import math
import random

from app.game import stasis_map as M

# ── 时间 ────────────────────────────────────────────────────

TICK_MS = 200                      # 5 Hz，与 room_manager 的 tick 循环一致
RUN_MS = 30 * 1000                 # 跑动抢舱室倒计时（原型是 20-30 秒）

# **一局没有时长上限**（v8 第二次改动，用户要求：「不要求 6 分钟内打死，时间不限」）。
#
# 这条推翻了同一天更早的 v8 第一版（那一版把 6 分钟从「胜利倒计时」改成「击杀
# 期限」）。用户的口径是「打到分出胜负为止」：**要么打死它，要么全员失压**。
#
# 不用怕打不完：静默体的等级**封顶在 `MAX_ENTITY_LEVEL`（10 级）**，7.5 分钟之后
# 它就不再变强，而玩家的经济与炮台是无限爬坡的 —— 所以僵持的局面**天然会朝玩家
# 倾斜**，最终一定分出胜负（前提是玩家在造炮台；一炮不造又守着满级门的组合才是
# 真正的死局，见下面 `STALL_CAP_MS`）。
#
# 顺带一提，这也更贴原型的**单局时长**：原作是 10~15 分钟一局（见 `增强调研.md`
# §9 的进度轴对照），6 分钟本来就是我们为「桌上对局」压短的。
GAME_MS = 6 * 60 * 1000            # ⚠️ **只是参考时长**，不再是胜负判据。
                                   # 客户端拿它画不了进度条了（没有终点），
                                   # 顶部那栏改成**正计时**。留着是因为
                                   # 「这一局大概多长」在文案与测试里还有用。

# **防卡死的兜底**（不是玩法规则）：引擎的终局条件只有「击杀」与「全员失压」两条，
# 万一出现「门强到它砸不破、炮台又弱到打不死它」的死局（比如玩家一炮不造、
# 只顾升门升床），这局就会永远跑下去 —— 而这是**本项目唯一的服务端 tick**，
# 一个永不结束的 tick 任务是要避免的。
#
# 30 分钟 ≈ 原型单局（10~15 分钟）的两倍，正常对局碰不到；碰到就说明确实僵住了。
STALL_CAP_MS = 30 * 60 * 1000

# ── 角色 ────────────────────────────────────────────────────

PLAYER_SPEED = 3.6                 # 格/秒。比静默体快 —— 跑得掉才有「躲进房间」这回事
PLAYER_MAX = 6
PLAYER_MIN = 2

# **走廊里被静默体抓到 = 直接出局**（v4 起）。
#
# 原来的三件套是「撞倒 + 掉生物能 + 击退」，那是 v2 为了不照抄原型而自创的
# （`增强调研.md` §6 不采纳 #1）。**v4 推翻了它**：用户要求「碰到鬼直接死」，
# 而且「躺下后不能起身」落地之后，对局阶段走廊里本来就不该有人 —— 于是
# 「走廊接触」唯一可能发生的场合是**倒计时结束时还没躺下的人**。
# 所以这条规则不再是「开局随机暴毙」，而是**「倒计时结束前必须躺下」的执法者**。
# 完整论证见 `docs/游戏设计/深空休眠/增强调研.md` §9.4（含代价）。
ENTITY_CATCH_DIST = 1.0            # 靠到这个距离内就抓住（格）

# ── 资源 ────────────────────────────────────────────────────

START_BIO = 30.0                   # 开局资金。对齐原型「鬼出来前固定给 30 金币」

# ── 建筑种类 ────────────────────────────────────────────────

TURRET = "turret"
REFINERY = "refinery"
REPAIR = "repair"
# **四座矿是四个独立的建筑**（v5，用户要求「培养槽改成建造菜单里四个独立条目」）。
# 原先是一个 `culture` 建筑带一条 3 级的升级链，升级价恰好是 1024/2048/4096 ——
# 也就是银/金/钻石矿的**放置价**。原作的四种矿在道具列表里本来就是四个并列条目
# （各自写「放置需要 X 闪电」，见 `增强调研.md` §10.1），所以拆开才是对齐。
#
# **升级链删除是有意的**：原作两种口径都成立（既能原地升级、也能直接放置），
# 但两条路并存时升级链恒为劣选（128+1024 > 1024），留着就是一份永远不该点的 UI。
# 拆开之后高级矿反而更便宜（走完链到 512/秒 要 7296 合金，直接放钻石矿只要 4096），
# 所以这次拆分不会把游戏改难。
COPPER = "copper"
SILVER = "silver"
GOLD = "gold"
DIAMOND = "diamond"
MINES = (COPPER, SILVER, GOLD, DIAMOND)

# **控场道具四件**（v13，`增强调研.md` §11.8「原型的控场道具不在本轮范围」的落地）。
# 它们是「门顶不住之后」的第二层防御：不打伤害，改的是**静默体的行为**或
# **炮台的输出效率**。四件在原作里都挂在「高科技商店」，用闪电（=本作的合金）建。
#
#   能量罩     → 应急力场   门血 < 30% 时开一个 3 秒的无敌护盾
#   符咒弹射器 → 束缚弹射器 静默体放技能时 30% 概率把它定住 3 秒
#   冰箱       → 低温阱     降低静默体 20% 攻速
#   粒子加速器 → 粒子加速器 炮台攻速 +50%
#
# 四条效果的数值来自原作攻略（§16.1），**名字全部是本作自己的**（v7 起界面上
# 不许出现原版名称）。只有「粒子加速器」沿用了原名 —— 它本来就是中性的物理词。
SHIELD = "shield"
BINDER = "binder"
CRYO = "cryo"
ACCEL = "accelerator"
CONTROLS = (SHIELD, BINDER, CRYO, ACCEL)
BUILDABLE = (TURRET, REFINERY, REPAIR) + MINES + CONTROLS

# 升级动作的两个特殊目标（不是"格上的建筑"，而是舱室自带的两个东西）
BED = "bed"
DOOR = "door"

# 建造费（资源名, 数量）。**只有舱室主人能在自己舱室里造** —— 见 design §1.4。
#
# 七个数字来自原型的实测资料（`增强调研.md` §9.1 / §10.1）：炮台 8 金币、
# 游戏机 200、维修台 64 闪电、四座矿 128 闪电起。
# **升级用的资源与建造同一个**（所以矿升级花的是合金 —— 原型里铜矿要的是闪电）。
#
# ⚠️ **四座矿的价格 v7 下调了**（用户实测反馈）：银/金/钻 1024/2048/4096 →
# **512/1024/2048**。原型的四个数（4 条来源一致）是 128/1024/2048/4096，但本作
# 一局原本只有 **6 分钟**（v8 起不限时，但合金产量的量级没变），远低于原型的
# 十几分钟局 —— 4096 的钻石矿在实测里
# 根本够不着，等于菜单上挂着一个永远点不了的条目。下调之后四个矿是
# 128 / 512 / 1024 / 2048，用户报的就是这三个数，**铜矿保持来源里的 128 不动**。
# ⚠️ **不要为了「四个矿凑成一条 ×2 链」把铜矿改成 256** —— 没有任何来源支持 256，
# 四条来源一致写的是 128。用户说的「每次 ×2」指的是**门**，不是矿。见 §11.3。
#
# ⚠️ **v10 用户又改了一轮**（四个矿的价钱与产率见下表，理由与来源对照见 §13）：
# 钻石矿 2048 → **4096**（回到原作口径）；四个矿的**产率**也重排了一遍
# （8 / 32 / 64 / 256，钻石矿再花 4096 合金升成氪金矿 → 512）。**铜矿与银矿
# 一个字没动**（128→8 / 512→32 从 v7 起就是用户拍板的数）。
BUILD_COST = {
    TURRET: ("bio", 8.0),
    REFINERY: ("bio", 200.0),
    REPAIR: ("alloy", 64.0),
    COPPER: ("alloy", 128.0),
    SILVER: ("alloy", 512.0),
    GOLD: ("alloy", 1024.0),
    DIAMOND: ("alloy", 4096.0),
    # 控场四件（v13）：价钱照抄原作的闪电价（§16.1），**全部走合金** ——
    # 与纳米修复器同一个池子。这不是随手挑的：合金的唯一来源是精炼机，
    # 所以「造控场还是造矿」天然是一道选择题，而控场道具正是**中期**（精炼机
    # 刚起来、炮台还没成规模）最想要的东西。
    SHIELD: ("alloy", 64.0),
    BINDER: ("alloy", 64.0),
    CRYO: ("alloy", 256.0),
    ACCEL: ("alloy", 2048.0),
}
# 名字**逐级**取（v10）：钻石矿升一级就是「氪金矿」，所以名字不能只按 kind 查。
BUILD_NAMES = {
    TURRET: "哨戒炮", REFINERY: "精炼机", REPAIR: "纳米修复器",
    COPPER: "铜矿", SILVER: "银矿", GOLD: "金矿", DIAMOND: "钻石矿",
    # 控场四件的名字**全是本作自己的**（原作叫能量罩 / 符咒弹射器 / 冰箱 /
    # 粒子加速器）。只有「粒子加速器」沿用原名 —— 它是中性的物理词，
    # 不是原版的专名（v7「界面上不许出现原版名称」那条管的是后者）。
    SHIELD: "应急力场", BINDER: "束缚弹射器", CRYO: "低温阱", ACCEL: "粒子加速器",
}
# 每级的名字（缺省＝这一级还用 `BUILD_NAMES` 那个名字）。
# **「氪金矿」这个名字来自用户**（原作攻略里查不到，见 §13.3 的说明）——
# 界面上是与不是都由这一处决定，别在别处再写一遍。
BUILD_NAMES_BY_LEVEL = {
    DIAMOND: {1: "钻石矿", 2: "氪金矿"},
}


def building_name(kind: str, level: int = 1) -> str:
    """建筑的展示名。逐级的名字查 `BUILD_NAMES_BY_LEVEL`，其余回落到 kind 名。"""
    by_level = BUILD_NAMES_BY_LEVEL.get(kind)
    if by_level and level in by_level:
        return by_level[level]
    return BUILD_NAMES.get(kind, kind)
REPAIR_RATE = 0.02                 # 每秒回门上限的 2%（原型：无距离限制，同）

# **纳米修复器每间舱室最多 4 台**（v7 补上，原型规则：「维修台每局只能建四个」）。
#
# 这条不加的话「门的血比原作多」根本修不掉：修复是按**门血上限的百分比**回的，
# 门越高级回得越多，而 20 格的舱室能摆下十几台 —— 十几台 = 每秒回 20%+ 上限，
# 静默体的伤害再怎么调都追不上。上限 4 台 = 最高 8%/秒，正好是原型里
# 「12 级鬼和 3 个维修台的回血持平」那条比例的分母。
MAX_REPAIRS = 4

# ── 控场四件的数值（v13；四条效果照抄原作，见 增强调研.md §16.1） ──────
#
# ⚠️ **上限是每间舱室**（同 `MAX_REPAIRS` 的口径）。原作只给了价钱与效果，
# 没说能建几台 —— 这里按「叠起来会不会破坏机制」各定一个：
# 应急力场与束缚弹射器是**布尔式**的效果（有/没有），建第二台毫无意义，
# 所以上限 1；低温阱是乘算减速（叠三台 = ×0.51），给 3 台；
# 粒子加速器是乘算增伤（叠两台就 ×2），上限 1。
#
# **应急力场的冷却（`SHIELD_CD_MS`）是本作自加的**：原作只说「门血 < 30% 时
# 开 3 秒无敌」，没提冷却 —— 照抄的话它就是一扇永远打不破的门（无敌一结束
# 血还在 30% 以下，立刻再开）。冷却取 60 秒：一场围门最多开一次，
# 定位是「门快破的那一刻争取 3 秒」，而不是持续免疫。
SHIELD_TRIGGER = 0.30              # 门血低于此比例才触发（原作 30%）
SHIELD_MS = 3000                   # 无敌时长（原作 3 秒）
SHIELD_CD_MS = 60 * 1000           # 冷却（本作自加，理由见上）
CRYO_SLOW = 0.20                   # 每台降低静默体 20% 攻速（原作 20%）
BINDER_CHANCE = 0.30               # 每次技能 30% 概率（原作 30%）
BINDER_MS = 3000                   # 定身 3 秒（原作 3 秒）
ACCEL_BOOST = 0.50                 # 炮台攻速 +50%（原作 50%）

# 每间舱室的建造上限（不在表里的按「只受格子数限制」）。
ROOM_LIMITS = {
    REPAIR: MAX_REPAIRS,
    SHIELD: 1,
    BINDER: 1,
    CRYO: 3,
    ACCEL: 1,
}

DEMOLISH_REFUND = 0.5

# ── 逐级数值表（**v4 起对齐原型，v7 起逐级价一律 ×2**，来源见 增强调研.md §9.1 / §11）
#
# 两套资源的翻译：原型的 `金币` → 本作 `生物能`，原型的 `闪电` → 本作 `合金`。
# **数字一律照抄，不按一局多长去压缩** —— 用户明确要求「每个等级的升级价格
# 都要和原作对齐」。本作只压缩**级数**（每张表只保留一局内够得着的那几级）。
#
# ⚠️ **门这一列 v7 起是纯 ×2 链**（用户实测报的：「升级门的价格应该是每次 *2，
# 但我玩着玩着就不是 2 的倍数了」）。旧表里的 `65` 与 `216` 都在这一列上破坏了
# 翻倍：`65` 是 32 的取整写法（×2.03），`216` 更离谱（128 → 216 只有 ×1.69）——
# 而两条独立来源写的是「累计生产 **128 加 256** 金币用于门的升级」「先建 **128**
# 金币门…最后开启 **256** 金币门」，即 128 之后就是 256。所以 `216` 是错的，
# 整列改成 `16 × 2^n`。见 `增强调研.md` §11.1。
#
# ⚠️ **从第 6 级起升级还要搭合金**（`upgrade_alloy`，v7）：原型的「升钢门时必须
# 有四个游戏机，电量不够」说的就是这件事 —— 铁门 1-5 只吃金币，钢门往上要金币
# **加**闪电。见 `增强调研.md` §11.2。
#
# ⚠️ 取值口径：**下标 = 当前等级 - 1**（`[0]` 是 1→2 级要花的钱）。
#
# 表首曾经有过一个占位 0 而代码取 `[lv]`，v2 重写时改成取 `[lv-1]` 却忘了删占位，
# 于是**每种建筑的第一次升级都免费**（起手式白送三次升级）。现在两张口径统一。
UPGRADE_COST = {
    BED: [25.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0],
    DOOR: [16.0, 32.0, 64.0, 128.0, 256.0, 512.0, 1024.0, 2048.0, 4096.0,
           8192.0, 16384.0, 32768.0],
    TURRET: [16.0, 40.0, 88.0, 128.0, 256.0, 512.0, 1024.0, 2048.0, 4096.0,
             8192.0, 16384.0, 32768.0],
    # **精炼机 v10 起满级 10 级**（用户要求：1 级 0.5 合金/秒 … 10 级 30 合金/秒），
    # 价钱照旧**每次翻倍**（400 × 2^n）。原作的逐级价查不到（只有 1→2 级那 400
    # 有来源），所以「每次翻倍」是用户定的规则，不是抄来的。
    REFINERY: [400.0, 800.0, 1600.0, 3200.0, 6400.0, 12800.0, 25600.0, 51200.0,
               102400.0],
    # **钻石矿有一条升级档**（v10，用户要求）：花 4096 合金升成「氪金矿」，
    # 秒产再翻一倍。**花的是合金不是生物能** —— 见 `UPGRADE_RES`。
    #
    # ⚠️ 四座矿里**只有它在这张表里**：铜/银/金仍是「一个等级、想换更快的就
    # 拆了重造」（v5 的拆分理由见上）。`upgrade()` 对不在表里的 kind 照旧回
    # 「这种建筑不能升级」。
    DIAMOND: [4096.0],
}
# 升级**主资源**（缺省是生物能）。矿这一族升级掏的是合金 —— 与它们的建造费
# 同一个池子（`BUILD_COST[MINE] = ("alloy", …)`），这样「造矿还是升矿」才是一道
# 真选择题：两边的钱是同一份。第 6 级起那份 `upgrade_alloy` 才是额外要搭的。
UPGRADE_RES = {DIAMOND: "alloy"}
# **从第 6 级起，升级还要搭合金**（v7）。原型的规则是「铁门 1-5 只吃金币，进钢门
# 之后要金币**加**闪电」——攻略原话「**在升钢门时务必要有四个游戏机，电量不够**」。
# 本作的休眠舱与哨戒炮没有「铁门/钢门」这种分段，所以三样统一取**第 6 级**这条线
# （用户报的也是「到一定等级后门、炮台、床铺升级都要合金」）。
#
# 比例取 **1/32**：来源里那扇「26 万金币 8 千闪电」的门，两个数都是 2 的幂
# （131072 / 4096），比值正好 32。见 `增强调研.md` §11.2。
ALLOY_FROM_LEVEL = 6               # 升到这一级（含）起，还要搭合金
ALLOY_RATIO = 32.0                 # 合金 = 生物能 / 32

# 炮台的前四个升级价是玩家**列算式反推**出来的：`8+16+40+88+128 = 280` 是「一枚
# 64 攻炮的总投入」，与「两枚 32 攻的炮共 152×2 = 304」对照 —— 见 §9.2。所以：
# 4 攻 = 8（建造）、8 攻 = 16、16 攻 = 40、32 攻 = 88、64 攻 = 128。
# **第 6 级往后是照「造价翻倍」外推的**（原型「炮台最高升到 13 级」有来源，
# 逐级价没有）。
#
# 三张长表的上限都有来源（§9.2）：**床满级 512/秒（10 级）、门最高振金门 3
# （13 级）、炮台最高 13 级**。

BED_RATE = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0]
# 门耐久：**规则（每级翻倍）来自原型，基数是我们自己的**（§9.2 说清了：原型
# 只公布了「每级血量翻倍」这条规则，绝对数值查不到）。基数取 375 是为了让
# **1-5 级沿用 v2 调平衡时定下的 [750, 1500, 3000, 6000]**，只把翻倍规则往
# 上、下各接：往下补出 1 级，往上接到 13 级（振金门 3）。
#
# ⚠️ **基数本身不影响手感**：门的威胁度只取决于 `DOOR_HP / ENTITY_DPS` 这个
# **比值**（修补与修复器都是按上限的百分比回血，与绝对值无关）。所以 v7 要
# 「门别那么肉」时改的是 `ENTITY_DPS`，不是这里 —— 见 §11.4。
DOOR_HP = [375.0 * 2 ** i for i in range(13)]
TURRET_DPS = [4.0 * 2 ** i for i in range(13)]
# 精炼机的合金产率（**下标 = 等级 - 1**）。
#
# ⚠️ **v10 起满级 10 级**（用户要求）：1 级 0.5、2 级 2、3~6 级每级 **+2**、
# 7~10 级每级 **+5** —— 所以 6 级 10/秒、10 级 30/秒。**这是一条加法曲线，不是
# 翻倍曲线**，而它恰好与原作口径一致（联网调研的 3 条来源都写「每次升级游戏机
# 收益 +2」，见 `增强调研.md` §13.2）——v10 之前本作从 2 级起走的是翻倍（4/8/16），
# 与原作不符，这一版顺手改回加法。
REFINERY_RATE = [0.5, 2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0, 25.0, 30.0]  # 合金/秒
# 四座矿的生物能产率：**按 kind + 等级取**（v10 起钻石矿有两个等级 —— 升成
# 氪金矿后翻倍；其余三个矿表里只有一项，永远取到那一项）。
#
# 原型的四个数（8 条来源一致 + 1 条玩家实测）是 8 / 32 / 128 / 512 金币每秒，
# **本作 v10 取 8 / 32 / 64 / 256（+ 氪金矿 512）** —— 用户指定，理由见 §13.1：
# 本作一局没有原作的十几分钟，而且第 6 级之后合金是硬门槛，金币不该再像原作
# 那样白白翻四倍。**铜矿与银矿保持原作的 8 / 32 不动。**
MINE_RATE = {
    COPPER: [8.0],
    SILVER: [32.0],
    GOLD: [64.0],
    DIAMOND: [256.0, 512.0],
}


def mine_rate(kind: str, level: int) -> float:
    """矿的产率（生物能/秒）。**按等级取** —— 钻石矿 2 级就是氪金矿。"""
    table = MINE_RATE[kind]
    return table[min(level, len(table)) - 1]

# 门的名字（原型：铁门 1-5 → 钢门 1-5 → 振金门 1-3，共 13 级）
DOOR_TIER = ["铁门", "钢门", "振金门"]

# **升到床 Lv N 需要的最低门等级。** 原型这条门槛是**间断的**，不是「床每升一级
# 门就要 +1」（用户实测后专门指出来的）。四个锚点来自攻略原文（§9.2）：
#
#   门 2 → 床 ≤4（8/秒）   「铁门2顶到8床」[64]、「升级16门后的两次升级50、100床」[60]
#   门 5 → 床 ≤6（32/秒）  「升到铁门5后你可以把床顶到32」[64]、「铁门5，床+16」[67]
#   门 8 → 床 ≤7（64/秒）  「将门升级为三级钢门。床升至+64」[67]
#   门 13 → 床 ≤10（512）  「门的最高等级为三级振金门，床可升至10级即提升512点」[67]
#
# **中间几级（8/9/11）是在锚点之间插值的**，查不到逐级表 —— 单调、且四个锚点
# 全中。改这张表之前先确认锚点没有被新的调研推翻。
BED_DOOR_REQ = {2: 1, 3: 2, 4: 2, 5: 5, 6: 5, 7: 8, 8: 9, 9: 11, 10: 13}

# 炮台射程（格，欧氏）。**基数 4 来自原型**（「攻击距离是 4」），每级 +0.5
# （原型只说「略加一些攻击范围」，增量是我们的取法）。
#
# 为什么射程要长起来：本作舱室内部 5×4、门开在长边的正中，所以**放在对角角落的
# 塔到门口有 6 格以上**。射程 4 时角落的塔打不到门，5 时刚好够 —— 「塔放哪儿」
# 因此从第一级起就是真决策（同原型的「凹形房最合适」）。
#
# **6.0 封顶（原型没有这个封顶）**：本作的楼层比原型密（25×30 格、房间挨着通道），
# 再往上加射程就会有塔隔着通道打到隔壁舱室的门，把「摆位」这件事抹平。封顶是
# 本作对有 13 级炮台这条长梯的适配，不是原型数值。
TURRET_RANGE_BY_LEVEL = [min(4.0 + 0.5 * (lv - 1), 6.0) for lv in range(1, 14)]

# 修补气密门：修自己 20 生物能，修队友 ×1.5（救队友永远比救自己亏，所以是个取舍）
PATCH_COST_BIO = 20.0
PATCH_COST_MULT = 1.5
PATCH_FRACTION = 0.25

# ── 静默体 ──────────────────────────────────────────────────

# 静默体每秒对门伤害。**v7 把基数与增长率一起抬了**（用户实测反馈：「门的血比
# 原作多，或者鬼的攻击力比原作少」）。
#
# **旧值（6/9/12/…/60，每级 ×1.3）就是那个 bug**：门血每级翻倍、鬼的伤害每级
# 只涨 1.3 倍，于是**越到后期门越无敌** —— 13 级门 1,536,000 血对上 10 级鬼的
# 60 DPS，一扇门要砸 7 小时。同级的门与鬼对峙时间：**旧 62.5 秒 → 新 37.5 秒**。
#
# 增长率取 ×1.5 是**扫出来的**，不是拍的 —— 判据是原型的「门比鬼高两级可以直接
# 无视鬼；高一级就需要输出凑」，而本作的静默体是**按仇恨值集火最弱的那扇门**
# （原型是随机挑一间），同样的比值在本作里更致命。
#
# ⚠️ **试过「两边都每级 ×2」（最贴近原型）—— 在当时不可行，已量过**：那一版
# 的静默体还有个每 45 秒白送一级的计时进化，一局送 7~8 级，×2 就是 ×128~256 的
# 伤害；而玩家的经济是指数爬坡、前期只有 1~2 生物能/秒，任何基数组合下 2/4/6
# 人档都是全灭（扫过 D∈{250,375} × G∈{6..24} × r=2 × H∈{500,1200} × evo∈{45,60,90,120}）。
# 完整过程与两张表见 `增强调研.md` §11.4。
#
# **v13 把计时进化删掉之后（见 `EVOLVE_ATTACK_MS`），那条结论的根因就没了** ——
# 等级现在由「砸掉几扇门」决定，玩家压得住它就长得慢。所以「×2 曲线」值得重测，
# 但那仍是一次独立的平衡改动，本轮没动（`ENTITY_DPS` 还是 ×1.5）。
#
# ⚠️ **基数 v8 从 10 抬到 12**（同一条用户反馈的后半句：「鬼的攻击力比原作少」，
# 而「时间不限」之后人机的胜率一度到 100/96/100 —— 没有失败面）。**32 局 × 3 档
# 扫出来的**（`test/balance_stasis.py`，判据是「均衡发展」这条打法）：
#
# | 基数 | 2 人 | 4 人 | 6 人 |
# |---|---|---|---|
# | 10（v7 旧值） | 100% | 96% | 100% |
# | **12（现值）** | **87%** | **81%** | **100%** |
# | 13 | 87% | 75% | 93% |
#
# 取 12：+20% 的门口压力，把 2/4 人档从「几乎必赢」拉到 81~87%，而**明显更差的
# 打法（只堆塔不升门 / 先贪床）仍在 9~31%**（见 `设计.md` §1.5.1 的 v8 表）——
# 难度梯度没有被压平。**`ENTITY_HP` 一个字没改**：抬血会让「打不死它」变成新的
# 失败模式，而本轮要的是「守不住」，不是「打不动」。
#
# 改这一列之前先跑 `test/balance_stasis.py`（**它是进版本库的**，v2 那版没进库，
# 后来连文档里那张表都复现不出来，见设计 §1.5.1）。
ENTITY_DPS = [12.0 * 1.5 ** i for i in range(10)]
# 静默体最大 HP（再乘人数系数）。**基数从 6000 砍到 600**（用户报的另一半：
# 「炮塔的攻击力偏低，或者鬼的血量偏高」）。旧值下 4 座 1 级炮台（16 DPS）要打
# **6 分钟**才把 1 级鬼打到撤退线 —— 炮台在手感上等于没有；新值约 28 秒。
#
# 基数的判据来自原型的攻略原话：「**一级鬼 2 个扫把炮台就可**」——2 座 1 级炮
# （8 DPS）要在二十几秒内打掉 75% 的血，解得 HP ≈ 200~400（×1.3~3.3 的人数
# 系数之后就是 600 这一档）。增长率与 `ENTITY_DPS` 同取 ×1.5，理由见上。
ENTITY_HP = [600.0 * 1.5 ** i for i in range(10)]
MAX_ENTITY_LEVEL = 10

# 人数系数：人多了静默体更肉（输出也多了），但**伤害不缩放** —— 多一个人就多一扇门
# 要守，压力天然更大。系数不是线性的：6 人时只有部分舱室的塔能同时开火。
# 5/6 人档是按 2→3→4 的斜率外推的。**v7 复核过**：这五个值在 ×2 的新曲线下
# 仍然让 2/4/6 人档拉开差距（见设计 §1.5.1 的新表），所以没动。
PLAYER_COUNT_HP_MULT = {2: 1.3, 3: 1.8, 4: 2.3, 5: 2.8, 6: 3.3}

# 三档难度（v1.18.1，房规 `stasis_difficulty`）：**只调已经平衡过的乘数**，
# 不做自由滑条，也不改地图与建造表。三条乘数分别作用在静默体的血量、门伤与
# 「砸门多久进化一级」上——正好覆盖「扛不扛得住 / 打不打得死 / 它长多快」。
# 标准档全部 ×1.0，所以既有战绩与文档里的平衡表仍然对应标准档。
DIFFICULTY_SPECS = {
    "easy": {"label": "轻松", "hp": 0.8, "dps": 0.8, "evolve": 1.3,
             "blurb": "静默体更脆、砸门更慢、进化更晚，适合第一次玩或带新手。"},
    "normal": {"label": "标准", "hp": 1.0, "dps": 1.0, "evolve": 1.0,
               "blurb": "与既有平衡表一致的原版难度。"},
    "hard": {"label": "挑战", "hp": 1.2, "dps": 1.2, "evolve": 0.8,
             "blurb": "更肉、砸门更疼、进化更快；需要明确分工与及时升级炮台。"},
}
DEFAULT_DIFFICULTY = "normal"


def difficulty_spec(gs: dict) -> dict:
    """当前对局的难度乘数；老存档没有该字段时退回标准档。"""
    return DIFFICULTY_SPECS.get(gs.get("difficulty") or DEFAULT_DIFFICULTY,
                                DIFFICULTY_SPECS[DEFAULT_DIFFICULTY])

ENTITY_SPEED = 2.0                 # 格/秒
# 撤退时瘸着走。**这个值是「能不能反杀」的总开关**（设计 §1.5.1 缺陷 2）：
# 撤退和前进同速时它退回气闸几乎不花时间，就没有击杀窗口。
# v2 地图放大后单程从 1-3 格变成 28 格，0.35 倍速下**一次撤退往返要 100 秒**
# （去 80s + 回 14s + 再生 3s），看着像它退场了 —— 但**不要因此调快**：
# 实测调到 0.5 反杀率从 2人14/16 掉到 6/16，调到 1.0 直接掉到 0/16。
# 那 100 秒正是玩家「集中火力把它打死」的窗口，是设计里刻意的。
RETREAT_SPEED_MULT = 0.35
RETREAT_THRESHOLD = 0.25           # HP 低于此比例**才考虑**撤退（还要过 `retreat_commit`）
# **「砸够一截才准跑」（v10，用户实测反馈）**：光看血线会让它变成打不死的小强 ——
# 血一掉到 25% 就撤回气闸、回血到 40% 又回来，每次只在门外挨一两下就又跑，
# 一个来回的净损失（15% 血）远小于它回的那 15%。用户的原话是
# 「没血了打一下就跑，回血后又过来又打一下就跑，永远打不死」。
#
# 判据：**本次出击对当前那扇门造成的伤害 ≥ 门血上限的这么多**，才准撤。
# 于是「挨打多久」有了下限 —— 玩家保证有一段能持续输出的窗口，塔的 DPS 才有意义。
#
# ⚠️ **它同时也是「这局还有没有救」的开关**：门血上限比静默体的 DPS 涨得快
# （门每级 ×2、鬼每级 ×1.5），所以终盘这一条会变得很难满足 —— 那是**对的**，
# 那时候它本来就该站在门口挨打。真正要防的是**前期**它一碰就跑。
#
# **撤退的门槛：这一次出击必须砸够「门血上限的这个比例」才准撤。**
#
# ⚠️ **判据是「累计打掉的伤害」，不是「此刻门的血量」**（用户明确指出的）：
# 门的血量会因为**修补**（每秒回上限的百分比）或**升级**（升级即满血）而涨回去，
# 拿「现在是不是 < 75%」当判据的话，一条边挨打边回血的循环就能把它永远挡在
# 「没砸够」之外 —— 而它真正想表达的是**它这一趟确实啃掉了这么多门**。
# 实现上就是 `trip_damage`（本次出击的累计伤害）对上一个**出发时冻住**的门槛
# `trip_commit`（见 `_start_trip`）：门中途被升级/补血都不会改变这一趟的账。
#
# ⚠️ **撤退次数不设上限**（v13 一度做过「一局最多撤两次」，已按用户意见撤回）：
# 拦「打一下就跑」这件事由上面这条承诺负责，不需要再叠一层次数上限 ——
# 而次数上限的副作用是**它后期再也回不了血**，玩家的输出一够它就必死，
# 难度梯度整个塌掉（实测见 `设计.md` §1.5.1 的 v13 表）。
RETREAT_COMMIT = 0.25
REGEN_RATE = 0.05                  # 再生：每秒回**最大 HP 的 5%**
# **回到满血才出来**（v13，用户明确要求：「鬼把血回满再出来进攻，而不是回到 40%
# 就出来，并且回血过程中不无敌，只要在炮台范围内就可以打」）。
#
# 老值 0.4 是 v2 定平衡时挑的，当年那条注释写着「必须 < 1 —— 回满血时数学上就
# 再也打不死了」：那时它回完血就立刻重新出发，而玩家的 DPS 又追不上它的成长曲线，
# 于是「打不死」是一条真实的死路。**v10 之后这条推理的两个前提都没了**：
# ① 再生期间不再无敌（射程够得着的炮台一直在打它），
# ② 撤退有承诺门槛（每次出击必须先啃掉门血上限的 25% 才准走），
# 所以它待在气闸里的每一秒都在挨打，而它每出来一趟都得先在炮口下站够时间。
# 回满血是**把主动权还给玩家**：想拦住它，就得在它退回去的路上/在气闸门口
# 把输出补齐，而不是「等它自己回 40% 又出来送」。
REGEN_UNTIL = 1.0
# ── 进化：**只有砸门才升级**（v13，把「按秒白送」彻底删掉） ──────────
#
# 原型的鬼是**砸门才升级**（`增强调研.md` §11.4 / §12.6 / §14.8 记了整整三轮），
# 而本作从 v2 起一直挂着一条 45→50 秒的计时进化 —— 它按**游戏时钟**走，于是
# 「把它打到只能在走廊里游荡」对它毫无影响，6 分钟照样白送 7~8 级（×25.6 的血、
# ×17 的伤害），而那正是「门与鬼都每级 ×2」那条最贴原型的曲线不可行的根因。
#
# 现在进化只由**「它真的在砸门」这件事**驱动：`e["attack_ms"]` 只在**它正贴着
# 门啃、真的在掉门血**的那些 tick 累加，攒够 `EVOLVE_ATTACK_MS` 进化一级。
# 游荡、赶路、追人、撤退、再生**一律不计**。于是：
#
# ① **压制第一次真的有意义**：把它打跑 = 成长停摆（老实现里打跑只是让它晚一点
#    回来，等级照涨）；
# ② 「砸门才升级」在数值上成立，而不是一句叙事。
#
# ⚠️ **先试过「按打掉的门血归一」，量下来不可行，别改回去**：进度记成
# `伤害 / 该门当前的血量上限`，看着更贴「砸门进度」，但它有一个**正反馈**——
# 升级越快 ⇒ DPS 越高 ⇒ 砸穿同一扇门越快 ⇒ 升级更快。实测（16 局 × 5 打法 ×
# 3 人数档，`test/balance_stasis.py`）阈值 0.25 时**任何打法都在 48 秒内全灭**
# （均 8~10 级），阈值 1.0 时又变成「它一级都升不上去、谁都能赢」（4/6 人档
# 16/16 击杀、均 1.3 级）——**中间没有可用的档位**，因为这条曲线是刀刃。
# 「按砸门**时间**」把正反馈掐掉了：升级速度恒为「每 `EVOLVE_ATTACK_MS` 的**实际
# 啃门时长**一级」，与它当前多强无关（与 `EVOLVE_MS` 那版相比，多出来的只是
# 「游荡 / 撤退 / 赶路不计」这一条）。
#
# 顺带修掉 §11.8 记的那条：老口径是**绝对累计伤害**（固定 1500），而门血每级
# 翻倍 ⇒ 砸破一扇 13 级门（153.6 万血）会一口气把它顶到满级。换成「砸门时长」
# 之后，**砸穿一扇高级门要花的时间本来就长**，这件事由门血自己管着，不再需要一个
# 会过期的手写阈值。
#
# **不累积超过一级**（攒满就清零）：积压多级会在界面上连成一串进化提示，玩家读不出
# 发生了什么；而且热重启恢复的老存档里这个字段是旧口径的绝对值（上千），清零刚好
# 把它自愈掉。
#
# **35 秒是扫出来的**（`test/balance_stasis.py`，16 局 × 5 打法 × 3 人数档，
# 同一轮里它还要跟「回满血」那条改动一起看），判据是**难度梯度分不分得开**：
#
# | 每级砸门时长 | 均衡发展（= 人机）2/4/6 | 只会造炮台 | 龟缩守门 | 经济+输出 |
# |---|---|---|---|---|
# | 18s | 12/1/0 | 5/1/1 | 5/1/1 | 7/1/3 |
# | 25s | 14/10/8 | 5/2/3 | 6/2/3 | 13/5/4 |
# | **35s（现值）** | **16/16/16** | **5/3/4** | **7/3/4** | **15/12/14** |
# | 50s | 16/16/16 | 7/9/8 | 16/10/8 | 16/15/16 |
#
# 35 秒这一行是唯一「好打法基本能赢（75~100%）、差打法赢不了（19~44%）」的档；
# 50 秒时连「只堆炮台不升门」都能赢一半，梯度糊掉了。
#
# ⚠️ **它比老计时器（50 秒游戏时钟）大，但成长其实更慢**：这 35 秒是**真的在啃门**
# 的时长，游荡/赶路/追人/撤退/回血都不算。一局里它大概只有一半时间在啃门。
EVOLVE_ATTACK_MS = 35 * 1000
IDLE_MS = 2500                     # 游荡时挑下一个目标的间隔
EMP_CD_MS = 25 * 1000
EMP_DURATION_MS = 3000
RAGE_THRESHOLD = 0.20              # 目标门血低于此比例 → 过载（攻速 ×2）
RAGE_MULT = 2.0

# 追击：通道里有人靠近就去追。**这是「走廊危险」的实现**，也是开局跑动阶段的张力来源。
CHASE_RANGE = 4.5                  # 这个距离内、且在通道里的玩家会被追
CHASE_REPATH_MS = 400              # 追人时重算路径的间隔
CHASE_LEASH = 8.0                  # 离目标门超过这么远就放弃追击回去砸门

# 撤退时**一路退回地图边缘的气闸**，到那儿才开始回血。
#
# ⚠️ **v8 推翻了 v2 的「退到 6 格外就停下再生」**（用户实测：「鬼残血没有退回到
# 气闸门，而是在走廊上某个位置就回血了，应该退回到地图边缘的气闸门才能回血」）。
#
# v2 当年改成「退 6 格即停」是为了救「反杀」——那时唯一的胜利条件是撑到救援，
# 反杀只是彩蛋，把脆弱窗口从 7 秒拉回 17 秒就够了。**v8 之后击杀是唯一的胜利
# 条件**，规则得反过来服务它：静默体退回气闸意味着**它每次都要重新穿过整条
# 走廊**，来回两趟都在炮台射程里 —— 玩家要做的不是抢那十几秒的爆发窗口，
# 而是「把走廊沿线的火力堆到能磨掉它这一趟的血」。这条比「抢窗口」好懂得多，
# 也让「塔摆哪儿」从「贴门摆」变成「沿来路铺开」。
#
# 代价：一次撤退往返可能要 100 秒以上（0.35 倍速 × 最长 40 格），它有相当一部分
# 时间在赶路。这是**刻意的**——那是玩家发育的窗口，也是「磨」的窗口。
# 平衡靠 `test/balance_stasis.py` 复核，别凭手感调。

# ── 身份色 ──────────────────────────────────────────────────

# 顶层 theme.stasisSeatColors 的键。ws.py 多处通用循环会把 `player_colors`
# 当「玩家 uid 集合」用，**留空会让所有广播静默地一个都不发**。
PLAYER_COLORS = ["blue", "red", "green", "yellow", "purple", "cyan"]

# 终局原因。
#
# ⚠️ **v8 删掉了 `REASON_RESCUE`（「撑到救援舰抵达」）** —— 用户要求「获胜条件中
# 的救援抵达删掉，唯一获胜条件是击杀鬼」。原来的两条胜利条件（撑满 6 分钟 /
# 打死它）里，撑时间那条是**默认结局**：人机实测 16/16 局都能撑到救援，于是
# 「反杀」永远只是彩蛋，倒计时也从「打赢」退化成了「等 6 分钟」。
#
# **一局现在没有时长上限**（用户追加要求：「不要求 6 分钟内打死，时间不限」），
# 所以 `REASON_TIMEOUT` 只剩「撞上 `STALL_CAP_MS` 防卡死兜底」这一种来源。
REASON_KILLED = "killed"           # 把静默体打死了 —— **唯一的胜利**
REASON_TIMEOUT = "timeout"         # 僵持到兜底上限（正常对局碰不到）
REASON_WIPED = "wiped"             # 全员舱室失压

def max_level(kind: str) -> int:
    """某种东西的满级。**从费用表推**（表有 n 项 ⇒ 能升到 n+1 级），
    这样「加一级」只需要动一处表，不会出现「表长 5、上限写 6」的漂移。"""
    return len(UPGRADE_COST[kind]) + 1


def upgrade_cost(kind: str, level: int) -> float:
    """从 `level` 升到 `level+1` 要花多少**主资源**（见 `upgrade_res`）。

    主资源**默认是生物能**，但矿（`UPGRADE_RES`）是**合金** —— 与它们的建造费
    同一个池子。⚠️ 除此之外这只是**一半**：第 6 级往上还要 `upgrade_alloy()`
    那份合金。任何「够不够钱」的判断都必须两个一起看 —— 只看生物能会让客户端
    画出一颗点不动的按钮、让人机卡在一个永远被拒的动作上
    （`_stasis_bot_think` 是静默吞掉 `ValueError` 的，见 `增强调研.md` §11.2）。
    """
    return UPGRADE_COST[kind][level - 1]


def upgrade_res(kind: str) -> str:
    """升级掏的是哪个资源池（`"bio"` / `"alloy"`）。"""
    return UPGRADE_RES.get(kind, "bio")


def upgrade_alloy(kind: str, level: int) -> float:
    """从 `level` 升到 `level+1` 要**额外**搭多少合金（0 = 这一级不搭）。"""
    if level + 1 < ALLOY_FROM_LEVEL:
        return 0.0
    return upgrade_cost(kind, level) / ALLOY_RATIO


def turret_range(level: int) -> float:
    return TURRET_RANGE_BY_LEVEL[level - 1]


def bed_door_req(level: int) -> int:
    """升到床 `level` 级所需的**最低门等级**（间断门槛，见 `BED_DOOR_REQ`）。

    1 级是开局自带的，所以 `BED_DOOR_REQ` 里没有它 —— 返回 1，永远不会挡住。
    """
    return BED_DOOR_REQ.get(level, 1)


def door_name(level: int) -> str:
    """门的展示名：铁门 1-5 → 钢门 1-5（原型的两档，见 `DOOR_TIER`）。"""
    tier, idx = divmod(level - 1, 5)
    tier = min(tier, len(DOOR_TIER) - 1)
    return f"{DOOR_TIER[tier]} {idx + 1}"


# ── 小工具 ──────────────────────────────────────────────────


def cell_key(x: int, y: int) -> str:
    return f"{int(x)},{int(y)}"


def parse_key(key: str) -> tuple[int, int]:
    a, b = key.split(",")
    return int(a), int(b)


# ── 地图与共用闸门 ──────────────────────────────────────────
#
# **几何一律走「这一局用的那张图」**（v9 起有多张图，`stasis_map.MAPS`）。
# 这里只放两个入口：按局取图，以及「共用闸门」这套目标 id 的解析。
#
# 共用闸门（`suite` 图的核心）在引擎里的实现只有一句话：**它就是一扇没有主人的
# 门**。字段与房间门**完全同构**（`door_level` / `door_hp` / `emp_until_ms`），
# 所以砸门、修补、升级、EMP、快照五段逻辑都不用分叉 —— 只有「砸破之后干什么」
# 不同（房间门破 = 主人失压；闸门破 = 它后面的门厅对静默体开放）。

GATE_PREFIX = "gate:"


def game_map(gs: dict) -> M.Map:
    """这一局用的地图（编译好的几何，`get_map` 带缓存）。"""
    return M.get_map(gs["map"]["id"])


def is_gate_id(tid: str) -> bool:
    return tid.startswith(GATE_PREFIX)


def gate_of(tid: str) -> str:
    return tid[len(GATE_PREFIX):]


def gate_id(gid: str) -> str:
    return GATE_PREFIX + gid


def open_gates(gs: dict) -> list[str]:
    """**已经砸破**的闸门 id —— 静默体的可达图靠它决定门厅开不开。"""
    return [gid for gid, g in gs["map"]["gates"].items() if g["broken"]]


def door_obj(gs: dict, tid: str) -> dict | None:
    """按目标 id 取门对象（房间门或共用闸门）。"""
    if is_gate_id(tid):
        return gs["map"]["gates"].get(gate_of(tid))
    return gs["map"]["rooms"].get(tid)


def door_out(gs: dict, tid: str) -> tuple[int, int]:
    """静默体砸这扇门时站的那一格（门外的通道格）。"""
    m = game_map(gs)
    if is_gate_id(tid):
        return m.gate_out(gate_of(tid))
    return m.room_door(tid)["out"]


def room_alive(gs: dict, rid: str) -> bool:
    """这间舱室现在有人（且那人还活着）吗。"""
    room = gs["map"]["rooms"].get(rid)
    if room is None or room["owner"] is None:
        return False
    return gs["players"][room["owner"]]["alive"]


def door_alive(gs: dict, tid: str) -> bool:
    """这扇门现在还算不算一个「值得砸」的目标。

    房间门：门后有人。**闸门：还没破，且它后面至少还有一间有人的舱室** ——
    一间都没人的套间不值得砸（那两间房是空的，砸开也没意义）。
    """
    if is_gate_id(tid):
        gid = gate_of(tid)
        gate = gs["map"]["gates"].get(gid)
        if gate is None or gate["broken"]:
            return False
        return any(room_alive(gs, r) for r in game_map(gs).gates[gid].rooms)
    return room_alive(gs, tid)


def _cell_of(pos) -> tuple[int, int]:
    """浮点坐标 → 格。

    **约定：格 `(x, y)` 的中心是 `(x + 0.5, y + 0.5)`**（`SPAWN` / `_start_playing`
    摆人、`dps_in_range` 算塔心、`_update_beds` 判「站在床上」全都按这条来）。
    所以取格是 `floor`，**不是 `floor(x + 0.5)`** —— 后者会把站在格心的
    `(9.5, 9.5)` 判成第 10 格，于是 `room_at` 返回 None、静默体跑去追一个
    躺在舱室里的人，追不到又永不放弃（实测踩过：整局 0 次砸门）。
    也**不能用 `round`**：Python 的 round 是银行家舍入，`round(4.5)==4` 而
    `round(5.5)==6`，边界上左右不一致。
    """
    return int(math.floor(pos[0])), int(math.floor(pos[1]))


# ── 建局 ────────────────────────────────────────────────────


def create_game(user_ids: list[int], seed: int | None = None,
                map_id: str | None = None,
                difficulty: str = DEFAULT_DIFFICULTY) -> dict:
    """建一局：按 `map_id` 取图、随机分配每间舱室的房型，所有人从气闸下方出生，
    进入**跑动阶段**。

    `map_id` 未知时退回默认图（`stasis_map.DEFAULT_MAP_ID`）—— 老战绩里可能
    存着一张后来被删掉的图的 id，那种情况不该让整局开不起来。
    """
    n = len(user_ids)
    if n < PLAYER_MIN or n > PLAYER_MAX:
        raise ValueError(f"深空休眠需要 {PLAYER_MIN}-{PLAYER_MAX} 名玩家")

    m = M.get_map(map_id or M.DEFAULT_MAP_ID)
    rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
    shapes = m.assign_shapes(rng)

    rooms: dict[str, dict] = {}
    for rid in m.room_ids:
        rooms[rid] = {
            "shape": shapes[rid],
            "owner": None,                 # 占房的玩家 uid
            "door_level": 1,
            "door_hp": DOOR_HP[0],
            "emp_until_ms": 0,
            # 应急力场（v13）：护盾到期时刻与冷却到期时刻，都是**游戏时钟**。
            "shield_until_ms": 0,
            "shield_cd_until_ms": 0,
            "buildings": {},               # "x,y" -> {"kind":, "level":}
        }

    # 共用闸门：与房间门**同构**的一份门状态（见 `door_obj` 上面那段）。
    gates: dict[str, dict] = {
        gid: {
            "door_level": 1,
            "door_hp": DOOR_HP[0],
            "emp_until_ms": 0,
            "broken": False,
            # **出资账本**（v13）：`uid -> {"bio":, "alloy":, "acts":}` —— 谁为这扇
            # 共用外闸掏过多少钱。见 `_credit_gate` 的说明。
            "contrib": {},
        }
        for gid in m.gates
    }

    players: dict[int, dict] = {}
    for seat, uid in enumerate(user_ids):
        spawn = m.spawns[seat % len(m.spawns)]
        players[uid] = {
            "uid": uid,
            "seat": seat,
            "room": None,
            "pos": [float(spawn[0]) + 0.5, float(spawn[1]) + 0.5],
            "dir": [0.0, 0.0],             # 本 tick 的移动输入（-1..1）
            "lying": False,                # 躺下了（显式动作，不是「站到床上」的副产物）
            "in_bed": False,
            "bio": START_BIO,
            "alloy": 0.0,
            "bed_level": 1,
            "alive": True,
            "surrendered": False,
            "eliminated_at_ms": None,
        }

    return {
        "game": "stasis",
        "num_players": n,
        "seat_uids": list(user_ids),
        "player_colors": {uid: PLAYER_COLORS[i] for i, uid in enumerate(user_ids)},
        "seed": seed,
        "difficulty": difficulty if difficulty in DIFFICULTY_SPECS else DEFAULT_DIFFICULTY,
        "rng": rng,
        "phase": "run",                    # run → playing → over
        "run_ms": RUN_MS,                  # 跑动倒计时（自己走，不占 clock_ms）
        "clock_ms": 0,                     # **游戏时钟**，只在 playing 阶段累加
        "map": {
            "id": m.id,
            "shapes": shapes,
            "rooms": rooms,
            "gates": gates,
            "airlock": list(m.airlock),
        },
        "players": players,
        "entity": _spawn_entity(n, m, difficulty),
        "winner": None,
        "finished": False,
        "reason": None,
        "ranking": [],
    }


def _spawn_entity(num_players: int, m: M.Map,
                  difficulty: str = DEFAULT_DIFFICULTY) -> dict:
    """静默体在气闸待命。跑动阶段它不动（`advance` 里按 phase 挡住）。"""
    spec = DIFFICULTY_SPECS.get(difficulty) or DIFFICULTY_SPECS[DEFAULT_DIFFICULTY]
    hp = ENTITY_HP[0] * PLAYER_COUNT_HP_MULT[num_players] * spec["hp"]
    return {
        "level": 1,
        "hp": hp,
        "max_hp": hp,
        "state": "idle",                   # idle/approach/attack/chase/retreat/regenerate
        "pos": [float(m.airlock[0]) + 0.5, float(m.airlock[1]) + 0.5],
        "path": [],                        # 剩余格子路径 [[x,y], ...]
        # 正在砸的目标：**房间 id 或 `"gate:G1"`**（v9 起两样都可能是目标，
        # 见 `door_obj`）。协议里也是这个字段，客户端按前缀分辨。
        "target_door": None,
        "target_uid": None,
        "idle_ms": IDLE_MS,
        "repath_ms": 0,
        # 累计**砸门时长** → 进化判据（v13）。只在它真的贴着门啃、门血在掉的
        # 那些 tick 累加（游荡/赶路/追人/撤退/再生一律不计），攒够
        # `EVOLVE_ATTACK_MS` 进化一级、随即清零。跨门累计。
        "attack_ms": 0.0,
        # **本次出击对当前目标门造成的伤害**（v10）→ 撤退判据（`retreat_commit`）。
        # 与 `attack_ms` 分开记：那个管进化（累计砸门时长）；这个是**绝对伤害**，
        # 因为它的判据本来就是「占这扇门血上限的多少」（换目标就清零）。
        "trip_damage": 0.0,
        # **这一趟出击的撤退门槛**：出发那一刻按门血上限冻住（`_start_trip`）。
        # 判据是累计伤害对它，**不是**「此刻门的血量比例」—— 门边挨打边修补/
        # 升级满血时，后者会永远判「还没砸够」。
        "trip_commit": 0.0,
        "emp_cd_ms": EMP_CD_MS,
        # **被束缚到什么时候**（v13，束缚弹射器）。期间不动、不砸门、不放技能。
        "bound_until_ms": 0,
        # **过载已经对哪扇门报过**（v13）：过载是「血量比例」这种连续量，
        # 而束缚弹射器要挂在一个**离散瞬间**上，所以这里记住「这一扇门报过了」。
        "rage_door": None,
        "killed": False,
    }


# ── 查询 ────────────────────────────────────────────────────


def seat_of(gs: dict, uid: int) -> int | None:
    try:
        return gs["seat_uids"].index(uid)
    except ValueError:
        return None


def player_of(gs: dict, uid: int) -> dict | None:
    return gs["players"].get(uid)


def alive_players(gs: dict) -> list[dict]:
    return [p for p in gs["players"].values() if p["alive"]]


def alive_player_uids(gs: dict) -> list[int]:
    return [uid for uid, p in gs["players"].items() if p["alive"]]


def room_of(gs: dict, rid: str) -> dict:
    return gs["map"]["rooms"][rid]


def shapes_of(gs: dict) -> dict[str, str]:
    return gs["map"]["shapes"]


def owner_of(gs: dict, rid: str) -> dict | None:
    uid = gs["map"]["rooms"][rid]["owner"]
    return gs["players"].get(uid) if uid is not None else None


def door_max(room: dict) -> float:
    return DOOR_HP[room["door_level"] - 1]


def room_cells(gs: dict, rid: str) -> list[tuple[int, int]]:
    return game_map(gs).room_cells(rid, shapes_of(gs)[rid])


def buildable_cells(gs: dict, uid: int) -> list[str]:
    """可建造的格子键：自己舱室的地板格，减去休眠舱那一格与已占用的。"""
    p = gs["players"][uid]
    rid = p["room"]
    if not rid:
        return []
    room = room_of(gs, rid)
    bed = cell_key(*game_map(gs).room_bed(rid))
    return [cell_key(x, y) for x, y in room_cells(gs, rid)
            if cell_key(x, y) != bed and cell_key(x, y) not in room["buildings"]]


def count_buildings(room: dict, kind: str) -> int:
    return sum(1 for b in room["buildings"].values() if b["kind"] == kind)


def room_limit(kind: str) -> int | None:
    """这种建筑每间舱室最多几台（`None` = 只受格子数限制）。"""
    return ROOM_LIMITS.get(kind)


# ── 控场四件的效果 ──────────────────────────────────────────
#
# 三件作用于「正在被砸的那间舱室」（谁造谁受益 —— 本作没有原作那种「帮队友
# 建造」，所以效果一律落在自己的门上），粒子加速器作用于自己舱室的炮台。


def cryo_mult(room: dict) -> float:
    """低温阱的减速倍率：每台 ×(1 − 20%)，三台 ×0.512。"""
    n = count_buildings(room, CRYO)
    return (1.0 - CRYO_SLOW) ** n


def accel_mult(room: dict) -> float:
    """粒子加速器的炮台增伤倍率：有就很 ×1.5。"""
    return 1.0 + ACCEL_BOOST * count_buildings(room, ACCEL)


def shield_ready(gs: dict, room: dict) -> bool:
    """应急力场现在能不能开：得有那台建筑、门血到了触发线、冷却也好了。"""
    if count_buildings(room, SHIELD) <= 0:
        return False
    if gs["clock_ms"] < room.get("shield_cd_until_ms", 0):
        return False
    return _ratio(room["door_hp"], door_max(room)) <= SHIELD_TRIGGER


def shield_active(gs: dict, room: dict) -> bool:
    return gs["clock_ms"] < room.get("shield_until_ms", 0)


def room_dps(gs: dict, rid: str) -> float:
    """该舱室哨戒炮的总 DPS（被 EMP 瘫痪时为 0；**含粒子加速器的增伤**）。"""
    room = room_of(gs, rid)
    if gs["clock_ms"] < room["emp_until_ms"]:
        return 0.0
    return accel_mult(room) * sum(TURRET_DPS[b["level"] - 1]
                                  for b in room["buildings"].values()
                                  if b["kind"] == TURRET)


def _turrets_in_range(gs: dict, pos) -> list[tuple[str, int, float]]:
    """能打到 `pos` 的哨戒炮：`[(格子键, 等级, 增伤倍率)]`。

    **逐塔按距离判定**，所以「塔放哪儿」直接影响火力覆盖（放角落打不到门口）。
    **射程逐塔按自己的等级算**（原型「升级增加攻击力与攻击距离」）：低级塔射程 4，
    够不到对角，所以前期的摆位比后期更挑。被 EMP 瘫痪的舱室整间不参与。
    倍率来自**该塔所在舱室**的粒子加速器（v13）—— 所以它是「舱室级」的加成，
    不是全局 buff：给别人造的塔吃不自己舱室里的加速器。
    """
    out: list[tuple[str, int, float]] = []
    for room in gs["map"]["rooms"].values():
        if gs["clock_ms"] < room["emp_until_ms"]:
            continue
        boost = accel_mult(room)
        for key, b in room["buildings"].items():
            if b["kind"] != TURRET:
                continue
            bx, by = parse_key(key)
            if math.hypot(bx + 0.5 - pos[0], by + 0.5 - pos[1]) <= turret_range(b["level"]):
                out.append((key, b["level"], boost))
    return out


def turrets_in_range(gs: dict, pos) -> list[tuple[str, int]]:
    """能打到 `pos` 的哨戒炮：`[(格子键, 等级)]`（表现层用，倍率不进载荷）。"""
    return [(k, lv) for k, lv, _ in _turrets_in_range(gs, pos)]


def dps_in_range(gs: dict, pos) -> float:
    """所有能打到 `pos` 的哨戒炮的 DPS 之和（**含粒子加速器的增伤**）。"""
    return sum(TURRET_DPS[lv - 1] * boost for _, lv, boost in _turrets_in_range(gs, pos))


def rooms_with_owner(gs: dict) -> list[str]:
    return [rid for rid, r in gs["map"]["rooms"].items()
            if r["owner"] is not None and gs["players"][r["owner"]]["alive"]]


# ── 移动输入 ────────────────────────────────────────────────


def set_move(gs: dict, uid: int, dx: float, dy: float) -> dict:
    """记录本 tick 的移动方向（客户端每 tick 发一次，服务端积分位置）。

    **服务端是位置的权威**：客户端只做本地预演（同俄罗斯方块的教训），
    真值一律以 `stasis_tick` 里的 `pos` 为准。
    """
    p = player_of(gs, uid)
    if p is None:
        raise ValueError("你不在这一局里")
    if not p["alive"]:
        return {"ok": False, "reason": "dead"}
    # **躺着就不收移动输入**：躺下时不能移动（用户的要求），而且这里必须真的
    # 把 dir 压成 0 —— 只是让 `_step_players` 跳过的话，起身那一刻手上还攥着
    # 一个陈旧方向，人会立刻被推离床心。
    if p["lying"]:
        p["dir"] = [0.0, 0.0]
        return {"ok": False, "reason": "lying"}
    # 归一化到单位向量；超长输入夹掉（防「一 tick 瞬移」）
    n = math.hypot(dx, dy)
    if n > 1.0:
        dx, dy = dx / n, dy / n
    p["dir"] = [float(dx), float(dy)]
    return {"ok": True}


def _can_stand(gs: dict, p: dict, x: float, y: float) -> bool:
    """从当前位置走到 `(x, y)` 是否允许（跨通道↔舱室只能经过门那一对格子）。"""
    shapes = shapes_of(gs)
    cur = _cell_of(p["pos"])
    nxt = _cell_of((x, y))
    if nxt == cur:
        return True
    return game_map(gs).can_step(shapes, cur, nxt)


def _step_players(gs: dict, dt_ms: int) -> None:
    """按 `dir` 积分玩家位置。分轴走 —— 撞墙时沿墙滑，手感不粘。"""
    dt = dt_ms / 1000.0
    for p in gs["players"].values():
        if not p["alive"]:
            continue
        if p["lying"]:
            continue                        # 躺下了就走不动（**没有起身**，v4 起）
        dx, dy = p["dir"]
        if dx == 0.0 and dy == 0.0:
            continue
        step = PLAYER_SPEED * dt
        x, y = p["pos"]
        nx = x + dx * step
        if _can_stand(gs, p, nx, y):
            p["pos"][0] = nx
        ny = y + dy * step
        if _can_stand(gs, p, p["pos"][0], ny):
            p["pos"][1] = ny


# ── 占房与上床 ──────────────────────────────────────────────


def _claim_rooms(gs: dict, events: list[dict]) -> None:
    """跑动阶段：**舱室跟着人走** —— 走进一间没主的舱室就占下它，走出去就放掉它。
    进 playing 后不再自动占房。

    「跟着人走」修的是用户实测的一条：原来只在 `p["room"]` 为空时才认领，
    于是「进 A 房 → 出来 → 跑进 B 房」的人**手里还攥着 A** —— 站在 B 的床边
    也点不了「躺下」（`_can_lie` 判的是 `p["room"]` 那间房的九宫格）。
    **躺下之前舱室可以随便换，躺下那一刻才锁死。**

    三条实现约定：

    - **两趟走**（先全部释放、再全部认领）。一趟走的话两个人对调舱室会互相挡住：
      先走的那个发现目标房还挂在对方名下，只能落空，然后对方再让出来已经晚了。
    - **`p["lying"]` 的人两趟都跳过** —— 躺下是不可逆决定（v4），舱室从那一刻起
      才真的是「你的」。躺下后位置被吸在床心、本来也走不出去，这条是显式表达规则。
    - **释放前核 owner**（`room["owner"] == p["uid"]`）：舱室可能已经被门破清空、
      或被 `_start_playing` 转给了别人，照着 `p["room"]` 硬清会误伤新主人。
    """
    if gs["phase"] != "run":
        return
    m = game_map(gs)

    # ① 走出舱室的人让出它
    for p in gs["players"].values():
        if not p["alive"] or p["lying"] or not p["room"]:
            continue
        if m.room_at(*_cell_of(p["pos"])) == p["room"]:
            continue
        room = room_of(gs, p["room"])
        if room["owner"] == p["uid"]:
            room["owner"] = None
        events.append({"type": "room_released", "uid": p["uid"], "room": p["room"]})
        p["room"] = None

    # ② 走进空舱室的人占下它
    for p in gs["players"].values():
        if not p["alive"] or p["lying"] or p["room"]:
            continue
        rid = m.room_at(*_cell_of(p["pos"]))
        if rid is None:
            continue
        room = room_of(gs, rid)
        if room["owner"] is not None:
            continue
        room["owner"] = p["uid"]
        p["room"] = rid
        events.append({"type": "room_claimed", "uid": p["uid"], "room": rid})


def _update_beds(gs: dict) -> None:
    """**躺下**才产资源，而且躺下时要被吸到床心。

    这条路原来写成「站在休眠舱那一格上 = 自动躺着产资源」。问题有两个：
    ① **没有「躺下」这个动作**，玩家走进舱室站在床边干看着，不知道该干嘛
    （用户报的「进房间没法躺下、生物能没增加」）；
    ② 躺在床上时**位置仍然是自由的**，任何一点残留的移动输入都会把你从床上
    蹭下去 —— `in_bed` 一帧 True 一帧 False，收入断断续续。

    现在 `lying` 是显式状态：`stasis_lie` 置上，**没有东西能清掉它**（v4 起
    「躺下后不能起身」，见 `增强调研.md` §9.3），躺着的期间 `_step_players`
    直接跳过这个人（不能移动）。位置也在躺下时吸附到床心
    （`lie` / `_update_beds` 两处都对齐），免得「躺在床沿上」。
    """
    for p in gs["players"].values():
        if not p["alive"] or not p["room"] or not p["lying"]:
            p["in_bed"] = False
            continue
        bx, by = game_map(gs).room_bed(p["room"])
        p["pos"] = [float(bx) + 0.5, float(by) + 0.5]
        p["in_bed"] = True


def _bed_adjacent(m: M.Map, rid: str, cell: tuple[int, int]) -> bool:
    """`cell` 在不在休眠舱的**九宫格**之内（v6，用户指定）。

    休眠舱固定在舱室内部的 `(BED_LX, BED_LY) = (2, 3)`，也就是**最里面那一行**
    —— 于是九宫格只可能落在舱室内部（`ly = 2, 3` 两行，`lx = 1..3` 三列），
    通道格**不可能**满足它，不必再判一次格子类型。房型缺角（`notch` / `lshape`
    / `tshape` / `pillar`）会吃掉其中一两格，剩下 4~6 格可站。

    门开在内部第 1 行，离床隔着一整行 —— 所以「走到床边」重新变成一步真的走位。
    """
    bx, by = m.room_bed(rid)
    return abs(cell[0] - bx) <= 1 and abs(cell[1] - by) <= 1


def _can_lie(gs: dict, p: dict) -> bool:
    """这个人**现在**能不能躺下。**服务端是唯一判据**（见 `can_lie`）。"""
    if not p["alive"] or p["lying"] or not p["room"]:
        return False
    return _bed_adjacent(game_map(gs), p["room"], _cell_of(p["pos"]))


def can_lie(gs: dict, uid: int) -> bool:
    """`uid` 现在能不能躺下 —— **随 tick 下发给客户端画「躺下」按钮**。

    v6 起客户端**不再自己算**这个判据（原来是 `roomAt(cell) === rid`）。理由是
    项目里反复踩过的那一条：**客户端的第二份实现必然与服务端漂移**（象棋的
    `legal`、UNO 的 `playable`、飞行棋的 `path` 都因此改成服务端下发）。这里
    尤其致命 —— 判据是「位置在不在某个格集里」，而客户端预演与服务端权威位置
    稳态差 0.14 格、跨格子边界时能差出一整格：

    - v5 之前用「床格或它的四邻」（5 格），客户端预演说「在」、服务端说「不在」，
      「按钮在、点下去被拒」，E2E 约 1/3 概率红；
    - v5 放宽成「整间舱室」（42 格）掩盖了它，但没解决它 —— 只是把边界推远了；
    - v6 把判据收回九宫格（用户要求），同时**把判定权整个交给服务端**：
      按钮的显隐直接读 tick 里的 `lie` 字段，两边不可能再各说各话。

    代价是按钮最多晚一个 tick（200ms）出现 —— 玩家这时正在**站定**准备点它，
    200ms 在「1 秒」那一档里，可接受；换来的是**按钮永不撒谎**。

    人机也走这个函数（`stasis_ai`），否则「一次决策一个动作」下，被静默拒绝的
    `{"type":"lie"}` 会变成死循环（`_stasis_bot_think` 里 `except ValueError: pass`）。
    """
    p = gs["players"].get(uid)
    return p is not None and _can_lie(gs, p)


# ── 推进 ────────────────────────────────────────────────────


def advance(gs: dict, dt_ms: int, rng: random.Random) -> list[dict]:
    """推进 `dt_ms` 毫秒，返回这期间发生的**离散事件**。

    调用方（tick 循环）负责广播与落盘节流；引擎只管算。已经终局时是空操作。
    """
    if gs["finished"]:
        return []
    events: list[dict] = []

    if gs["phase"] == "run":
        gs["run_ms"] -= dt_ms
        _step_players(gs, dt_ms)
        _claim_rooms(gs, events)
        _update_beds(gs)
        # **跑动阶段躺着也产资源**（v4）：原型的倒计时里就是「躺下 → 攒钱 →
        # 赶在鬼出来前把床升上去」，如果这 30 秒躺着颗粒无收，「躺下」就又一次
        # 变成了看起来没反应的动作（v3 用户报过的那类缺陷）。建筑此时还不存在，
        # 所以实际只有床在产。
        _produce(gs, dt_ms)
        if gs["run_ms"] <= 0:
            events += _start_playing(gs, rng)
        return events

    gs["clock_ms"] += dt_ms
    _step_players(gs, dt_ms)
    _update_beds(gs)
    _produce(gs, dt_ms)
    _move_entity(gs, dt_ms, rng, events)
    _combat(gs, dt_ms, events)
    _check_end(gs, events)
    return events


def _start_playing(gs: dict, rng: random.Random) -> list[dict]:
    """倒计时归零：气闸开启。**没占到房的自动补一间最大的空房**（并送进去），
    否则开局就有人在通道里裸奔，被静默体一路追着打。"""
    gs["phase"] = "playing"
    gs["clock_ms"] = 0
    events: list[dict] = []
    # **所有人都归位到自己的休眠舱上**（不只是没占到房的那批）。
    #
    # 跑动阶段「占到房」发生在**踏进舱室那一刻**，人这时还在门口 —— 如果只给
    # 没占到房的人传送，这批人会整局卡在门口：`in_bed` 永远 False，而床是唯一
    # 的收入来源，于是**一滴生物能都不产、什么都造不出来**，最后门破出局
    # （实测 4 人局 188 秒全灭、一座塔都没有）。
    # 叙事上也顺：倒计时结束＝大家都已经进屋躺好了。
    for p in gs["players"].values():
        if not p["alive"] or not p["room"]:
            continue
        if p["lying"]:
            # **已经躺下的原样留着**（v4：跑动阶段就能躺，而且躺下不可逆）。
            # 这里原来无条件 `p["lying"] = False`，在新规则下等于「倒计时结束
            # 把所有人的床拆了」—— 玩家会发现自己又被拎起来站着。
            continue
        bx, by = game_map(gs).room_bed(p["room"])
        p["pos"] = [float(bx) + 0.5, float(by) + 0.5]
        p["dir"] = [0.0, 0.0]
        # **仍然不替玩家躺下** —— 站到床边、等他自己点「躺下」。这一步就是给
        # 玩家的动作，替他做了等于把这个机制藏起来。v4 之后代价更明确：
        # **没躺下的人不能建造、不能升级，走进走廊被静默体碰到就出局**。

    homeless = [p for p in gs["players"].values() if p["alive"] and not p["room"]]
    if homeless:
        free = sorted((r for r in game_map(gs).room_ids
                       if room_of(gs, r)["owner"] is None),
                      key=lambda r: (-len(room_cells(gs, r)), r))
        for p, rid in zip(homeless, free):
            room_of(gs, rid)["owner"] = p["uid"]
            p["room"] = rid
            bx, by = game_map(gs).room_bed(rid)
            p["pos"] = [float(bx) + 0.5, float(by) + 0.5]
            # **必须清掉残留的移动方向**：跑动阶段没占到房的人手里可能还攥着一个
            # 方向，不清的话 `_step_players` 会立刻把他推离床格 → `in_bed` 永远
            # False → **床是唯一收入来源，从此一滴生物能都不产**（什么都造不出来）。
            # 人类客户端每 tick 发方向（松手补 0,0）能自愈，**人机在对局阶段从不发
            # move，所以是永久残废**。
            p["dir"] = [0.0, 0.0]
            events.append({"type": "room_claimed", "uid": p["uid"], "room": rid,
                           "auto": True})

    events.append({"type": "airlock_open"})
    return events


def _produce(gs: dict, dt_ms: int) -> None:
    """产资源 + 纳米修复器自动补门。

    **床只在你躺着的时候产**；建筑一直产（它在舱室里，跟人在不在无关）。
    **精炼机按自己的等级产**（v4 起它能升级了，所以不能再「数个数 × 固定产率」）；
    **矿按 kind + 等级产**（v10：钻石矿的 2 级就是氪金矿，产率翻倍）。
    """
    dt = dt_ms / 1000.0
    for p in gs["players"].values():
        if not p["alive"]:
            continue
        if p["in_bed"]:
            p["bio"] += BED_RATE[p["bed_level"] - 1] * dt
    for rid, room in gs["map"]["rooms"].items():
        owner = gs["players"].get(room["owner"]) if room["owner"] is not None else None
        if owner is None or not owner["alive"]:
            continue
        n_rep = 0
        for b in room["buildings"].values():
            kind = b["kind"]
            if kind == REFINERY:
                owner["alloy"] += REFINERY_RATE[b["level"] - 1] * dt
            elif kind in MINE_RATE:
                owner["bio"] += mine_rate(kind, b["level"]) * dt
            elif kind == REPAIR:
                n_rep += 1
        if n_rep and room["door_hp"] < door_max(room):
            room["door_hp"] = min(door_max(room),
                                  room["door_hp"] + n_rep * REPAIR_RATE * door_max(room) * dt)


# ── 静默体 ──────────────────────────────────────────────────


def _go_idle(e: dict) -> None:
    """回到游荡态。**必须清 path** —— 否则残留的旧路径会让它继续沿上一扇门的
    路线走，看起来像在原地打转。"""
    e["state"] = "idle"
    e["idle_ms"] = 0
    e["target_door"] = None
    e["target_uid"] = None
    # 目标没了，这一趟的账也清了（v10/v13）。**这里不调 `_start_trip`** ——
    # 它要 `gs` 去查门血上限，而 `_go_idle` 刻意只吃 `e`（调用点太多，
    # 而且「没有目标」时门槛恒为 0）。
    e["trip_damage"] = 0.0
    e["trip_commit"] = 0.0
    e["path"] = []
    e["repath_ms"] = 0


def _plan_to(gs: dict, e: dict, goal: tuple[int, int]) -> None:
    """沿通道网格规划到 `goal` 的格子路径。

    **走的是「这一刻的可达图」**：门厅只在它那扇闸门被砸破之后才进得来
    （`Map.ghost_graph`）。所以闸门没破时，通往门厅里那两扇门的路是**空的** ——
    调用方必须把这种情况当成「够不着」，不能当成「已经到了」。
    """
    m = game_map(gs)
    e["path"] = [[x, y] for x, y in
                 m.ghost_path(_cell_of(e["pos"]), goal, open_gates(gs))]
    e["repath_ms"] = 0


def _advance_along(e: dict, dt_ms: int, speed: float) -> None:
    """沿 path 前进；走完则 path 为空（＝已到达）。"""
    remaining = speed * dt_ms / 1000.0
    while remaining > 1e-9 and e["path"]:
        tx, ty = e["path"][0]
        dx, dy = tx - e["pos"][0], ty - e["pos"][1]
        d = math.hypot(dx, dy)
        if d <= remaining or d < 1e-9:
            e["pos"] = [float(tx), float(ty)]
            e["path"].pop(0)
            remaining -= d
        else:
            e["pos"][0] += dx / d * remaining
            e["pos"][1] += dy / d * remaining
            remaining = 0.0


def _ratio(hp: float, mx: float) -> float:
    return max(0.0, min(1.0, hp / mx)) if mx else 0.0


def _door_weight(gs: dict, tid: str) -> float:
    """一扇门有多「诱人」。`(1 - 门血比例)² + 0.15`（满血 0.15，一成血 0.96）。"""
    if not is_gate_id(tid):
        room = room_of(gs, tid)
        return (1.0 - _ratio(room["door_hp"], door_max(room))) ** 2 + 0.15
    # 闸门：取**它后面最诱人的那间房** —— 砸闸门是手段，进去才是目的；
    # 再乘一个「闸门还剩多少」的折扣：闸门越厚，这笔投资越不划算。
    # 0.4 是下限（满血闸门仍然值得开个头），免得它永远绕开闸门去啃别的门。
    gid = gate_of(tid)
    inner = max((_door_weight(gs, r) for r in game_map(gs).gates[gid].rooms),
                default=0.15)
    gate = gs["map"]["gates"][gid]
    return inner * (0.4 + 0.6 * (1.0 - _ratio(gate["door_hp"], door_max(gate))))


def choose_target(gs: dict, rng: random.Random) -> str | None:
    """仇恨值选目标：**门血比例越低越优先**，其余人保底。

    权重 `(1 - 门血比例)² + 0.15`：满血的门权重 0.15，只剩一成的门 0.96（约 6 倍）。
    纯随机的话玩家无法预判、协作无从谈起；「门最弱的人最危险」才让「救队友」
    成为一个有时限的真实决策。保底 0.15 保证不会完全忽略满血的人。

    **候选里既有房间门也有共用闸门**（v9），而且要先按**可达性**筛一遍：
    闸门没破时，它后面那两间房的门根本走不到（门厅不在可达图里），砸它们是空谈。
    可达性从**它自己站的那一格**算起（一次 BFS 覆盖全部候选，不逐门跑）。
    """
    m = game_map(gs)
    reach = m.reachable(_cell_of(gs["entity"]["pos"]), open_gates(gs))
    cands: list[str] = []
    weights: list[float] = []
    for tid in list(m.room_ids) + [gate_id(g) for g in m.gates]:
        if not door_alive(gs, tid) or door_out(gs, tid) not in reach:
            continue
        cands.append(tid)
        weights.append(_door_weight(gs, tid))
    if not cands:
        return None
    return rng.choices(cands, weights=weights, k=1)[0]


def _start_trip(gs: dict, e: dict, tid: str) -> None:
    """新的一趟出击开始：账清零，**并把这趟的门槛冻在这里**。

    门槛 = 出发那一刻这扇门血上限的 `RETREAT_COMMIT`。冻住它有两个理由：

    ① 判据是**累计伤害**，不是「此刻门的血量比例」—— 门边挨打边被修补/升级满血时，
       拿当前血量比会永远判「没砸够」（用户明确指出来的那个坑）；
    ② 门中途被**升级**（上限翻倍）也不追认 —— 这一趟它啃的就是出发时那扇门。

    调用点与 `trip_damage = 0.0` 完全重合（挑目标、撤退完回场），所以两者一起走。
    """
    e["trip_damage"] = 0.0
    obj = door_obj(gs, tid) if tid else None
    e["trip_commit"] = RETREAT_COMMIT * door_max(obj) if obj else 0.0


def _may_retreat(gs: dict, e: dict) -> bool:
    """血够了、但**准不准跑**（v10 的承诺）。

    只有当它**正在砸一扇门**时才谈承诺：这一趟砸掉的伤害不足出发时那扇门上限的
    `RETREAT_COMMIT` 就继续砸 —— 于是「打一下就跑」那条循环被掐掉了（用户实测
    报的正是它：「没血了打一下就跑，回血后又过来又打一下就跑，永远打不死」）。

    追人 / 游荡 / 走到半路都放行：那几种状态下它没在跟任何一扇门较劲，
    再把它按在原地等于让它站着挨打死，那是另一回事（而且走廊本来就是危险区）。
    """
    if e["state"] != "attack":
        return True
    tid = e["target_door"]
    if tid is None or not door_alive(gs, tid):
        return True
    return e["trip_damage"] >= e["trip_commit"]


def _retreat_goal(gs: dict, e: dict) -> tuple[int, int]:
    """负伤撤退的目的地：**永远是地图边缘的气闸**（v8）。

    它必须走完整条走廊才回得了血 —— 路上一直在炮台射程里，这是「磨死它」的
    唯一窗口。理由与代价见 `RETREAT_SPEED_MULT` 上面那段注释。

    **它现在是个常量函数**（v2~v7 那版要遍历通道格找「离所有有主舱门 ≥6 格的
    格子」）。签名保留 `gs` / `e` 是为了不动调用点，也方便将来再按局势挑撤退点。
    """
    return game_map(gs).airlock


def _chase_candidate(gs: dict, e: dict) -> dict | None:
    """找一个「在通道里、离得够近」的玩家去追。取最近的那个。"""
    m = game_map(gs)
    opened = open_gates(gs)
    best, best_d = None, CHASE_RANGE
    for p in gs["players"].values():
        if not p["alive"]:
            continue
        cell = _cell_of(p["pos"])
        if m.room_at(*cell) is not None:
            continue                        # 站在舱室里不算（那是安全区）
        # **门厅里的人也够不着**（除非那扇闸门已经破了）：闸门只拦静默体，
        # 玩家能走进去 —— 不排掉的话它会对一个隔着门的猎物永远重规划、永远
        # 追不上（`_plan_to` 把目标吸附到门外那格，距离永远 > 1）。
        gid = m.gated_owner(cell)
        if gid is not None and gid not in opened:
            continue
        d = math.hypot(p["pos"][0] - e["pos"][0], p["pos"][1] - e["pos"][1])
        if d < best_d:
            best, best_d = p, d
    return best


def _move_entity(gs: dict, dt_ms: int, rng: random.Random,
                 events: list[dict]) -> None:
    m = game_map(gs)
    e = gs["entity"]
    state = e["state"]

    # **被束缚住 = 原地不动**（v13，束缚弹射器）。移动整段跳过 ——
    # 注意它同时也砸不了门、放不出 EMP（`_combat` ②/④ 各有一道）。
    # 重生/回血不受影响：那是「它自己在气闸里恢复」，不是行动。
    if gs["clock_ms"] < e.get("bound_until_ms", 0) and state != "regenerate":
        return

    if state == "idle":
        # 游荡：沿通道慢慢走，边走近边挑目标
        if not e["path"]:
            graph = m.ghost_graph(open_gates(gs))
            cur = _cell_of(e["pos"])
            nb = sorted(c for c in graph
                        if abs(c[0] - cur[0]) + abs(c[1] - cur[1]) == 1)
            if nb:
                _plan_to(gs, e, nb[0])
        _advance_along(e, dt_ms, ENTITY_SPEED * 0.6)
        e["idle_ms"] -= dt_ms
        if e["idle_ms"] <= 0:
            target = choose_target(gs, rng)
            if target is not None:
                e["target_door"] = target
                _start_trip(gs, e, target)  # 换了目标：账清零、门槛按新门冻住
                _plan_to(gs, e, door_out(gs, target))
                e["state"] = "approach"
                events.append({"type": "entity_target", "door": target})

    elif state == "approach":
        _advance_along(e, dt_ms, ENTITY_SPEED)
        if not e["path"]:
            tid = e["target_door"]
            # **到站了才算「开始砸」**：路径为空也可能是被清空的（换目标、
            # 目标没了、闸门状态变了），那时它还在半路 —— 老代码会就地开砸，
            # 与 v8 在撤退分支上踩过的是同一个坑。所以按**位置**判、不按路径判。
            if tid is None:
                _go_idle(e)
            elif _cell_of(e["pos"]) != door_out(gs, tid):
                _plan_to(gs, e, door_out(gs, tid))
                if not e["path"]:
                    _go_idle(e)          # 够不着（闸门没破）→ 回去重新挑
            else:
                e["state"] = "attack"
                events.append({"type": "entity_attack_start", "door": tid})

    elif state == "attack":
        # 停在门口不动；目标没了（被淘汰/弃守/闸门已破）就重新挑
        if not door_alive(gs, e["target_door"]):
            _go_idle(e)

    elif state == "chase":
        tgt = gs["players"].get(e["target_uid"])
        # 目标没了、或者**已经躲进舱室**就放弃 —— 静默体进不了舱室，继续追
        # 只会贴着墙原地打转（`_plan_to` 把舱内目标吸附到最近的通道格，
        # 到了那儿距离永远 > 1，于是每一帧重规划、永远不结束）。
        if tgt is None or not tgt["alive"] \
                or m.room_at(*_cell_of(tgt["pos"])) is not None:
            _go_idle(e)
            return
        e["repath_ms"] -= dt_ms
        if e["repath_ms"] <= 0 or not e["path"]:
            _plan_to(gs, e, _cell_of(tgt["pos"]))
            e["repath_ms"] = CHASE_REPATH_MS
        _advance_along(e, dt_ms, ENTITY_SPEED * 1.15)
        d = math.hypot(tgt["pos"][0] - e["pos"][0], tgt["pos"][1] - e["pos"][1])
        if d <= ENTITY_CATCH_DIST:
            # **抓到即出局**（v4）。原型就是这样，而且在新规则下它不再是
            # 「随机开局暴毙」：对局阶段走廊里只可能有**没躺下的人**。
            events.append({"type": "entity_catch", "uid": tgt["uid"]})
            _eliminate(gs, tgt["uid"], events, cause="entity")
            _go_idle(e)

    elif state == "retreat":
        _advance_along(e, dt_ms, ENTITY_SPEED * RETREAT_SPEED_MULT)
        if not e["path"]:
            # **只有站在气闸那一格才开始回血**（v8）。`path` 为空也可能是被意外
            # 清空的（`_go_idle` / 目标消失），那种情况下位置还在走廊中间 ——
            # 老代码会就地回血，正是用户报的「在走廊上某个位置就回血了」。
            # 所以这里按**位置**判、不按「路走完了」判。
            if _cell_of(e["pos"]) != m.airlock:
                _plan_to(gs, e, m.airlock)
            else:
                e["state"] = "regenerate"
                e["target_door"] = None
                e["target_uid"] = None
                _start_trip(gs, e, None)
                events.append({"type": "entity_regenerate"})

    elif state == "regenerate":
        e["hp"] = min(e["max_hp"] * REGEN_UNTIL,
                      e["hp"] + e["max_hp"] * REGEN_RATE * dt_ms / 1000.0)
        if e["hp"] >= e["max_hp"] * REGEN_UNTIL:
            e["state"] = "idle"
            e["idle_ms"] = IDLE_MS
            _start_trip(gs, e, None)
            events.append({"type": "entity_return"})


def _combat(gs: dict, dt_ms: int, events: list[dict]) -> None:
    e = gs["entity"]
    dt = dt_ms / 1000.0

    # ① 追击优先：通道里有人就掉头去追（撤退/再生中不追）
    if e["state"] not in ("retreat", "regenerate"):
        chased = _chase_candidate(gs, e)
        if chased is not None:
            if e["state"] != "chase" or e["target_uid"] != chased["uid"]:
                e["state"] = "chase"
                e["target_uid"] = chased["uid"]
                e["path"] = []
                e["repath_ms"] = 0
                events.append({"type": "entity_chase", "uid": chased["uid"]})
        elif e["state"] == "chase":
            # 追丢了：回去砸门，没目标就游荡
            e["target_uid"] = None
            if door_alive(gs, e["target_door"]):
                _plan_to(gs, e, door_out(gs, e["target_door"]))
                e["state"] = "approach"
            else:
                _go_idle(e)

    # ② 静默体砸门。**房间门与共用闸门走同一段代码** —— 两者字段同构，
    #    差别只在「砸破之后干什么」（`_breach`）。
    #
    #    这一段同时也是**控场道具**生效的地方（v13）：低温阱减攻速、应急力场
    #    在门血见底时开无敌。共用闸门没有「舱室」这个词（`room_of` 查不到），
    #    所以三件作用在舱室上的道具**只护自己的门**，护不到共用外闸。
    bound = gs["clock_ms"] < e.get("bound_until_ms", 0)
    if e["state"] == "attack" and e["target_door"] is not None:
        tid = e["target_door"]
        obj = door_obj(gs, tid)
        room = None if is_gate_id(tid) else room_of(gs, tid)
        if obj is not None and door_alive(gs, tid):
            # 应急力场：门血掉进触发线就开 3 秒无敌（有冷却，见 `SHIELD_CD_MS`）
            if room is not None and shield_ready(gs, room):
                room["shield_until_ms"] = gs["clock_ms"] + SHIELD_MS
                room["shield_cd_until_ms"] = gs["clock_ms"] + SHIELD_CD_MS
                events.append({"type": "shield_up", "room": tid,
                               "ms": SHIELD_MS})
            shielded = room is not None and shield_active(gs, room)
            raging = obj["door_hp"] <= RAGE_THRESHOLD * door_max(obj)
            # **过载是一个「技能」，只在它刚刚进入过载的那一刻报一次** ——
            # 束缚弹射器就是挂在这个瞬间上的（原作：猛鬼释放技能时概率控制）。
            if raging and e["rage_door"] != tid:
                e["rage_door"] = tid
                events.append({"type": "entity_rage", "door": tid})
                _try_bind(gs, e, [tid], events)
            if e["rage_door"] != tid and not raging:
                e["rage_door"] = None
            mult = cryo_mult(room) if room is not None else 1.0
            dmg = (ENTITY_DPS[e["level"] - 1] * difficulty_spec(gs)["dps"]
                   * (RAGE_MULT if raging else 1.0) * dt)
            if shielded:
                dmg = 0.0                      # 力场把这 3 秒的门血全吃掉
            elif bound:
                dmg = 0.0                      # 被束缚住：动不了、也砸不动
            else:
                dmg *= mult
            if dmg > 0:
                obj["door_hp"] -= dmg
                # **进化进度＝砸门时长**（v13）：只有这一支会累加，所以游荡 /
                # 赶路 / 追人 / 撤退 / 再生 / 被控制**都不算成长**。
                e["attack_ms"] += dt_ms
                e["trip_damage"] += dmg
            if obj["door_hp"] <= 0:
                obj["door_hp"] = 0.0
                _breach(gs, tid, events)
                _go_idle(e)

    # ③ 哨戒炮反击。**只要它在通道里就能打**（游荡/接近/砸门/追击/撤退/再生）。
    #
    # ⚠️ **v10 把「再生时无敌」这条删了**（用户实测反馈：「鬼即使在恢复过程中也要
    # 允许被打，不能无敌」）。旧代码写的是 `if e["state"] != "regenerate"`，理由
    # 是「它在气闸外，打不到」—— 但气闸**就在地图边缘的通道上**，射程够得着的塔
    # 本来就能打它。那条守卫于是变成了一条**凭空的无敌**：玩家看着它当着面回血、
    # 一发都打不进去，只会以为游戏坏了。至于「围在气闸外等它出来」的收益，本来
    # 就该由射程与摆位决定，不该由状态机决定。
    total = dps_in_range(gs, e["pos"])
    if total > 0:
        e["hp"] -= total * dt
        if e["hp"] <= 0:
            e["hp"] = 0.0
            e["killed"] = True
            events.append({"type": "entity_killed"})
            return
        # **已经决定撤退了（或已在气闸再生）就别再触发一次**：这段代码每 tick 都
        # 跑，漏掉这个判断就会每 tick 重新触发一次撤退事件、并把路径重规划一遍，
        # 静默体于是原地磨蹭着走不回去 —— 一个只有跑模拟才看得出来的坑。
        #
        # ⚠️ **v10 起 `regenerate` 也必须排掉**：那时它把「再生期间无敌」那条
        # 删了（改成能被打），于是这段判断**开始能在再生态下跑到** —— 而再生态
        # 下面它本来就在气闸里回血，`_may_retreat`（只在 `attack` 态才谈承诺）
        # 一律放行，于是每一 tick 都把它从 `regenerate` 拽回 `retreat`、
        # `_move_entity` 下一 tick 又把它推回 `regenerate`：**回血与撤退每 tick
        # 互翻，撤退事件刷屏、血也永远回不上去**（实测 12 秒里刷了 7 次）。
        # 这条是跑测试时才暴露的 —— 它正是「再生时无敌」当年顺手掩盖掉的。
        #
        # **撤退次数不设上限**（v13 一度试过「一局最多两次」，按用户意见撤回）：
        # 拦住「打一下就跑」的是 `_may_retreat` 那条承诺（每趟必须啃够门血上限的
        # 25%），而不是次数 —— 次数上限的副作用是它后期再也回不了血，玩家的输出
        # 一够它就必死，难度梯度整个塌掉（实测见 `设计.md` §1.5.1 的 v13 表）。
        if e["state"] not in ("retreat", "regenerate") \
                and e["hp"] < RETREAT_THRESHOLD * e["max_hp"] \
                and _may_retreat(gs, e):
            e["state"] = "retreat"
            e["target_uid"] = None
            _plan_to(gs, e, _retreat_goal(gs, e))
            events.append({"type": "entity_retreat"})

    # ④ 过载时放 EMP，瘫痪目标舱室的炮台。**被束缚住时放不出来**（那是「无法
    #    攻击」的一部分），冷却照走 —— 否则束缚一结束它就把攒着的那发立刻砸出来。
    if e["state"] == "attack" and e["target_door"] is not None and not bound:
        e["emp_cd_ms"] -= dt_ms
        if e["emp_cd_ms"] <= 0:
            hit = _emp_rooms(gs, e["target_door"])
            for rid in hit:
                room_of(gs, rid)["emp_until_ms"] = gs["clock_ms"] + EMP_DURATION_MS
            e["emp_cd_ms"] = EMP_CD_MS
            events.append({"type": "entity_emp", "door": e["target_door"],
                           "rooms": hit})
            # **EMP 是一个「技能」** → 束缚弹射器在这里掷骰子（原作：猛鬼放技能
            # 时 30% 概率发出符咒、控制住它）。闸门目标没有舱室，掷不出来。
            _try_bind(gs, e, hit, events)

    # ⑤ 进化：**只有砸门才升级**（v13，原型的规则）。
    #
    #    计时进化在本轮被删掉了（见 `EVOLVE_ATTACK_MS` 上面那段）：现在唯一的
    #    成长来源是 `attack_ms` —— 它只在 ② 那一支里累加，也就是**它真的在啃门**
    #    的时候。**把它打跑就等于掐断它的成长**，这是老实现里做不到的事。
    #
    #    **再生期间不进化**：那会让 max_hp 一路上涨、「回血到 40%」的目标跟着
    #    往后跑，它就会卡在气闸外回血回一整局（实测踩过）。
    if e["state"] != "regenerate" and e["level"] < MAX_ENTITY_LEVEL \
            and e["attack_ms"] >= EVOLVE_ATTACK_MS * difficulty_spec(gs)["evolve"]:
        e["level"] += 1
        # **清零而不是扣掉阈值**（不累积超过一级）：积压多级会在界面上连成一串
        # 进化提示，玩家读不出发生了什么；而且热重启恢复的老存档里这个字段是
        # 旧口径的绝对值（上千），清零刚好把它自愈掉。
        e["attack_ms"] = 0.0
        mult = PLAYER_COUNT_HP_MULT[gs["num_players"]]
        new_max = ENTITY_HP[e["level"] - 1] * mult * difficulty_spec(gs)["hp"]
        e["hp"] += max(0.0, new_max - e["max_hp"]) * 0.5
        e["max_hp"] = new_max
        events.append({"type": "entity_evolve", "level": e["level"]})


# ── 舱室失压 ────────────────────────────────────────────────


def _try_bind(gs: dict, e: dict, rooms: list[str],
              events: list[dict]) -> None:
    """束缚弹射器掷骰子（v13）。

    挂在**它释放技能的那一刻**（原作的触发条件就是「猛鬼释放技能时」）。本作里
    「技能」有两个，都是可观测的离散瞬间：**放 EMP**（④）与**进入过载**
    （②里刚跌破 `RAGE_THRESHOLD` 那一下）。

    `rooms` 是这次技能影响的舱室 —— 命中任意一台束缚弹射器就定住它，
    多台一起掷（每台各自 30%，不是叠加成一次）。
    """
    for rid in rooms:
        room = gs["map"]["rooms"].get(rid)
        if room is None or count_buildings(room, BINDER) <= 0:
            continue
        if gs["rng"].random() < BINDER_CHANCE:
            e["bound_until_ms"] = gs["clock_ms"] + BINDER_MS
            e["path"] = []                      # 定住期间不走，把旧路径清掉
            events.append({"type": "entity_bound", "room": rid, "ms": BINDER_MS})
            return


def _emp_rooms(gs: dict, tid: str) -> list[str]:
    """EMP 打在谁头上。砸房间门就是那一间；**砸共用闸门就是它后面所有的舱室**
    —— 闸门是两个人的共用前线，瘫一半留一半说不通。"""
    if is_gate_id(tid):
        return list(game_map(gs).gates[gate_of(tid)].rooms)
    return [tid]


def _breach(gs: dict, tid: str, events: list[dict]) -> None:
    """门被砸破。两种门两种后果：

    - **房间门**：舱室主人淘汰，房间清空并重置（别人可以再来占）；
    - **共用闸门**（v9）：它不倒人，只是**它后面的门厅对静默体开放** ——
      从这一刻起它才能走进门厅去砸里面那两扇门。闸门不会重建，
      所以套间一旦被破防，之后就只剩两道内门可守。

    淘汰走遗产转移（`_eliminate`），不做原型的「伥鬼」（变节帮静默体）——
    那在合作游戏里是把队友变成敌人。
    """
    if is_gate_id(tid):
        gid = gate_of(tid)
        gate = gs["map"]["gates"][gid]
        gate["broken"] = True
        gate["door_hp"] = 0.0
        events.append({"type": "gate_breached", "gate": gid,
                       "rooms": list(game_map(gs).gates[gid].rooms)})
        return
    room = room_of(gs, tid)
    uid = room["owner"]
    room["owner"] = None
    room["door_level"] = 1
    room["door_hp"] = DOOR_HP[0]
    room["buildings"] = {}
    events.append({"type": "door_breached", "room": tid, "uid": uid})
    if uid is not None and gs["players"][uid]["alive"]:
        _eliminate(gs, uid, events)


def _eliminate(gs: dict, uid: int, events: list[dict],
               cause: str = "breach") -> None:
    """出局：淘汰本人，**遗产平分给存活队友**。

    `cause` 是给客户端文案用的（`breach` 舱室失压 / `entity` 被静默体抓住 /
    `surrender` 弃守）—— 三种死法的提示词完全不同，只靠 uid 分不出来。

    遗产转移是给淘汰者的「还能影响战局」的出口。
    """
    p = gs["players"][uid]
    p["alive"] = False
    p["in_bed"] = False
    p["lying"] = False
    p["eliminated_at_ms"] = gs["clock_ms"]
    heirs = alive_players(gs)
    if heirs:
        share_bio = p["bio"] / len(heirs)
        share_alloy = p["alloy"] / len(heirs)
        for h in heirs:
            h["bio"] += share_bio
            h["alloy"] += share_alloy
    p["bio"] = 0.0
    p["alloy"] = 0.0
    events.append({"type": "player_eliminated", "uid": uid, "cause": cause,
                   "heirs": alive_player_uids(gs)})


def _check_end(gs: dict, events: list[dict]) -> None:
    """终局判定。**只有三条**（v8）：

    ① 打死静默体 → 胜；② 全员失压 → 败；③ 撞上 `STALL_CAP_MS` 的兜底 → 败。

    ③ **不是玩法规则，是防卡死的保险丝**（理由见 `STALL_CAP_MS`）：一局本来就没有
    时长上限，打到分出胜负为止。
    """
    if gs["finished"]:
        return
    if gs["entity"]["killed"]:
        _finish(gs, REASON_KILLED, events)
    elif not alive_players(gs):
        _finish(gs, REASON_WIPED, events)
    elif gs["clock_ms"] >= STALL_CAP_MS:
        _finish(gs, REASON_TIMEOUT, events)


def _finish(gs: dict, reason: str, events: list[dict]) -> None:
    gs["finished"] = True
    gs["phase"] = "over"
    gs["reason"] = reason
    # **只有打死静默体算胜**（v8）。超时兜底与全员失压都是败 —— 救援舰不会来。
    gs["winner"] = "players" if reason == REASON_KILLED else "entity"
    events.append({"type": "game_over", "reason": reason, "winner": gs["winner"]})


# ── 动作 ────────────────────────────────────────────────────


def _require_active(gs: dict, uid: int) -> dict:
    """在局中、没出局、且对局还没结束（**跑动阶段也算**，v4 起）。

    跑动阶段能动作，是因为「倒计时内把钱花掉」正是原型的开局：
    「在倒计时最后五秒你会攒够 25 金币，一定要升级床」。
    """
    if gs["phase"] == "over":
        raise ValueError("对局已经结束了")
    p = gs["players"].get(uid)
    if p is None:
        raise ValueError("你不在这一局里")
    if not p["alive"]:
        raise ValueError("你已经失压出局了")
    return p


def _require_lying(gs: dict, uid: int) -> dict:
    """**躺着才做得成事**（原型：未上床不能建造、不能升级）。

    这条把「躺下」从装饰变成闸门：没躺下的人在场上唯一能做的事是走 ——
    而走廊里遇到静默体就是直接出局。客户端必须把这句话显示出来
    （`要先躺进休眠舱才能建造与升级`），否则就是用户已经报过一次的
    「点了没反应」。
    """
    p = _require_active(gs, uid)
    if not p["lying"]:
        raise ValueError("要先躺进休眠舱才能建造与升级")
    return p


def lie(gs: dict, uid: int) -> dict:
    """躺进休眠舱。**要走到休眠舱的九宫格之内**（v6 收紧，见 `_bed_adjacent`），
    **躺下后再也起不来**。

    这是「进房间 → 躺下 → 开始产资源」里缺的那一步。原型里是点床铺躺下。
    v4 的两处变化：①**跑动阶段就能躺**（原型就是倒计时内上床，躺下是这个游戏
    的开局动作，不是对局动作）；②**没有起身**（`stasis_stand` 连协议一起删了）
    —— 于是躺下是一个**不可逆**决定，而 30 秒倒计时就是给你做这个决定用的。
    """
    p = _require_active(gs, uid)
    if not p["room"]:
        raise ValueError("你还没有舱室")
    if p["lying"]:
        return {"ok": True, "lying": True}
    if not _can_lie(gs, p):
        raise ValueError("要走到休眠舱旁边才能躺下")
    bx, by = game_map(gs).room_bed(p["room"])
    p["pos"] = [float(bx) + 0.5, float(by) + 0.5]
    p["lying"] = True
    p["dir"] = [0.0, 0.0]
    p["in_bed"] = True
    return {"ok": True, "lying": True, "room": p["room"]}


def build(gs: dict, uid: int, kind: str, cell) -> dict:
    """在空格上造建筑。`cell` 是 `"x,y"` 键。

    **只有舱室主人能在自己舱室里造**（v2 暂不做原型的「帮你花钱」）。
    """
    p = _require_lying(gs, uid)
    if kind not in BUILDABLE:
        raise ValueError("不能建造这种建筑")
    key = cell if isinstance(cell, str) else cell_key(*cell)
    if key not in buildable_cells(gs, uid):
        raise ValueError("这一格不能建造")
    limit = room_limit(kind)
    if limit is not None and count_buildings(room_of(gs, p["room"]), kind) >= limit:
        # **错误文案要说清是「满了」不是「不能建」** —— 客户端照同一条上限把菜单项
        # 标成「n/n 已满」，别让玩家点了才知道。上限表见 `ROOM_LIMITS`。
        raise ValueError(f"{building_name(kind)}最多 {limit} 台")
    res, amount = BUILD_COST[kind]
    if p[res] < amount:
        raise ValueError("资源不够")
    p[res] -= amount
    room_of(gs, p["room"])["buildings"][key] = {"kind": kind, "level": 1}
    return {"ok": True, "kind": kind, "cell": key, "level": 1, "cost": {res: amount}}


def upgrade(gs: dict, uid: int, target) -> dict:
    """升级。`target` 是 `"bed"` / `"door"` / 建筑所在的 `"x,y"` 键。

    **能升的只有两种建筑**：哨戒炮与精炼机（各有自己的逐级价与逐级数值，照抄原型，
    见 `增强调研.md` §9.1）。纳米修复器没有升级档（原型也没有）；**四座矿也没有**
    —— 它们是四个并列的建筑，想换更快的矿就拆了重造（`增强调研.md` §10.1）。
    """
    p = _require_lying(gs, uid)
    if not p["room"]:
        raise ValueError("你还没有舱室")
    room = room_of(gs, p["room"])

    if isinstance(target, str) and is_gate_id(target):
        return _upgrade_gate(gs, p, gate_of(target))

    if target == BED:
        lv = p["bed_level"]
        _require_not_maxed(lv, BED)
        # **休眠舱升级要气密门先跟上**（原型的「升级小床必须要门的等级对应」，
        # 见 docs/游戏设计/深空休眠/增强调研.md §9.1）。这条把「先经济还是先防御」
        # 变成一条硬约束：想更快产资源，就必须先把门升上去保住它 —— 而不是
        # 把钱全砸进床、门破人亡。
        #
        # **门槛是间断的**（`BED_DOOR_REQ`）：床 3-4 级只要门 2，然后一路可以
        # 顶到门 5 才卡住 —— 所以「攒一波一次性连升几级床」是原型的标准打法。
        need = bed_door_req(lv + 1)
        if room["door_level"] < need:
            raise ValueError(
                f"休眠舱升到 Lv{lv + 1} 需要{door_name(need)}"
                f"（现在 {door_name(room['door_level'])}）")
        _spend_upgrade(p, BED, lv)
        p["bed_level"] = lv + 1
        return {"ok": True, "target": BED, "level": lv + 1}

    if target == DOOR:
        lv = room["door_level"]
        _require_not_maxed(lv, DOOR)
        _spend_upgrade(p, DOOR, lv)
        room["door_level"] = lv + 1
        room["door_hp"] = door_max(room)   # **升级即满血**，原型最爽的设计，勿改
        return {"ok": True, "target": DOOR, "level": lv + 1,
                "name": door_name(lv + 1)}

    key = target if isinstance(target, str) else cell_key(*target)
    b = room["buildings"].get(key)
    if b is None:
        raise ValueError("这一格没有建筑")
    kind = b["kind"]
    if kind not in UPGRADE_COST:
        raise ValueError("这种建筑不能升级")
    lv = b["level"]
    _require_not_maxed(lv, kind)
    _spend_upgrade(p, kind, lv)
    b["level"] = lv + 1
    return {"ok": True, "target": key, "kind": kind, "level": lv + 1}


def demolish(gs: dict, uid: int, cell) -> dict:
    """拆除，返还建造费的 50%（不追溯升级投入）。"""
    p = _require_lying(gs, uid)
    room = room_of(gs, p["room"]) if p["room"] else None
    key = cell if isinstance(cell, str) else cell_key(*cell)
    b = room["buildings"].get(key) if room else None
    if b is None:
        raise ValueError("这一格没有建筑")
    res, amount = BUILD_COST[b["kind"]]
    refund = amount * DEMOLISH_REFUND
    p[res] += refund
    del room["buildings"][key]
    return {"ok": True, "cell": key, "refund": {res: refund}}


def _credit_gate(gs: dict, gid: str, uid: int, bio: float, alloy: float) -> None:
    """把一笔支出记到**共用闸门**的出资账本上（v13）。

    为什么要记：闸门是一份**公共品**，两个住户都能升能修，于是天然有搭便车
    （`增强调研.md` §13.3 是这一轮唯一一条明确的反面证据：责任分散、纳什均衡＝
    零贡献）。这一轮**不做**「按出资分收益」那类机制（它是另一个系统），
    只做**可见性**那一档 —— 因为「谁出了多少」一旦公开，「两个人都等对方升外门」
    这件事就从一个看不见的僵局变成一个看得见的社交问题。

    记账的单位是**实际花掉的资源**（升级＝主资源＋合金，修补＝生物能），
    只累加、不校验 —— 它是一份展示用的流水，不是裁决依据。
    """
    gate = gs["map"]["gates"].get(gid)
    if gate is None:
        return
    row = gate.setdefault("contrib", {}).setdefault(
        uid, {"bio": 0.0, "alloy": 0.0, "acts": 0})
    row["bio"] += bio
    row["alloy"] += alloy
    row["acts"] += 1


def _upgrade_gate(gs: dict, p: dict, gid: str) -> dict:
    """升**共用闸门**（v9，`suite` 图）。价钱与规则完全照房间门那一套。

    **两个住户谁都能升**，这是「合作」在数值上唯一的落点：闸门是两个人的共用
    前线，账单落在谁头上都说得通 —— 一个人攒经济、一个人攒输出，闸门归谁付
    正是他们要商量的那件事。**只有住在它后面的人能升**（不然 6 个人可以隔着
    半张图替别人升级，责任就散了）。
    """
    gate = gs["map"]["gates"].get(gid)
    if gate is None:
        raise ValueError("没有这扇闸门")
    if gate["broken"]:
        raise ValueError("这扇闸门已经被砸破了")
    if p["room"] not in game_map(gs).gates[gid].rooms:
        raise ValueError("你不住在这扇闸门后面")
    lv = gate["door_level"]
    _require_not_maxed(lv, DOOR)
    _spend_upgrade(p, DOOR, lv)
    _credit_gate(gs, gid, p["uid"], upgrade_cost(DOOR, lv), upgrade_alloy(DOOR, lv))
    gate["door_level"] = lv + 1
    gate["door_hp"] = door_max(gate)      # 升级即满血，与房间门同一条规则
    return {"ok": True, "target": gate_id(gid), "level": lv + 1,
            "name": door_name(lv + 1)}


def patch(gs: dict, uid: int, target_uid: int | None = None,
          gate: str | None = None) -> dict:
    """修补气密门。`target_uid` 省略＝修自己的；给别人修按 1.5 倍加价。

    **远程修补是整套合作机制的核心** —— 它是唯一一个「主动花自己的钱去帮别人」
    的动作。1.5 倍是刻意的：救队友永远比救自己亏，所以救不救是一个真取舍。
    **v4 之后它的分量更重了**：躺下就起不来，所以「帮队友」再也没有第二条路
    —— 走出去救队友的玩法已经删掉（见 `增强调研.md` §9.3）。

    `gate` 是**共用闸门**的 id（v9）：闸门是两个人的前线，所以一律按「帮别人」
    的 1.5 倍算 —— 它是这局里最划算的一笔公共支出，但不该是免费的。
    """
    p = _require_lying(gs, uid)
    if gate is not None:
        obj = gs["map"]["gates"].get(gate)
        if obj is None or obj["broken"]:
            raise ValueError("这扇闸门已经不在了")
        if p["room"] not in game_map(gs).gates[gate].rooms:
            raise ValueError("你不住在这扇闸门后面")
        cost = PATCH_COST_BIO * PATCH_COST_MULT
    else:
        if target_uid is None or target_uid == uid:
            tgt_p, rid = p, p["room"]
        else:
            tgt_p = gs["players"].get(target_uid)
            rid = tgt_p["room"] if tgt_p else None
        if tgt_p is None or not tgt_p["alive"] or not rid:
            raise ValueError("目标不在局中")
        obj = room_of(gs, rid)
        cost = PATCH_COST_BIO * (1.0 if tgt_p is p else PATCH_COST_MULT)
    if p["bio"] < cost:
        raise ValueError("生物能不够")
    if obj["door_hp"] >= door_max(obj):
        raise ValueError("这扇门是满血的")
    p["bio"] -= cost
    healed = door_max(obj) * PATCH_FRACTION
    obj["door_hp"] = min(door_max(obj), obj["door_hp"] + healed)
    if gate is not None:
        _credit_gate(gs, gate, p["uid"], cost, 0.0)
        return {"ok": True, "gate": gate, "cost": cost, "healed": healed,
                "remote": True}
    return {"ok": True, "target_uid": tgt_p["uid"], "cost": cost,
            "healed": healed, "remote": tgt_p is not p}


def surrender(gs: dict, uid: int) -> dict:
    """弃守：等同舱室失压，走遗产转移。

    ⚠️ **跑动阶段（`phase == "run"`）也必须能弃守**，所以判据是 `_require_active`
    而不是 `_require_playing`。用户实测报的「在游戏中点返回想强制退出时没反应、
    闪一下还在原处」有一半就是这里：跑动阶段点「退出房间」，`rm.leave_room` 走到
    这一行抛 `ValueError("对局还没开始")` → REST 500（**而且 500 响应没有 CORS 头，
    浏览器把它报成 CORS 错误**）→ 客户端静默吞掉、人没被淘汰 → 房间页看到
    「房间在游戏中且自己还在局中」又把人弹回对局页。**开局那 30 秒是玩家最想
    退出的时段**（还没躺下、什么都还没投入），偏偏只有那一段是坏的。
    """
    p = _require_active(gs, uid)
    p["surrendered"] = True
    if p["room"]:
        room = room_of(gs, p["room"])
        room["door_hp"] = 0.0
        room["owner"] = None
        room["buildings"] = {}
    events: list[dict] = []
    _eliminate(gs, uid, events, cause="surrender")
    _check_end(gs, events)
    return {"ok": True, "events": events}


def _require_not_maxed(level: int, kind: str) -> None:
    if level >= max_level(kind):
        raise ValueError("已经是满级了")


def _spend(p: dict, res: str, amount: float) -> None:
    if p[res] < amount:
        raise ValueError("资源不够")
    p[res] -= amount


def _spend_upgrade(p: dict, kind: str, level: int) -> None:
    """付一次升级的钱。**主资源 + （第 6 级起）合金**。

    主资源走 `upgrade_res`：**矿是合金**、其余是生物能。
    两种资源分别报错、**不要合成一句「资源不够」**：升级弹窗要把缺的那样说出来，
    否则玩家只看到「点了没反应」（原型的「有前置但看不出来」是同一类事故）。
    """
    res = upgrade_res(kind)
    main = upgrade_cost(kind, level)
    alloy = upgrade_alloy(kind, level)
    if p[res] < main:
        raise ValueError("合金不够" if res == "alloy" else "生物能不够")
    if alloy > 0 and p["alloy"] < alloy:
        raise ValueError("合金不够")
    p[res] -= main
    p["alloy"] -= alloy


# ── 结算 ────────────────────────────────────────────────────


def compute_ranking(gs: dict, players_info: list[dict]) -> list[dict]:
    """终局排名：**存活优先 → 存活时长倒序 → 座位号**。

    合作塔防里所有人共享同一个结果（打死静默体 / 到点没打死 / 全员失压），
    所以名次记的是「谁撑得更久」，而不是胜负。
    """
    entries = []
    for info in players_info:
        uid = info["uid"]
        p = gs["players"].get(uid)
        entries.append({
            "seat": info["seat"],
            "uid": uid,
            "username": info["username"],
            "nickname": info.get("nickname"),
            "alive": bool(p and p["alive"]),
            "surrendered": bool(p and p["surrendered"]),
            "room": p["room"] if p else None,
            "survived_ms": (p["eliminated_at_ms"] if p and p["eliminated_at_ms"]
                            is not None else gs["clock_ms"]),
        })

    entries.sort(key=lambda e: (0 if e["alive"] else 1, -e["survived_ms"], e["seat"]))

    result = []
    for i, e in enumerate(entries):
        rank = i + 1
        if i > 0:
            prev = entries[i - 1]
            if (e["alive"] == prev["alive"]
                    and e["survived_ms"] == prev["survived_ms"]):
                rank = result[-1]["rank"]
        result.append({**e, "rank": rank})
    return result


def _owns_room(gs: dict, p: dict) -> bool:
    """这个玩家现在还占着 `p["room"]` 吗（弃守/门破后房间会被清空或换主）。"""
    rid = p["room"]
    return bool(rid) and room_of(gs, rid)["owner"] == p["uid"]


def _map_payload(gs: dict) -> dict:
    """`game_to_sync` 里的 `map`：**整张图的几何** + 每间舱室/每扇闸门的**状态**。

    v9 起几何由服务端下发（`stasis_map.Map.payload`），客户端不再镜像一份 ——
    这是「多张地图」能做起来的前提（见 `stasis_map.py` 文件头第 1 条）。
    一局只发几次（开局 + 离散动作之后），**不进 5 Hz 的 tick**。
    """
    p = game_map(gs).payload(shapes_of(gs))
    for rid, room in gs["map"]["rooms"].items():
        p["rooms"][rid].update({
            "owner": room["owner"],
            "door_level": room["door_level"],
            "door_hp": round(room["door_hp"], 1),
            "door_max": door_max(room),
            "emp": gs["clock_ms"] < room["emp_until_ms"],
            "buildings": [{"cell": k, "kind": b["kind"], "level": b["level"]}
                          for k, b in sorted(room["buildings"].items())],
        })
    for gid, gate in gs["map"]["gates"].items():
        p["gates"][gid].update({
            "level": gate["door_level"],
            "hp": round(gate["door_hp"], 1),
            "max": door_max(gate),
            "broken": gate["broken"],
            "emp": gs["clock_ms"] < gate["emp_until_ms"],
            # **出资账本**（v13）：`{"1": {"bio": 320, "alloy": 64, "acts": 3}, ...}`。
            # 键是 uid 的字符串形式（JSON 的键只能是字符串）。
            "contrib": {str(u): {"bio": round(c["bio"], 1),
                                 "alloy": round(c["alloy"], 1),
                                 "acts": c["acts"]}
                        for u, c in (gate.get("contrib") or {}).items()},
        })
    return p


def snapshot(gs: dict) -> dict:
    """战绩快照：保住每人的舱室、建筑、存活时长与静默体终局状态。"""
    return {
        "game": "stasis",
        "num_players": gs["num_players"],
        "seat_uids": list(gs["seat_uids"]),
        "reason": gs.get("reason"),
        "winner": gs.get("winner"),
        "clock_ms": gs["clock_ms"],
        "entity": {"level": gs["entity"]["level"], "killed": gs["entity"]["killed"]},
        # **地图 id 与名字都要存**（v9 起有多张图）：id 用于判定，名字给战绩页显示
        # ——「这一局打的是哪张图」是玩家会想知道的事，而客户端没有那张表
        # （几何只有服务端一份，名字也一样，不要为了显示再抄一张表到客户端）。
        "map": {"id": gs["map"]["id"],
                "name": game_map(gs).name,
                "shapes": dict(gs["map"]["shapes"])},
        "players": {
            str(uid): {
                "room": p["room"],
                "bed_level": p["bed_level"],
                "alive": p["alive"],
                "surrendered": p["surrendered"],
                "eliminated_at_ms": p["eliminated_at_ms"],
                # **必须确认那间房现在还是他的**：弃守 / 门破后房间会被清空甚至被
                # 别人重新占下，只看 `p["room"]` 会把别人的门等级与建筑写进他的战绩。
                "buildings": ([{"cell": k, "kind": b["kind"], "level": b["level"]}
                               for k, b in sorted(room_of(gs, p["room"])["buildings"].items())]
                              if _owns_room(gs, p) else []),
                "door_level": (room_of(gs, p["room"])["door_level"]
                               if _owns_room(gs, p) else 1),
            }
            for uid, p in gs["players"].items()
        },
    }


def _entity_snapshot(gs: dict) -> dict:
    e = gs["entity"]
    return {
        "level": e["level"],
        "hp": round(e["hp"], 1),
        "max_hp": round(e["max_hp"], 1),
        "state": e["state"],
        "pos": [round(e["pos"][0], 2), round(e["pos"][1], 2)],
        "path": e["path"],
        # 正在砸的目标：房间 id 或 `"gate:G1"`（客户端按 `gate:` 前缀分辨）
        "target_door": e["target_door"],
        "target_uid": e["target_uid"],
    }


def dynamic_snapshot(gs: dict) -> dict:
    """**每 tick 广播**的精简层（5 Hz）：只发会动的东西。

    布局（房型 / 门 / 建筑）走 `game_to_sync` 的低频层，不进这里 —— 见设计文档 §2.6。
    """
    e = gs["entity"]
    return {
        "clock_ms": gs["clock_ms"],
        "run_ms": max(0, gs["run_ms"]),
        "phase": gs["phase"],
        "entity": _entity_snapshot(gs),
        "players": {
            str(uid): {
                "pos": [round(p["pos"][0], 2), round(p["pos"][1], 2)],
                "room": p["room"],
                "lying": p["lying"],
                "in_bed": p["in_bed"],
                "bio": int(p["bio"]),
                "alloy": int(p["alloy"]),
                "alive": p["alive"],
                # 「现在能不能躺下」——**客户端不复刻这个判据**，按钮的显隐直接
                # 读它（理由见 `can_lie`）。5 Hz 下发，最多晚一个 tick。
                "lie": _can_lie(gs, p),
            }
            for uid, p in gs["players"].items()
        },
        "doors": {
            rid: {"hp": round(room["door_hp"], 1), "max": door_max(room),
                  # 应急力场的护盾还剩多久（0 = 没开）。客户端据此画罩子 ——
                  # **它必须看得见**：那 3 秒里门不掉血，不标出来玩家会以为
                  # 「门卡住了」。
                  "shield_ms": max(0, room.get("shield_until_ms", 0) - gs["clock_ms"])}
            for rid, room in gs["map"]["rooms"].items()
            if room["owner"] is not None
        },
        # 共用闸门的血量也走 tick（被砸时它每 tick 都在掉）。最多 6 扇，
        # 三个数字一条，对带宽没有影响。
        "gates": {
            gid: {"hp": round(g["door_hp"], 1), "max": door_max(g),
                  "broken": g["broken"]}
            for gid, g in gs["map"]["gates"].items()
        },
        # **表现层弹丸**：哪些哨戒炮正在开火、朝哪儿打（客户端据此画枪口焰 /
        # 弹丸 / 命中冲击环，见 `增强调研.md` §10.3）。
        #
        # ⚠️ **引擎里没有弹丸实体，也不会因为这一层引入飞行时间** —— 伤害仍然是
        # `_combat` ③ 的连续 DPS（`e["hp"] -= dps_in_range(...) * dt`）。把弹丸做成
        # 真实体是**平衡改动**（DPS 会开始依赖距离），不该混在美术轮里做。
        # 所以这里只发「谁在打、打谁」，一发子弹的节奏由客户端自己定。
        # ⚠️ **再生态也要发**：v10 起它回血时不再无敌（射程够得着的炮台一直在打），
        # 这里原来挂着一条「再生态不发」的旧假设 —— 那会让玩家看着它当面掉血
        # 却一发弹道都没有（表现层在说谎）。**发不发只由「有没有塔够得着」决定。**
        "shots": [
            {"cell": key, "level": lv,
             "tx": round(e["pos"][0], 2), "ty": round(e["pos"][1], 2)}
            for key, lv in turrets_in_range(gs, e["pos"])
        ],
    }


def game_to_sync(gs: dict, uid: int) -> dict:
    """完整状态（进房 / 重连 / 开局时下发）。

    这是**合作**游戏，舱室布局对所有人公开（队友在造什么本来就该看得见），
    所以不做逐人裁剪 —— 与 UNO 的手牌私有是两回事。
    """
    return {
        "game": "stasis",
        "num_players": gs["num_players"],
        "seat_uids": list(gs["seat_uids"]),
        "seat_colors": [gs["player_colors"].get(u, "") for u in gs["seat_uids"]],
        "your_seat": seat_of(gs, uid),
        "your_uid": uid,
        "phase": gs["phase"],
        # 本局难度（客户端只用于显示；数值裁决在引擎里）
        "difficulty": gs.get("difficulty") or DEFAULT_DIFFICULTY,
        "difficulty_label": difficulty_spec(gs)["label"],
        "run_ms": max(0, gs["run_ms"]),
        "clock_ms": gs["clock_ms"],
        # **参考时长，不是终点**（v8）：一局没有时长上限，客户端拿它画不了进度条，
        # 顶部那栏改成**正计时**（`已坚守 M:SS`）。留着是因为「这一局大概多长」在
        # 文案与测试里还有用。
        "game_ms": GAME_MS,
        "map": _map_payload(gs),
        "entity": _entity_snapshot(gs),
        "players": {
            str(u): {
                "seat": p["seat"],
                "room": p["room"],
                "pos": [round(p["pos"][0], 2), round(p["pos"][1], 2)],
                "lying": p["lying"],
                "in_bed": p["in_bed"],
                "bio": int(p["bio"]),
                "alloy": int(p["alloy"]),
                "bed_level": p["bed_level"],
                "alive": p["alive"],
                "surrendered": p["surrendered"],
            }
            for u, p in gs["players"].items()
        },
        "finished": gs["finished"],
        "reason": gs["reason"],
        "winner": gs["winner"],
    }
