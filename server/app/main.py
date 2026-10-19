"""FastAPI application entry point."""

import os
import sys
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Ensure app package is importable when running from server/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.cfg import config, server_dir
from app.database import init_db, seed_admin, set_password
from app.security import init_security, hash_password
from app.routers import auth, admin, rooms, app_update, history
from app.ws import (
    router as ws_router,
    _register_dc_timeout_callback,
    reschedule_after_restore,
)
from app import persistence
# Ensure bot callbacks are registered at import time (ws.py bottom-level calls)
from app.database import save_game_record
from app import room_manager as rm

app = FastAPI(title="GameHub")

# CORS — allow all origins for web client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    """Initialize database, security, seed admin, and room manager.

    async：确保 create_task 拿到正在运行的事件循环（sync 版会被丢进
    线程池，get_event_loop 拿不到主循环，sweeper/恢复任务可能不执行）。
    """
    db_path = config.get("db_path", "data/gamehub.db")
    # Resolve relative to server/ directory
    if not os.path.isabs(db_path):
        server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(server_dir, db_path)
    init_db(db_path)
    init_security(config.get("jwt_secret", "secret"))
    rm.set_disconnect_timeout(config.get("reconnect_timeout", 120))
    # Seed admin —— 用户名与密码都来自配置（可被 ADMIN_USERNAME / ADMIN_PASSWORD 覆盖）。
    # **没有内置默认口令**：两者缺一就不建号，免得公开仓库里的已知口令被直接沿用。
    admin_cfg = config.get("admin", {})
    admin_user = str(admin_cfg.get("username") or "").strip()
    admin_pass = str(admin_cfg.get("password") or "").strip()
    if admin_user and admin_pass:
        pw_hash, salt = hash_password(admin_pass)
        seed_admin(admin_user, pw_hash, salt)
        # 环境变量显式给了密码 = 意图是「以它为准」，要能覆盖已存在的账号 ——
        # 否则线上那个早先建出来的 admin 永远改不掉（seed 只在不存在时插入）。
        if os.environ.get("ADMIN_PASSWORD", "").strip():
            if set_password(admin_user, pw_hash, salt):
                print(f"[startup] 已按 ADMIN_PASSWORD 重置 {admin_user} 的密码", flush=True)
    else:
        print(
            "[startup] 未配置管理员账号（config.admin 或 ADMIN_USERNAME/ADMIN_PASSWORD），跳过种子",
            flush=True,
        )
    # Register disconnect timeout callback
    _register_dc_timeout_callback()
    # Register game-end persistence callback
    rm.set_game_end_callback(save_game_record)
    # Start idle room sweeper (every 60s)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    loop.create_task(rm._sweeper_loop())
    # 热重启：启用房间落盘 → 从磁盘恢复房间 → 事件循环内重排 bot/清理等待房
    persistence.init()
    restored = rm.restore_rooms()
    if restored:
        print(f"[startup] 热重启恢复 {restored} 个房间", flush=True)
    loop.create_task(reschedule_after_restore())


# Mount routers
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(rooms.router)
app.include_router(app_update.router)
app.include_router(history.router)
app.include_router(ws_router)

# APK 静态托管：server/data/app-release/ -> /media/app-release/
_apk_dir = os.path.join(server_dir(), "data", "app-release")
os.makedirs(_apk_dir, exist_ok=True)
app.mount("/media/app-release", StaticFiles(directory=_apk_dir), name="app-release")
