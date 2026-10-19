# GameHub Server

13 个游戏的联网对战后端：FastAPI + SQLite + WebSocket（房间制，断线重连，热重启）。

## 快速启动

```bash
cd server/
pip install -r requirements.txt

# 首次部署：配置文件不进版本库，先从模板拷一份
cp config/config.example.json config/config.json   # 填好 public_base 与 admin

uvicorn app.main:app --host 0.0.0.0 --port 50541
```

仓库根目录的 `scripts/{start,stop,restart,status}.sh` 是正规的启停入口（会做端口/进程
指纹校验、日志轮转与探活），本地开发直接用它更省事。

## 配置

`config/config.json`（**已 gitignore**，装本机真实地址与口令）与环境变量，取值与优先级
见根目录 `README.md` 的「地址与配置」。改完配置**必须重启**——`app/cfg.py` 在 import
时把配置读进内存。

## 目录结构

```
server/
├── config/                  # config.json（不进库）+ config.example.json（模板）
├── app/
│   ├── main.py              # FastAPI 入口：startup 钩子、路由挂载、静态托管
│   ├── cfg.py               # 配置加载 + 环境变量覆盖（import 时读取）
│   ├── database.py          # SQLite：建表 / 用户 / 战绩落库
│   ├── persistence.py       # 房间实时落盘与启动恢复（热重启）
│   ├── security.py          # JWT + 密码哈希
│   ├── schemas.py           # 请求/响应模型
│   ├── room_manager.py      # 房间生命周期（创建/加入/准备/开局/断线保座/结算）
│   ├── room_settings.py     # 房间级玩法设置的**唯一注册表**（客户端只按 kind 渲染）
│   ├── ws.py                # WS 路由与消息分发
│   ├── game/                # 各游戏的纯引擎与人机 AI（无 IO、不碰 asyncio）
│   │   ├── gomoku.py  minesweeper.py  reversi.py  battleship.py  dots.py
│   │   ├── ludo.py  game2048.py  xiangqi.py  connect4.py  tetris.py
│   │   ├── uno.py + uno_rules.py  luzhanqi.py
│   │   ├── stasis.py + stasis_map.py          # 深空休眠（唯一有服务端 tick 的游戏）
│   │   └── *_ai.py                            # 对应的人机 AI
│   └── routers/             # auth / admin / rooms / app_update / history
├── test/                    # pytest 单测（含脚本式平衡台与对局台，见下）
└── data/                    # SQLite 数据与托管的 APK（gitignore）
```

游戏引擎一律放 `app/game/`，房间与传输层的东西留在 `app/` —— 引擎不认识时间、不读
数据库、不发消息，收发与落盘都在 `ws.py` / `room_manager.py`。

## 测试

```bash
cd server/
python -m pytest test/ -q              # 单测全量（引擎 / AI / 战绩 / WS 协议）

python test/balance_stasis.py --games 24   # 深空休眠平衡模拟台（改战斗常量前必跑）
python test/bench_luzhanqi_ai.py           # 军棋人机对局台（新版 vs 随机 / vs 旧版）

python test/test_ws_game.py            # 需真实后端在跑（不进 pytest）
```

`test_ws_game.py` 这类脚本式测试**不进 pytest**（`test/conftest.py` 里 `collect_ignore`），
需要先起后端再单独 `python` 运行。

## 管理员账号

账号取自 `config.json` 的 `admin` 段（见 `config.example.json`）。**没有默认口令**：
用户名或密码为空就不建号；用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 环境变量可以覆盖配置，
其中密码一旦显式给出，启动时会直接重置已存在账号的密码。
