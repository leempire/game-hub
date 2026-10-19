# GameHub Server

五子棋对战平台后端，基于 FastAPI + SQLite + WebSocket。

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务（监听 0.0.0.0:50541）
cd server/
uvicorn app.main:app --host 0.0.0.0 --port 50541 --reload
```

## 测试

```bash
# 五子棋逻辑单测
python test/test_gomoku.py

# WebSocket 集成测试（需先启动服务）
# 先安装测试依赖：
pip install websockets httpx
# 后台启动服务后运行：
python test/test_ws_game.py
```

## 目录结构

```
server/
├── config/config.json     # 配置文件
├── app/
│   ├── main.py            # FastAPI 入口
│   ├── database.py        # SQLite 封装
│   ├── security.py        # JWT + 密码哈希
│   ├── schemas.py         # Pydantic 模型
│   ├── room_manager.py    # 内存房间管理
│   ├── gomoku.py          # 五子棋逻辑
│   ├── ws.py              # WebSocket 处理
│   └── routers/
│       ├── auth.py        # 认证接口
│       ├── admin.py       # 管理员接口
│       └── rooms.py       # 房间 REST
├── test/
│   ├── test_gomoku.py     # 单测
│   └── test_ws_game.py    # 集成测试
└── data/                  # SQLite 数据（gitignore）
```

## 默认管理员

账号取自 `config.json` 的 `admin` 段（见 `config.example.json`）。**没有默认口令**：
用户名或密码为空就不建号；用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 环境变量可以覆盖配置，
其中密码一旦显式给出，启动时会直接重置已存在账号的密码。
