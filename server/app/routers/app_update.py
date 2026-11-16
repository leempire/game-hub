"""APP 版本与下载接口：无需登录，供移动网页下载和 APP 内检查更新使用。"""

import os

from fastapi import APIRouter, Response

from app.cfg import config, server_dir

router = APIRouter(prefix="/api/app", tags=["app"])

APK_FILENAME = "gamehub-latest.apk"


def apk_path() -> str:
    return os.path.join(server_dir(), "data", "app-release", APK_FILENAME)


def download_url() -> str:
    base = config.get("public_base", "").rstrip("/")
    # 带上版本号做 cache-busting：文件名固定为 gamehub-latest.apk，浏览器/中间代理
    # 可能缓存旧包；顺带让访问日志能看出客户端下的是哪一版。
    version_code = config.get("app", {}).get("version_code", 0)
    return f"{base}/media/app-release/{APK_FILENAME}?v={version_code}"


@router.get("/version")
def get_version():
    """返回最新版本信息；has_update 表示服务端是否已发布 APK。"""
    app_cfg = config.get("app", {})
    released = os.path.exists(apk_path())
    return {
        "version_code": app_cfg.get("version_code", 0) if released else 0,
        "version_name": app_cfg.get("version_name", "0.0.0") if released else "0.0.0",
        "download_url": download_url() if released else None,
        "has_update": released,
    }


@router.get("/latest")
def get_latest():
    """302 跳转到 APK 实际地址，供浏览器直接下载。"""
    return Response(status_code=302, headers={"Location": download_url()})
