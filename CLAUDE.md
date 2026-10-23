# GameHub 项目说明（项目级 CLAUDE.md）

移动优先的小游戏对战平台：Expo（React Native + react-native-web）客户端，一套代码产出 Android APK 与手机/PC 浏览器网页；FastAPI + SQLite 后端，房间制联网对战。

- 仓库根：以当前克隆目录为准；以下命令默认从仓库根执行。
- 后端：`0.0.0.0:50541`（公网基址见 `server/config/config.json` 的 `public_base`，
  可用环境变量 `PUBLIC_BASE` / `IP` 覆盖 —— 仓库里不存具体地址，见「配置与密钥」）
- Web dev server：`50540`（Metro 热更新，常驻运行，勿随意重启）
- **启停服务一律用 `scripts/{start,stop,restart,status}.sh`**，不要手写 `ps aux | grep ... | kill`——本机还跑着别的项目的 uvicorn/vite/expo，会误杀（见「运维脚本」）
- 全局协作规则见 `~/.claude/CLAUDE.md`（commit 须用户明确要求；知识库写入先问；临时脚本用完删）
- **公开仓库**：开发走 `dev/<用户名>` 分支 → 测试通过 → 联系维护者合并到 `main`。
  **远端 `main` 是快照式提交、不含开发历史**（历史只在维护者本机，别对 main 做 force-push/mirror）。
  分支模型、合并前的测试要求、提交信息规范、敏感信息红线见 `docs/协作开发.md`；
  地址与密钥的取法见下文「配置与密钥」——**仓库里不存任何具体地址与口令**。

## 文档索引与查阅规则（**先读这里**）

**CLAUDE.md 只记跨游戏的通用方法论、约定与注意事项。** 每款游戏的玩法、数值、
交互、协议与专属踩坑都在 `docs/游戏设计/<游戏中文名>/` 下，**不要回写到本文件**。

| 要做什么 | 先读 |
|---|---|
| 改某一款游戏 | `docs/游戏设计/<游戏中文名>/` 下的**全部**文档（通常 `设计.md` + `增强调研.md`；深空休眠另有 `协议.md`） |
| 查跨游戏设计规范 / 游戏索引 | [`docs/游戏设计/README.md`](docs/游戏设计/README.md)（含人数、人机、交互主轴与通用 UI 规范） |
| 做任何移动端交互 / 动效 / 无障碍 | [`docs/移动端体验设计方法论.md`](docs/移动端体验设计方法论.md) |
| 改 WS / HTTP 消息 | [`docs/接口协议.md`](docs/接口协议.md)；深空休眠同时改 `docs/游戏设计/深空休眠/协议.md` |
| 跑测试 / 发布前自检 | [`docs/测试矩阵.md`](docs/测试矩阵.md) |
| 看版本与待办 | [`docs/更新计划.md`](docs/更新计划.md) |
| 了解玩法（面向用户） | [`docs/使用指南.md`](docs/使用指南.md) |
| 分支 / 合并 / 敏感信息 | [`docs/协作开发.md`](docs/协作开发.md) |

**游戏索引**（详情以 `docs/游戏设计/README.md` 为准；目录名就是游戏中文名，
例如深空休眠 → `docs/游戏设计/深空休眠/`）：

| 分类 | 游戏 |
|---|---|
| 棋类 | 五子棋 / 中国象棋 / 军棋 / 翻转棋 / 重力四子棋 / 点格棋 / 海战棋 |
| 桌游 | UNO / 飞行棋 |
| 各玩各的（竞速/跑分） | 2048 / 俄罗斯方块 / 扫雷 |
| 合作 | 深空休眠 |
| 单人 | 蜘蛛纸牌 |

> 新增游戏时：先按「先联网调研」一节把结论归档到
> `docs/游戏设计/<游戏中文名>/增强调研.md`，再写 `设计.md`，然后接引擎与客户端
> （接入清单见 `docs/游戏设计/README.md` 与本文「引擎层」）。

## 目录结构

```
app/                    # Expo 客户端（RN + Web）
├── App.tsx             # 入口：navigation container + linking 配置 + initBackGuard
├── app.json            # version/versionCode（与 server/config/config.json 双写）
├── assets/             # 图标 PNG；assets/svg/ 为矢量源稿
└── src/
    ├── navigation.tsx  # 路由栈：Auth → MainTabs(游戏/房间/社交/我的) → Room → 各对局屏 → Admin
    ├── backGuard.tsx   # 返回操作统一体系（见下文，改动前必读注释）
    ├── help.tsx        # 游戏规则帮助系统（GAME_HELP 内容库 + GameHelpButton）
    ├── icons.tsx       # react-native-svg 矢量图标组件
    ├── api.ts          # HTTP 客户端（注册/登录/房间/历史/版本检查）
    ├── socket.ts       # 房间 WS 客户端
    ├── auth.tsx        # 登录态 Context
    ├── theme.ts        # 颜色/字号/间距（颜色只有这一个来源）
    ├── toast.tsx       # 应用内提示（替代 window.alert）
    ├── appUpdate.ts    # 版本检查与 APK 下载
    ├── deepLink.ts     # 初始 URL 记录：未登录打开 /room/:code，登录后恢复并自动进房
    ├── roomSettings.tsx# 房间级玩法设置：只按服务端下发的 kind 画控件
    ├── <游戏展示层>    # 各游戏的规则解析 / 棋盘几何 / 牌面美术 / 渲染层
    │                   # 例：uno.ts + UnoCardView.tsx、luzhanqi.ts + luzhanqiBoard.ts
    │                   #     + LuzhanqiBoardView.tsx、stasisRules.ts + StasisBoard.tsx
    │                   #     + StasisPanels.tsx + stasisArt.tsx + StasisShots.tsx
    │                   # 每款游戏的文件职责与实现约定见其 docs/游戏设计/<游戏>/设计.md
    └── screens/        # AuthScreen/GamesTab/RoomsTab/SocialTab/ProfileTab/RoomScreen/
                        # 各对局屏（Gomoku/Mine/Reversi/Battleship/Dots/Ludo/Game2048/
                        # Xiangqi/ConnectFour/Tetris/Uno/Stasis/Luzhanqi/Spider）
                        # + HistoryScreen/AdminScreen
server/
├── config/config.json  # 端口、jwt_secret、admin 种子、public_base、app.version_code/name
│                       # **不进版本库**（本机真值），模板 config.example.json
├── data/gamehub.db     # SQLite（users + game_records 战绩表）；data/app-release/ 托管 APK
└── app/
    ├── main.py         # FastAPI 入口：startup 钩子（async）、routers 挂载、静态托管
    ├── cfg.py          # config.json 加载（import 时读取 → 改配置必须重启后端）
    ├── database.py     # SQLite 建表/users/战绩落库（save_game_record）
    ├── persistence.py  # 房间实时落盘 + 启动恢复（热重启，见「热重启」）
    ├── security.py     # JWT + 密码哈希
    ├── room_manager.py # 房间生命周期：创建/加入/准备/开始/断线保座(120s)/对局结束回调
    ├── room_settings.py# 房间级玩法设置的**唯一注册表**（下发统一的 room.settings）
    ├── ws.py           # WS 路由与消息分发（各游戏 move/胜负广播/认输/断线判负）
    ├── game/           # **各游戏的纯引擎与人机 AI**（无 IO、不碰 asyncio）
    │   ├── <游戏>.py   # 引擎：五子棋/扫雷/翻转棋/海战棋/点格棋/飞行棋/2048/象棋/
    │   │               # 重力四子棋/俄罗斯方块/UNO/深空休眠/军棋/蜘蛛纸牌
    │   ├── <游戏>_ai.py# 人机 AI（扫雷/2048/俄罗斯方块无 AI）
    │   └── __init__.py # 这层的约定（引擎不认识时间/不发消息；AI 只看下发过的信息）
    └── routers/        # auth / admin / rooms / app_update / history
bin/
├── release-apk.sh      # 发布：版本号双写 → prebuild → gradlew assembleRelease → 拷贝 APK → curl 验证
└── render_icons.py     # assets/svg/*.svg → PNG（headless chromium 截图）
scripts/                # 运维脚本（见「运维脚本」）——启停本项目服务只走这里，别手写 kill
├── lib.sh              # 公共库：三重指纹定位进程、进程树 kill、端口/HTTP 等待（被 source）
├── start.sh / stop.sh / restart.sh / status.sh
tmp/                    # gitignore：run/*.pid + log/{backend,web}.log
test/                   # 浏览器 E2E 与视觉审计（见 docs/测试矩阵.md）
docs/                   # 使用指南 / 接口协议 / 协作开发 / 更新计划 / 测试矩阵 /
                        # 移动端体验设计方法论 / design/图标草稿.html
                        # 游戏设计/README.md（跨游戏通用规范 + 索引）
                        # 游戏设计/<游戏中文名>/{设计.md, 增强调研.md[, 协议.md]}
```

## 新游戏 / 玩法增强：先联网调研，再动手

**不要凭记忆闭门造车**。开新游戏、做玩法增强、做新功能之前，先搜一遍再动手——
通用方案（别人怎么做、有哪些成熟变体、踩过什么坑）网上基本都有，先摸清再设计，
能省掉大量「做完了才发现方向不对」的返工。

### 用法

Key 从环境变量 **`ARK_AGENT_API_KEY`** 读（Agent Plan 的 Key 配置 Harness 后同样适用于豆包搜索，
脚本已内置该回退，**直接调用即可，不要手动传 `--api-key`**）：

```bash
cd ~/.claude/skills/byted-web-search && python3 scripts/web_search.py "搜索词" --count 10
```

常用参数：`--query-rewrite`（口语化长问改写，召回更好）、`--time-range OneWeek|OneDay|YYYY-MM-DD..YYYY-MM-DD`、
`--auth-level 1`（仅权威来源）、`--type image`（找图/找参考视觉）。个人免费额度 500 次/月。

**不要预检查环境变量**：直接跑，只有报错（未找到凭证 / `invalid_api_key` / `10403`）时才引导用户配 Key。

### 搜什么：按维度各搜一轮

单一视角搜出来的东西往往是偏的。游戏相关需求**中英文各搜一遍**（英文资料在
游戏设计类话题上往往更深），核心结论**交叉验证 2 个以上来源**：

| 维度 | 搜什么 |
|---|---|
| **玩法规则** | 经典变体 / 房规 / 道具系统 / 「为什么它耐玩」 |
| **视觉** | 主流实现的美术风格与配色惯例 |
| **交互** | 交互范式、反馈动效、触屏与小屏的取舍 |

非游戏类需求同理自定维度（通用方案 / 已知坑 / 性能与兼容性陷阱等）。

### 结论要归档

调研结论**归档到 `docs/游戏设计/<游戏中文名>/增强调研.md`（带来源链接）**，再据此写实现计划。
**「哪些方案采纳、哪些不采纳、为什么」必须留在文档里**，否则下次遇到同一问题要重新搜一遍
（有意不做的方向尤其要写清楚理由，否则会被反复重新评估）。

## 跨游戏通用约定

### 引擎层（`server/app/game/`）

- **引擎一律放 `server/app/game/`**（导入路径 `from app.game.gomoku import ...`），
  房间/传输/落盘/配置留在 `server/app/`。引擎**不认识时间**（深空休眠只认 `dt_ms`）、
  不读数据库、不发消息；人机 AI 的入参就是「真人看到的那份视图」，不许偷看未下发的信息。
- 这一层是**纯函数 + 显式入参**，所以单测能毫秒级跑完整局
  （`test/balance_stasis.py` 就是靠它）。
- **加一个游戏 = 在 `game/` 加引擎（+ 可选 AI）**，再按「新游戏接入清单」接线
  （引擎 + AI + 战绩快照 + 帮助文案 + 返回双路径 + 矢量图标 + RoomsTab/GamesTab 入口 +
  RoomScreen 游戏类型/人数 + HistoryScreen 快照渲染）。漏接 RoomScreen 映射会显示成
  「五子棋」，已踩坑。

### 客户端不实现规则（服务端权威）

所有「这一步能不能做」的判断都由服务端下发，客户端只做高亮/禁用/动画：

- 象棋/军棋的合法着法（`legal`）、UNO 的可出牌（`playable`/`jumpable`）、
  飞行棋的走子路径（`path`/`segments`）、俄罗斯方块的形状与踢墙顺序（`shapes`/`kicks`）。
- **凡是「服务端有一份表、客户端照着做」的地方，那张表就随消息下发**。
  客户端抄一份的代价不是「可能不一致」，而是**静默的功能缺失**（UNO 曾因客户端只认
  经典两张黑牌，`W+6`/`W+10`/`W+RD4` 点了不弹选色框，且无补就办法）。
- **私有信息压根不下发**，不是渲染时藏：UNO 手牌、军棋暗棋的对手身份、2048/俄罗斯
  方块/扫雷的个人盘面。回归要写在 **WS 层**（浏览器层看不见线路上的字节）。
- **逐人下发的消息要带够字段**：多人/双视角游戏的 `game_start` 与 `sync` 必须逐人下发
  （飞行棋附 `your_seat`、2048 附 `your_color` 且只给自己的盘面），不能全员同一份广播。

### 蜘蛛纸牌（单人）

`game/spider.py` 为纯规则引擎，`spider` 每账号一个私有牌局；首页直接开始/续玩。
**离线不判负，也不执行多人房间闲置清理或恢复后的 waiting 清退**。
`spider_action` 经 revision 校验后完成动作、落盘、再回权威视图；暗牌和余牌顺序不下发。
撤销保留最近 200 步；新局与自然终局经 `_set_game_over` 记录战绩，单人返回只保存。
接入说明、协议与验证见 `docs/游戏设计/蜘蛛纸牌/设计.md`，调研见同目录 `增强调研.md`。
回归：`server/test/test_spider.py`、`server/test/test_ws_spider.py`、`test/e2e_spider_smoke.py`。

### 房间与对局流转

创建/加入房间（6 位码）→ 准备 → 房主开始 → WS 对局（`ws.py` 按 game 类型分发）→
胜负/平广播 → `room_manager.set_game_end_callback(save_game_record)` 落库战绩 →
房间**回到 `waiting`**（保留，房主可直接续开下一局）。断线保座 120s，超时判负。

- **房间只有两种状态**：`waiting` / `playing`，**没有 `finished`**。对局结束调
  `_back_to_lobby()` 放回 `waiting`（`round += 1`），房主可直接 `start` 续开。
  房主离开时按座位顺序转移给下一个真人。
- **等待大厅里可以互换座位**（v1.16）：座位 = `room["players"]` 下标，开局时决定先手 /
  出牌顺序，所以**只在大厅里能换**。**必须双方同意** —— 人机直接算同意，真人之间
  服务端把 `swap_request` 转给对方弹窗确认。四种结果各回一句 `swap_result.message`，
  **一条都不静默**。待答请求存 `room["swap_requests"]`（不进 `room_to_dict`），
  发起人退房或开局时作废。
- **游戏级设置一律「进房之后再选」**（v1.13/v1.15）：`POST /api/rooms` 只收游戏类型；
  房主在等待大厅里改，服务端校验「只有房主 / 只有 `waiting`」，然后**广播一条 `lobby`
  给全房**（只回给房主的话，别人看到的还是旧值）。
  - `server/app/room_settings.py` 是「这个房间能调什么」的**唯一来源**，下发统一的
    `room.settings`（`{key,label,kind,value,value_label,blurb,choices?,toggle_labels?,
    visible_when?,in_summary?}`），客户端 `app/src/roomSettings.tsx` 只按 `kind` 画控件、
    **一行规则都不写**。改设置只有一条消息 `room_setting {key, value}`。
    **加一条房规 = 在注册表里加一项。**
  - **`store` 键一律不动**：注册表声明「这项设置写房间字典里的哪个键」，统一的是
    对外协议，不是内部字段。
  - **`value_label` 与 `blurb` 由服务端翻译好**（摘要要显示「中级 / 自定义」，
    客户端 `choices.find(...)` 可能在 `custom` 这类不在 choices 里的值上落空）。
  - **`visible_when` 由客户端求值**（只在某档下有意义的设置，常显会变成「点了没反应」）。
  - **旧消息留「墓碑」不留别名**：收到就回「客户端版本过旧」。别名等于把重复再养一遍；
    纯删又会变静默（应用内更新是用户手动点的，老 APK 不会被自动拦下）。
  - **界面**：房间页只留一行「玩法设置 + 当前摘要 ›」，点开是底部抽屉。芯片 44pt、
    边框用 `borderStrong`、激活态是「底色 + 白字 + 勾 + 加粗」四条通道。
    **非房主 / 对局中整排芯片不渲染**，只留当前值与说明。
- **等待房掉线不能立即移出玩家**：客户端从对局页返回房间是「先关对局页 socket、
  再重连房间页」，两步之间有窗口期；立即移出会让房间瞬间没人而被销毁（症状：
  **点「返回房间」提示房间不存在**）。改为只标记离线 + 记 `empty_since`，重连即取消。
- **掉线只在游戏中存在**：`playing` 才有 120s 保座；等待房（含刚打完一局的房）掉线
  只标离线，界面不显示（`RoomScreen` 的「断线」胶囊有 `room.status === 'playing'` 守卫）。
- **GC 只有一条规则**：没有真人在线的房间——`waiting` 记 `empty_since` 并由
  `WAITING_EMPTY_TTL=60s` 回收，`playing` 由 `ROOM_IDLE_TTL=1800s` 回收
  （`_sweeper_loop` 每 60s 跑）。显式退出/被移出导致「连真人成员都没有了」时才立即销毁。
- **一个人可以同时在多个房间**（v1.7 起）：不再有「只能在一个等待房」的限制；
  房间按 (房间, 玩家) 维度管理连接与回收，没人管的等待房由 sweeper 回收。
- **终局要把真人非房主的 `ready` 复位**（v1.7）：`_back_to_lobby()` 里统一复位。
  房主本来就不用准备，人机自动准备，两者都不参与复位。
- **落盘的 `finished` 房要在 `restore_rooms` 里丢掉**：它是永远回收不掉的僵尸房。
- **房间列表**：`get_all_rooms` 返回全部 waiting/playing 房间（等待房必然有真人）；
  `join_room` 的判断顺序是 **① 已是本房成员 → 直接放行**（覆盖游戏中重连 / 结束后重进 /
  等待房重进）→ ② `playing` → 抛「游戏已开始」→ ③ 密码/满员。**①必须在②之前**。
  前端点房间卡片必须走 `joinRoom` 接口再导航，不能直接跳房间页。
- **结算流转**：结算弹窗只有「返回房间」按钮，**没有「再来一局」**。续开新局统一回
  房间大厅由房主点「开始游戏」（rematch 协议已删干净）。
- **人机（bot）**：房主在等待大厅 `add_bot` → 合成玩家（负 id、`is_bot=True`、自动
  ready、无 WS 连接但视为在线）入座 → 轮到 bot 时 `asyncio` 延迟 0.8~1.2s 调
  `*_ai.best_move` 走正常广播路径。bot 以 nickname 入战绩 `players_json`，不查 users 表。
  **满员即止**，`remove_bot` 按 `bot_id` 精确移除。**重搜索的 AI 必须丢进线程池**
  （`asyncio.to_thread` + 盘面副本）——同步搜索直接 await 会卡住整个事件循环。
  哪些游戏支持人机见 `docs/游戏设计/README.md` 索引表。
- **各游戏的人数上限、开局人数校验、出局后是否留在局中**都以各游戏文档为准；
  RoomScreen 的 `needMorePlayers` 与服务端校验必须对齐。

### 服务端 tick（当前只有深空休眠）

其余游戏都是请求-响应模型。深空休眠绕不开时间（单位持续移动、门血持续下降、
炮塔持续开火），是全项目**唯一**一个服务端 tick。通用约定（细节与论证见
`docs/游戏设计/深空休眠/设计.md` §2 / §10）：

- **引擎不认识时间，只认识 `dt_ms`**（`advance(gs, dt_ms, rng)`）：不碰 asyncio、
  不读墙钟。单测能毫秒级跑完整局，热重启不需要恢复 tick 状态，全员离线时暂停＝不调 advance。
- **游戏内时钟由 tick 累加，绝不用墙钟**（墙钟在热重启后会跳变）。
  **唯一用墙钟的是断线保座 120 秒**。
- **任务泄漏用对账式，不做配对式**：tick 每轮检查 `status` 不对就 `break`（自净），
  `finally` 里摘掉自己；sweeper 每轮补一个缺失的任务。
- **tick 内零写盘**，只刷内存的 `last_activity`；每隔若干 tick 落一次关键帧；
  离散动作才立即落盘。**瞬态输入（如移动方向）不落盘。**
- **广播分层**（高频动态 / 低频布局 / 立即事件），不要把全量状态塞进每个 tick。
- **卡顿排查四条结论（2026-10-19，Windows + Edge 4 倍降速实测）**：
  ①**web 服务要跑生产模式 bundle**（`scripts/start.sh` 默认 `--no-dev --minify`）——
  手机浏览器玩家连的就是它，开发版每 8 秒卡 2.8 秒、生产版 0；②**远端单位必须插值**：
  队友/静默体走 `remoteRef` 的缓冲一帧插值（段长取实测 tick 间隔，>2 格吸附），
  原来直接 `setValue` 只有 5 帧/秒；③**常驻挂载的面板用「前后都关着就跳过」的 memo**
  （`StasisPanels` 末尾的 `whileHidden`），门血/闸门血**数值没变不换引用**（`sameHp`）；
  ④**服务端 tick 按截止时间排、广播走逐连接发送链**（`ws._stasis_enqueue`）：一个卡住
  的连接只会丢自己的 tick 快照，不会再拖住全房间；状态/事件/终局照样必达保序。
  回归：`test_stasis_send.py`。**别在 tick 路径上再写 `await ws.send_json`。**
- 将来做炸弹人/贪吃蛇这类需要 tick 的游戏，可以复用这套骨架；能不用 tick 的
  （如俄罗斯方块的「客户端攒动作、服务端重放」）仍然优先不用。

### 返回操作（backGuard.tsx）

优先级：弹窗 > 对局页确认 > 主 Tab 双击退出（2s 窗口）。

- 原生：`BackHandler` 单监听（initBackGuard）+ 各对局屏 `beforeRemove` 弹确认（仅原生）。
- Web：模块加载即挂 popstate 监听（**必须早于 react-navigation linking**），用
  `stopImmediatePropagation` + `history.forward()` 取消导航，再派发 `gh:confirm-leave`
  事件让对局屏自己弹确认框；主 Tab 页用 `useExitBackGuard` 垫一个 guard history 条目。
- **程序化 vs 用户返回**：backGuard 包装了 `history.go/back/forward`，凡程序化遍历
  （react-navigation 的 goBack、Tab 切换的 go(-1)）先置 `suppressPop`（+400ms 安全网）
  令 popstate 直接放行；用户真实返回不走这些方法，才进入提示/确认逻辑。
- 新弹窗一律接 `useModalBackGuard(visible, close)`；新对局屏加入 `PROTECTED_SCREENS`
  并实现双路径（beforeRemove 仅原生 + `onConfirmLeave(handler)` 监听——backGuard 导出的
  助手，原生端自动 no-op，**禁止**在屏幕里裸写 `window.addEventListener`）。
  ⚠️ **漏一个对局屏不会有任何报错**，只表现为「点了返回没反应、闪一下还在原处」。
  `visual_audit.py` 的 `audit_back_wiring` 拿 `navigation.tsx` 的路由表逐个对账。
- **禁止**在对局屏用 `beforeRemove.preventDefault()` 处理 Web 返回（会造成
  history/路由失同步 → 重挂载死循环，已踩坑）。

### 热重启（房间实时落盘）

`server/app/persistence.py` 把整房 JSON 写穿到 SQLite `rooms` 表，进程重启后
`startup` 里 `persistence.init()` → `rm.restore_rooms()` → `reschedule_after_restore()`
把对局原样接回来（客户端 WS 自动重连即回到原局）。

- **落盘收口**：`room_manager._touch_activity` 是所有状态变更的写盘点。走子类动作
  统一是「内部实现 + `_finish_action` 收尾」两段式——**必须在全部变更完成后再落盘**，
  中途落盘会写下半更新状态（五子棋 `next` 还没切换 → 热重启后同一人连走两步）。
- **编码器**：`int` 键 dict → `__kv__`、`set` → `__set__`、`random.Random` → `__rng__`
  （保存 `getstate()`，恢复后序列延续，保证 2048 公平性）。未 `init()` 时全部 no-op。
- **恢复语义**：所有真人标记离线（不判负！），playing 房启动空房计时；
  `reschedule_after_restore` 重排 bot 回合，并对 waiting 房给 60s 宽限期。
- **客户端**：`RoomSocket.pause()/resume()` —— 页面失焦（被对局页盖住）时暂停自动重连。
  服务端每个 (房间,玩家) 只保留一条连接；RoomScreen 在 `useFocusEffect` 里失焦
  `pause()`、聚焦 `resume()+reconnect()`。
- 回归：`test/e2e_hot_restart.py`。

### 配置与密钥（公开仓库，改动前必读）

**仓库里不存任何具体地址与口令。** `server/config/config.json` 装的是本机真值，
**已 gitignore**（模板 `server/config/config.example.json` 进版本库）；环境变量优先级更高：

| 环境变量 | 覆盖 | 备注 |
|---|---|---|
| `PUBLIC_BASE` / `IP` | `public_base` | 公网基址；`IP` 只给主机，端口取配置里的 `port` |
| `JWT_SECRET` | `jwt_secret` | 空/占位时由 `cfg.py` 生成并持久化到 `server/config/jwt_secret.key`（0600，gitignore） |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin.*` | **没有内置默认口令**（缺一就不建号）；密码显式给出时启动会重置已存在账号 |
| `EXPO_PUBLIC_API_BASE` / `EXPO_PUBLIC_WS_BASE` | 客户端地址 | 由 `scripts/start.sh` 与 `bin/release-apk.sh` 自动导出，一般不用手设 |

- **客户端地址是打包期内联的**：expo 把 `EXPO_PUBLIC_*` 写进 bundle，所以改地址后
  必须重启 web dev server / 重新打 APK —— 只改配置文件不重启**没有任何效果**
  （反过来也要小心：改了 `app/src/config.ts` 会被 Metro 热更新**立刻**推到线上网页）。
- `scripts/lib.sh` 的 `public_base()` 是这套解析的唯一实现；`bin/release-apk.sh`
  **取不到地址直接失败**，并在打完包后复核地址**真的**进了 APK。
- **改配置必须重启后端**（`cfg.py` 在 import 时读），`scripts/restart.sh backend`。

### 版本与应用内更新

`app/app.json`(version/versionCode) 与 `server/config/config.json`(app.version_name/version_code)
**双写**，`bin/release-apk.sh <ver>` 自动处理。客户端 `GET /api/app/version` 检查更新，
下载 `<public_base>/media/app-release/gamehub-latest.apk`。

### 图标体系

全部手写矢量 SVG（禁 emoji/颜文字）：源稿 `app/assets/svg/`，`bin/render_icons.py`
渲染 PNG（1024 应用图标 / adaptive 前景+monochrome / favicon），运行时组件在
`app/src/icons.tsx`。草稿评审页归档于 `docs/design/图标草稿.html`。

### 运维脚本（scripts/，改进程管理前必读）

本机同时跑着**好几个项目**的服务（novel_reader 50526/50525、quant_view 50026/50025、
team_tools 50031/50035/50040…），手写 `ps aux | grep uvicorn | xargs kill` 会把别人的
进程一起杀掉。`scripts/{start,stop,restart,status}.sh` 用**三重指纹**定位本项目进程，
三项全中才允许 kill：

| 指纹 | backend | web |
|---|---|---|
| ① 端口 | `50541`（从 `server/config/config.json` 的 `port` 读） | `50540` |
| ② 工作目录 | `<repo>/server` | `<repo>/app` |
| ③ 命令行 | `uvicorn.*app\.main:app.*--port[= ]50541` | `expo start.*--port[= ]50540` |

- **对不上就只告警、绝不动手**：pid 文件指向别的进程会被忽略；端口被别的项目占着时
  `start.sh` 直接报错退出，不去抢。
- kill 的是**整棵进程树**：先 TERM，`STOP_GRACE`（默认 10s）后仍有存活才 KILL，
  停完校验端口已释放。expo 是 `npm exec → sh -c → node` 三层，只杀 node 会留孤儿。
- **兜底发现**：pid 文件丢了、或端口还没起来时，靠 `pgrep -f` + 同样的指纹校验捞回来。
- 公共逻辑全在 `scripts/lib.sh`（被 source，不单独执行）；`restart.sh` 只做编排。
- 可覆盖的环境变量：`WEB_PORT` / `BACKEND_PORT` / `UVICORN_BIN` / `STOP_GRACE` /
  `BACKEND_TIMEOUT` / `WEB_TIMEOUT`。
- 日志 `tmp/log/{backend,web}.log`（超 8MB 自动轮转 `.1`），pid 在 `tmp/run/*.pid`。
- `start.sh` 会等 HTTP 探活通过才返回（backend 40s / web 120s 上限），失败时自动
  tail 日志再退出。

## 常用命令

**启停一律走 `scripts/` 下的运维脚本，不要手写 `ps aux | grep ... | kill`**。

```bash
scripts/status.sh                    # 进程、端口、HTTP 探活、日志位置
scripts/start.sh                     # 拉起没在跑的服务（已在跑的跳过）
scripts/start.sh web --clear         # expo 加 --clear（Metro 漏更时）
scripts/start.sh web --dev           # 默认是生产模式 bundle（--no-dev --minify，玩家连的就是它）；调试才加 --dev
scripts/restart.sh backend           # 改 server/ 代码或 config.json 后必须执行
scripts/stop.sh                      # 停全部
tail -f tmp/log/backend.log          # 服务日志；pid 在 tmp/run/*.pid

# 类型检查（在 app/ 目录下执行，勿在仓库根）
(cd app && npx tsc --noEmit)

# 后端单测（conftest 已忽略脚本式 test_ws_game.py）
(cd server && .venv/bin/python -m pytest test/ -q)

# 发布 APK（版本号双写 + 构建 + 部署 + 验证一条龙）
bash bin/release-apk.sh <版本号>
scripts/restart.sh backend           # 发布后必须重启后端（版本号在 import 时读进内存）
curl -s http://127.0.0.1:50541/api/app/version
```

## 测试

**改完按需跑，发布前全跑。** 完整脚本清单、每一步断言与已知的时序陷阱见
[`docs/测试矩阵.md`](docs/测试矩阵.md)；改某款游戏前先看该游戏文档里的「回归」一节。

- 后端单测 / 脚本式测试 / 平衡模拟台都在 `server/test/`。
- 浏览器 E2E 与视觉审计在 `test/`；E2E 前置是后端 50541 + web 50540 都在跑。
- 新增/修改对局屏后，`test/visual_audit.py` 的 `audit_back_wiring` 会逐个对账
  `PROTECTED_SCREENS` 与 `onConfirmLeave`。
- `server` pytest 连跑两遍会撞用户名（409 假失败），两次之间隔 100 秒。
- **E2E 前置**：先用后端虚拟环境安装 `server/requirements-dev.txt`，再执行
  `python -m playwright install chromium`；脚本使用 Playwright 管理的浏览器，
  项目路径从脚本位置推导。

## 项目级规范与已知坑

- **禁用浏览器专属 API**：`window.confirm/alert/prompt` 在原生端崩溃（1.1.x 事故）；
  `window.addEventListener` 同理——原生 RN 里 `window === global` 且**没有**
  addEventListener，对局屏裸调它挂载即 TypeError 白屏+WS 断连（1.3.1 APP 五子棋白屏事故，
  Web 端却完全正常，极易漏测）。一律用应用内 Modal/toast；Web 专属逻辑必须收口到
  backGuard（`onConfirmLeave` 等 IS_WEB 守卫的助手）；`visual_audit.py` 静态扫描兜底。
- **RNW 特性**：disabled 的 TouchableOpacity 是 `pointer-events:none`（Playwright 点击会挂起，
  改断言 computedStyle）；颜色编译进 class 名（测试取值用 `getComputedStyle`）。
- **react-navigation linking**：开启后浏览器 history 与路由双向同步；程序化导航先改路由
  状态再调 history.go，popstate 时 `getRouteName()` 已是新路由——backGuard 靠包装 history
  方法区分程序化/用户返回。linking 的 replaceState 会冲掉自定义 pushState 条目；
  `getCurrentRoute()` 返回叶子路由名（Tab 页是 Games/Rooms/Social/Profile，不是 MainTabs）。
- **新增对局屏必须同时补三处深链**：`App.tsx` 的 `LINKING.config.screens` path、
  `app/src/deepLink.ts` 的 `ROUTE_BY_PATH`、以及 `navigation.tsx` 的 Stack.Screen。
  漏了不会报错，只会表现为「刷新页面回主页 / 分享链接打不开」。
- **返回误触退出提示（已修，勿回退）**：程序化导航的 popstate 里路由已是主 Tab，被误判成
  用户在主 Tab 按返回。修法是 backGuard 包装 `history.go/back/forward` + `suppressPop`。
- **僵尸 WS 连接（已修，勿回退）**：对局结束返回房间后点开始游戏没反应/提示房间不存在。
  修法三件套：①RoomScreen 聚焦时 `socket.reconnect()`；②服务端 receive 循环自愈
  （槽位为空时重新注册当前连接，绝不抢占更新的连接）；③`sendOrQueue` 大厅消息未连接时
  排队、onopen 补发，对局内动作不排队防过期。
- **`socket.ts` 的 `try` 只许包 `JSON.parse`，不许包 `dispatch`（勿改回去）**：
  静默 try 会把「崩溃」伪装成「功能没做」（地图全黑、控制台一行日志都没有）。
- **服务端处理器里给某个连接发消息一律 `await _send_ws(ws, msg)`，不许裸写 `ws.send_json`**：
  接收循环把 `_handle_message` 包在 `try` 里，异常一冒上来就走掉线流程。给**对手**发时
  对手的连接可能刚断、还没注销，裸 `send_json` 一抛就把**正在操作的这个人**踢下线
  （还起 120 秒判负计时）。`_send_ws` 走发送链、失败即丢；处理器自己的异常也只作废
  那一条消息并 `logging.exception`，连接保留。回归在 `test_ws_resilience.py`。
- **`RoomSocket` 的重连不设次数上限**（退避封顶 30 秒）；60 秒没收到任何消息就判半死
  并重连（服务端每个 ping 都回 pong）；回到前台（AppState）与网页 `online` 立刻叫醒；
  4001/4003/4004 是服务端的拒绝，不再重连。**暂停中的连接不查不叫醒**——它可能正被
  同一用户的新连接顶替着，收不到 pong 是正常的。网页键盘统一用 backGuard 的
  `useWebKeys`（俄罗斯方块、2048 在用）。
- **token 每次都查库**（`security._verify`）：用户还在、没被封、`pv`（密码盐的指纹）
  与库里一致才放行 —— 改密码当场作废所有旧会话，封号当场断开 WS（`ws.kick_user`，
  关闭码 4001）。**没有 `pv` 的老 token 放行到自然过期**（7 天），别改成拒绝，否则
  一上线全员被踢回登录页。登录失败按 (用户名, IP) 限流（10 次 / 10 分钟，内存里，
  重启清空）。昵称 ≤16、房名 ≤20、房间密码 ≤32、新密码 6-32 都在**服务端**校验
  （客户端的 `maxLength` 只是体验）。客户端只把「带着 token 的请求」的 401 当成
  会话失效 —— 登录接口密码错也回 401。回归 `test_auth_security.py`。
- **任何点击都必须有反馈**：不能靠 `disabled`（RNW 下等于 `pointer-events:none`，点了连
  提示都没有）、不能只置灰；服务端拒绝要回一句中文错误，别让玩家看到「点了没反应」。
- **必须被看见的状态用 `position:absolute` 盖屏**：不要挂在固定高度内容的底部，
  对局屏没有滚动容器，小屏上会被整条裁到视口外（俄罗斯方块出局提示踩过）。
- **破坏性按钮一律二次确认**：认输/退出都要弹确认框；新弹窗必须接 `useModalBackGuard`。
- **原生端 `locationX` 相对被触摸的子 View**（Web 端 RNW 相对 responder 节点）：
  棋盘类手势层若子元素多，局部坐标会全部落到 (0,0)（五子棋曾「点哪都下在左上角」）。
  修法：手势层容器加 `pointerEvents="box-only"`。
- **终局路径必须调 `_set_game_over`**：漏掉则房间停在 `playing`，玩家点「返回房间」后
  房间页 sync 到 `status=playing` 立刻把人弹回对局页。
- **`_build_sync` 只在 `status == "playing"` 时下发 `game`**：否则房间回等待态后仍会把
  玩家弹回对局屏——与上一条是同一类事故的两个入口。
- **房间页自动跳对局页还要看「这个人是否仍在局中」**：允许「本人已出局、其他人继续」的
  游戏（扫雷 / 飞行棋 / 2048）里，`leave_room` 会把该玩家留在 `players` 名单中，房间仍是
  `playing`。`room_manager.user_in_game()` 判定 `in_game`，房间页只在 `msg.in_game !== false`
  时跳转。
- **`save_game_record` 的守卫不能看房间状态**：终局后房间已是 `waiting`，改看对局态本身
  （`gs["winner"]` / `gs["finished"]`），否则战绩静默停止记录。
- **被新连接顶替的旧连接关闭 ≠ 掉线**：`ws.py` 的 finally 里 `handle_player_disconnect`
  要有 `current is websocket` 守卫，否则对局结束返回房间会概率性显示「断线」并误启判负计时。
- **WS 的 `useEffect` 别依赖「我的座位号」**：`your_seat` 开局前是 `null`、收到 `game_start`
  后变成座位号，放进依赖数组会让 effect 重跑、旧 socket 关闭被标成掉线，而房间数据在游戏中
  不再刷新，于是自己那一格一直显示「掉线」。座位号另存 ref 给回调读。
- **人机回合要说「思考中」**：服务端留了 0.8-1.2s 思考延迟，只写「等待对方…」用户会以为
  卡了。对手 `is_bot` 时文案改成 `XX 思考中…`。
- **游戏名散在七处、没有单一来源，改名必须一起改**：`GamesTab`、`navigation.tsx`、
  `RoomScreen`、`RoomsTab`、`HistoryScreen.GAME_LABELS`、`docs/使用指南.md`、
  `docs/接口协议.md`。改名前先 `grep -rn "<旧名>"`。命名约定：**游戏名就是游戏本身**
  （`UNO 卡牌`，不是「UNO 牌类」；`2048 竞速`，不是「2048 对战」）。
- **主页一句话说明写「这游戏是什么」，不写「这版改了什么」**。徽标只放**人数 + 玩法**，
  不写「可人机」。
- **颜色只有 `app/src/theme.ts` 一个来源**：`playerColors` / `ludoColors` /
  `battleshipShipColors` / `g2048Tile()` / `withAlpha()` / `xiangqiBoard` 系列。
  屏幕里**不要写死 hex**——曾出现「引擎叫 black/white、界面画蓝红、文案写黑白」三方错位。
- **`_opponent_color` 必须带 `game`**：象棋用红/黑，`black` 在象棋里对手是红、在五子棋/
  翻转棋里对手是白；认输/中途离开/断线判负三条路径都靠它定赢家，漏传会把赢家判反。
- **改配色必须重算对比度并更新注释**（WCAG 1.4.3/1.4.11：正文 4.5:1、大字与图形 3:1）。
  `theme.ts` 每个颜色的注释里都写着实测值。**深色字要配浅底——把底色调暗是对比度更低。**
- **颜色不能单独承载信息**（WCAG 1.4.1）：可动棋子是「深墨环 + 放大一档」、最后落子标记是
  「黄点 + 深墨描边」、命中/未命中是形状。新增「用颜色表示状态」的地方都要配第二通道。
- **脉冲类动效一律限次，禁止 `Animated.loop` 无限循环**（WCAG 2.2.2：自动播放且超 5 秒的
  运动要能停）。五子棋回合脉冲 3 轮、胜利脉冲 2 轮后停在常亮态。
- **UI 文案禁止用 emoji**；SVG 图标内的 `<Text>` 属矢量美术，不受此限。
- **单文件 ≤500 行为宜**，复杂对局屏例外。继续加功能时按**模块级函数与子组件**拆：
  棋盘美术层、牌面美术层、面板层、通用小部件、数值/纯函数模块各归各文件，
  主组件只挂状态与回调（`StasisScreen` 2709 → 1369 是最彻底的例子）。
- **测试断言的可靠性**：数棋子/元素不要靠「方形按钮里有没有子元素」这类启发式，
  用 `testID`（`stone-x-y` / `bs-<mode>-x-y` 等）。
- **对局屏触控目标**：主要操作按钮统一 `paddingVertical: spacing.sm + 6`（≈47px，
  HIG 建议主控件 ≥44pt）；2048 方向键固定 48×48。棋盘格子做不到 44pt 的，
  靠第二条输入路径补偿，具体取舍见各游戏设计文档。
- **Metro 偶发漏更**：改了文件页面没变化 → `--clear` 重启 web dev server。
- **shell cwd 陷阱**：`cd app` 后跑 `python3 test/...` 会找不到文件，命令里始终写
  绝对路径或显式 cd。
- **测试勿依赖随机性**：单测涉及随机雷盘/随机布局时，用引擎保证的确定量或从状态里
  筛确定目标；勿 `list(set)` 取下标或 reveal 随机格。单测也别「随手挑一格」造场景
  （那格可能正好是雷 / 触发连锁翻开）。
- **跨连接的 WS 广播要用共享事件循环**：`TestClient(app)` 不写成 `with TestClient(app) as c:`
  时每个 websocket 连接各起一个 blocking portal，跨循环唤醒会让 `receive_json()` 永久阻塞。
  `test/conftest.py` 的 `ws_client` fixture 显式给共享 portal，所有 WS 用例的 `client`
  fixture 都走它。
- **WS 用例会永久阻塞，靠 `pytest.ini` 兜底**：`faulthandler_timeout = 90` 会在 90 秒后
  打印卡住那行的完整栈。改「终局 → 续开」「ready/开局校验」这类逻辑时先看这个栈。

> 改交互前先读 [`docs/移动端体验设计方法论.md`](docs/移动端体验设计方法论.md)
> （响应时间 0.1/1/10s 三段阈值、动效时长按元素尺寸定、触控目标六家标准对照、
> 确认弹窗的摩擦阶梯、WCAG 颜色条款、渐进式披露），逐游戏的落地取值见
> [`docs/游戏设计/README.md`](docs/游戏设计/README.md) 的跨游戏通用规范一节。

## 当前状态与路线图

**当前版本 v1.18.1（versionCode 33）**。完整的逐版本发布记录、待办与优先级在
`docs/更新计划.md`；每款游戏的规则取舍、美术与踩坑在
`docs/游戏设计/<游戏中文名>/{设计.md,增强调研.md}`。**本文件不记录每个版本的流水账**
——只保留「改动前必须知道的跨游戏约定」与路线图。

- **14 款游戏**：棋类（五子棋 / 中国象棋 / 军棋 / 翻转棋 / 重力四子棋 / 点格棋 /
  海战棋）、桌游（UNO / 飞行棋）、各玩各的（2048 / 俄罗斯方块 / 扫雷）、合作（深空休眠）、
  单人（蜘蛛纸牌）。
- **平台能力**：账号与 JWT、房间制 + 人机、战绩落库与终局快照回放、应用内更新、
  热重启恢复、房间级设置注册表、Web 深链。
- **近期版本**（细节见更新计划）：v1.16 统一房间设置 + 座位互换 + 主页分类；
  v1.17 各游戏待优化清账 + UNO 组队 + 游戏名单单一来源；
  v1.18 P0 体验修复（房间列表三态 / 弹窗 / 登录 / 深链）+ 扫雷竞速计分；
  v1.18.1 P1 链路与玩法增强（主页 IA / 房间卡 / 战绩 / 回放 / Web 键盘 /
  俄罗斯方块 hold / 2048 撤销与最佳分 / 海战棋房规 / 扫雷问号与每日盘 /
  深空休眠三档难度）。
- **路线图**：
  - **下一个已定方向：大富翁**（用户指定 UNO 之后做；军棋是中途插进来的，已做完）。
    与本项目现有资产重合度最高——飞行棋已经把**服务端权威骰子 + 逐格路径动画 +
    4 座棋盘美术**全铺好了（`ludo.py` / `ludoBoard.ts` / `LudoBoardView.tsx`），
    大富翁基本上是这套骨架换一套地产系统与棋盘。届时单独出计划。
  - 未排期候选：海战棋格子仍低于 44pt、五子棋放大态下的长按放大镜、扫雷长按与
    五子棋长按手势的语义统一、象棋「可动子」高亮。
  - 需要新基础设施才能做的（不要贸然开工）：狼人杀 / 谁是卧底 / 你画我猜要
    **房间内聊天或画布**（项目现在一条都没有）；炸弹人 / 贪吃蛇大作战要**服务端 tick**
    （深空休眠已铺好骨架，可复用；俄罗斯方块的「动作序列重放」是另一条路）。
