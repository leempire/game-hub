# GameHub 小游戏平台

手机小游戏平台：注册登录与管理员后台、房间制多人联机（断线重连）、人机对战、历史战绩、游戏规则帮助。
游戏（13 个）：**五子棋**、**中国象棋**、**重力四子棋**、**翻转棋**、**海战棋**、**点格棋**、**军棋（明棋/暗棋）**（以上 2 人对战）、**扫雷（1-4 人同盘竞速）**、**飞行棋（2-4 人派对）**、**2048 竞速（1-4 人各自独立盘）**、**俄罗斯方块对战（1-4 人各自独立盘）**、**UNO 卡牌（2-4 人回合制）**、**深空休眠（2-6 人实时合作塔防）**。
人机对战支持五子棋/翻转棋/海战棋/点格棋/飞行棋/中国象棋/重力四子棋/UNO/军棋/深空休眠（扫雷、2048、俄罗斯方块不支持——后两者是「各玩各的」，人机在语义上不成立）。服务端 AI：棋型评分+minimax / 位置权重+α-β / 猎杀奇偶追踪 / 封格链启发式 / 迭代加深 / 概率热力图 / 信念集推理 / 规则型贪心。
客户端一套代码三端运行：**安卓 APP / 手机浏览器 / PC 浏览器**（Expo React Native + react-native-web）。

## 协作开发

- 开发在 **`dev/<你的用户名>`** 分支上进行，测试通过后推上去，**联系维护者合并到 `main`**；
  `main` 只有维护者合并，请勿直接推。
- 合并前的测试要求、提交信息规范、**敏感信息红线**（这是公开仓库）见
  [`docs/协作开发.md`](docs/协作开发.md)。
- 远端 `main` 是**快照式提交**，不含开发历史 —— 克隆下来历史很浅是正常的。

## 地址与配置

仓库里**不存任何具体地址与口令**（这是公开仓库）。所有地址与密钥来自本机配置文件
`server/config/config.json`（**不进版本库**，模板见 `server/config/config.example.json`），
或环境变量覆盖：

| 环境变量 | 作用 |
|---|---|
| `PUBLIC_BASE` | 公网基址，如 `http://1.2.3.4:50541`（APK 下载链接、客户端 bundle 都用它） |
| `IP` | 只给主机/IP，端口取配置里的 `port` |
| `JWT_SECRET` | JWT 签名密钥；不设且配置为空时自动生成并持久化到 `server/config/jwt_secret.key` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 管理员账号；**密码给了就覆盖已存在账号**（改口令的正规入口） |
| `EXPO_PUBLIC_API_BASE` / `EXPO_PUBLIC_WS_BASE` | 客户端地址（`scripts/start.sh` 与 `bin/release-apk.sh` 会自动导出，一般不用手设） |

首次部署：`cp server/config/config.example.json server/config/config.json`，填好
`public_base` 与 `admin`，再 `scripts/start.sh`。

> 客户端地址是**打包期内联**的（expo 把 `EXPO_PUBLIC_*` 写进 bundle），所以改地址后
> 必须重启 web dev server 并重新打 APK —— 只改配置文件、不重启是没用的。

## 目录结构

```
game_hub/
├── CLAUDE.md          # 项目架构速览（维护前必读）
├── server/            # FastAPI 后端（端口 50541，SQLite）
│   ├── app/
│   │   ├── main.py / cfg.py / database.py / security.py   # 入口/配置/落库/鉴权
│   │   ├── room_manager.py + ws.py    # 房间生命周期 + WS 消息分发
│   │   ├── gomoku.py / minesweeper.py / reversi.py / battleship.py / dots.py / ludo.py / game2048.py   # 纯游戏引擎
│   │   ├── gomoku_ai.py / reversi_ai.py / battleship_ai.py / dots_ai.py                                # 人机 AI
│   │   └── routers/                   # auth / admin / rooms / app_update / history
│   ├── config/config.json   # 端口/JWT/管理员种子/版本号（与 app.json 双写）
│   ├── data/          # SQLite 库（users + game_records）+ app-release/（APK 托管）
│   └── test/          # pytest：引擎/AI/战绩单测 + WS 集成测试
├── app/               # Expo 前端（Web 端口 50540）
│   ├── src/           # screens/路由/socket/api/backGuard返回体系/help帮助/icons矢量图标
│   ├── assets/svg/    # 图标矢量源稿（bin/render_icons.py 渲染 PNG）
│   └── android/       # prebuild 生成的原生工程（gitignore）
├── bin/               # release-apk.sh 一键发版；render_icons.py 图标渲染
├── test/              # 浏览器 E2E（Playwright）+ visual_audit.py 视觉审计
└── docs/              # 接口协议.md（REST+WS 契约）/ 使用指南.md / design/图标草稿
```

## 快速启动

```bash
# 后端
cd server && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 50541

# 前端 Web（手机浏览器/PC）
cd app && npm install
npm run web            # expo start --web --port 50540（开发模式；对外服务请用 scripts/start.sh，默认生产模式 bundle）

# 前端原生开发（Expo Go 扫码）
cd app && npm start
```

改地址只改一处：`server/config/config.json` 的 `public_base`（或环境变量 `PUBLIC_BASE` / `IP`）。
前端 `app/src/config.ts` 只是读取构建期注入的 `EXPO_PUBLIC_API_BASE`，**不用手改**；
`scripts/start.sh` 起 web 前会自动从上面那处推导并导出，`bin/release-apk.sh` 打 APK 时同理
（取不到地址会直接报错中止，不会打出指向 localhost 的包）。

## 发版 APK

```bash
bash bin/release-apk.sh <版本号>   # 如 1.3.0，versionCode 自动 +1
```

脚本自动完成：版本号双写（app.json + 后端 config）→ prebuild → gradle 打包 → 拷贝到
`server/data/app-release/gamehub-latest.apk` → curl 校验 `/api/app/version`。
**发版后需重启后端**（配置 import 时加载）。用户在手机浏览器点「下载 APP」，或在 APP 内「检查更新」即可拿到新包。

## 测试

```bash
# 后端单测（引擎/AI/战绩/WS 协议）
cd server && python -m pytest test/ -q
cd server && python test/test_ws_game.py        # 脚本式：真实后端五子棋对局/断线重连（需后端已启动）

# 浏览器 E2E（需前后端都已启动）
python test/e2e_web_game.py           # 双人五子棋 11 步（含断线重连）
python test/e2e_mine_smoke.py         # 扫雷竞技淘汰+排名 12 步
python test/e2e_reversi_smoke.py      # 翻转棋 8 步（翻转/非法着/认输终局）
python test/e2e_battleship_smoke.py   # 海战棋 9 步（布阵→开战→开炮→认输）
python test/e2e_bot_smoke.py          # 人机 8 步（加人机→自动准备→开始→bot 应招）
python test/e2e_back_behavior.py      # 返回行为 7 步（双击退出/关弹窗/对局确认）
python test/e2e_history_smoke.py      # 历史战绩 7 步（认输落库→我的→历史列表）
python test/e2e_dots_smoke.py         # 点格棋 8 步（画边/回合移交/认输终局）
python test/e2e_ludo_smoke.py         # 飞行棋 6 步（掷骰广播/认输弃权/排名结算）
python test/e2e_2048_smoke.py         # 2048 竞速 12 步（同种子/各自走子/认输只结束自己/单人开局）
python test/e2e_luzhanqi_smoke.py     # 军棋 20 步（明棋/暗棋选择→布阵→走子→认输；暗棋下对手只画牌背）
python test/visual_audit.py           # 本地视觉审计 32 项 + 浏览器专属 API 静态扫描
```

## 文档

- [CLAUDE.md](CLAUDE.md) —— 项目架构、关键机制、常用命令、已知坑（维护必读）
- [docs/接口协议.md](docs/接口协议.md) —— REST/WebSocket 全量契约
- [docs/使用指南.md](docs/使用指南.md) —— 玩家与管理员使用手册
