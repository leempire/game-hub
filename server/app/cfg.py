"""全局配置：从 server/config/config.json 加载，并允许环境变量覆盖。

config.json **不进版本库**（见 .gitignore 与 config.example.json）—— 它装的是这台
机器的真实地址与密钥，模板在 config.example.json。环境变量优先级更高，给容器/CI
这类不方便改文件的场景用：

| 环境变量 | 覆盖的配置项 | 说明 |
|---|---|---|
| `PUBLIC_BASE` | `public_base` | 公网基址，如 `http://1.2.3.4:50541`（用于 APK 下载链接） |
| `IP` | `public_base` | 只给主机/IP，端口取配置里的 `port` |
| `JWT_SECRET` | `jwt_secret` | 缺省/占位时自动生成并持久化到 `config/jwt_secret.key` |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin.*` | 管理员种子账号（密码给了就强制覆盖已存在的账号） |

注意：配置在 **import 时**读进内存，改完必须重启后端（`scripts/restart.sh backend`）。
"""

import json
import os
import secrets

_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config",
)
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "config.json")

# 自动生成的密钥文件（0600，已 gitignore）——落在本地而不是写回 config.json，
# 免得把生成的密钥又写进一个可能被误提交的文件里。
_SECRET_PATH = os.path.join(_CONFIG_DIR, "jwt_secret.key")

# 这些值等于「没配」：历史上仓库里发过的占位串 + 空串，见到就换成随机密钥。
# 老部署用的正是那个占位串，而它在公开仓库里躺着 —— 一律不再沿用。
_PLACEHOLDER_SECRETS = {
    "",
    "secret",
    "change-me",
    "gamehub-secret-key-change-in-production",
}


def _persisted_secret() -> str:
    """读取（必要时生成）持久化的 JWT 密钥。

    持久化是为了让**重启后已发出的 token 依然有效**；随机生成一次就够，
    密钥本身不落进版本库。
    """
    if os.path.exists(_SECRET_PATH):
        with open(_SECRET_PATH, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    fd = os.open(_SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    return value


def _apply_env(cfg: dict) -> None:
    """把环境变量覆盖进配置字典（就地改，调用方拿到的仍是普通 dict）。"""
    env_ip = os.environ.get("IP", "").strip()
    env_base = os.environ.get("PUBLIC_BASE", "").strip()
    if not env_base and env_ip:
        env_base = f"http://{env_ip}:{cfg.get('port', 50541)}"
    if env_base:
        cfg["public_base"] = env_base.rstrip("/")

    secret = os.environ.get("JWT_SECRET", "").strip() or str(
        cfg.get("jwt_secret", "")
    ).strip()
    cfg["jwt_secret"] = (
        secret if secret not in _PLACEHOLDER_SECRETS else _persisted_secret()
    )

    admin = cfg.setdefault("admin", {})
    if os.environ.get("ADMIN_USERNAME", "").strip():
        admin["username"] = os.environ["ADMIN_USERNAME"].strip()
    if os.environ.get("ADMIN_PASSWORD", "").strip():
        admin["password"] = os.environ["ADMIN_PASSWORD"].strip()


with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
    config = json.load(f)

_apply_env(config)


def server_dir() -> str:
    """server/ 目录的绝对路径。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
