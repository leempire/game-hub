#!/usr/bin/env bash
# install.sh —— 把 GameHub 注册为 systemd 服务，随机器开机自启。
#
# 用法（在仓库根执行，脚本内部自己调 sudo）：
#   bash scripts/install.sh
#
# 注册两个系统级单元（以仓库所有者的身份运行）：
#   gamehub-backend.service  → scripts/start.sh backend / scripts/stop.sh backend
#   gamehub-web.service      → scripts/start.sh web     / scripts/stop.sh web
#
# 两者都是 Type=oneshot + RemainAfterExit=yes：start.sh 把进程 setsid 拉起后就返回，
# systemd 只负责「开机拉起」和「关机/停止时调 stop.sh 收尾」；进程本身仍归
# scripts/{start,stop,status}.sh 管，与手工运维完全一致（不另写一套启停逻辑）。
#
# 环境变量：systemd 不继承登录 shell 的 PATH，这里自动探测 uvicorn / node 的位置，
# 连同 HOME / LANG 写进 /etc/systemd/system/gamehub.env。
# **PUBLIC_BASE / IP / JWT_SECRET / ADMIN_* 刻意不固化进单元**：它们都以
# server/config/config.json（本机真值，已 gitignore）为准。写进单元后，lib.sh
# 「环境变量 > config」的优先级会静默盖掉 config，之后改 config.json 反而无效。
#
# 卸载：bash scripts/uninstall.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICE_PREFIX="gamehub"
UNIT_DIR="/etc/systemd/system"
ENV_FILE="${UNIT_DIR}/${SERVICE_PREFIX}.env"

# 服务以仓库所有者的身份运行：直接 sudo 时用 SUDO_USER；以 root 直接跑时取仓库属主。
if [[ "${EUID}" -eq 0 && -z "${SUDO_USER:-}" ]]; then
    RUN_USER="$(stat -c %U "${PROJECT_ROOT}")"
else
    RUN_USER="${SUDO_USER:-$(id -un)}"
fi
RUN_HOME="$(getent passwd "${RUN_USER}" | cut -d: -f6)"
RUN_GROUP="$(id -gn "${RUN_USER}")"
[[ -n "${RUN_HOME}" ]] || { echo "✗ 取不到 ${RUN_USER} 的家目录" >&2; exit 1; }

if [[ "${EUID}" -eq 0 ]]; then SUDO=(); else SUDO=(sudo); fi

info() { printf '%s\n' "$*"; }
ok()   { printf '✓ %s\n' "$*"; }
err()  { printf '✗ %s\n' "$*" >&2; }

# ---------------------------------------------------------------- 前置检查

command -v systemctl >/dev/null || { err "没有 systemctl，本机不是 systemd"; exit 1; }
[[ -x "${PROJECT_ROOT}/scripts/start.sh" ]] || { err "找不到 ${PROJECT_ROOT}/scripts/start.sh"; exit 1; }
[[ -x "${PROJECT_ROOT}/scripts/stop.sh" ]]  || { err "找不到 ${PROJECT_ROOT}/scripts/stop.sh"; exit 1; }

# ---------------------------------------------------------------- PATH（systemd 不继承登录 shell）

path_dirs=()
for bin in uvicorn node npx npm; do
    p="$(command -v "${bin}" 2>/dev/null || true)"
    [[ -n "${p}" ]] && path_dirs+=("$(dirname "${p}")")
done
path_dirs+=("${RUN_HOME}/.local/bin" /usr/local/sbin /usr/local/bin /usr/sbin /usr/bin /sbin /bin)

PATH_VALUE=""
for d in "${path_dirs[@]}"; do
    case ":${PATH_VALUE}:" in
        *":${d}:"*) ;;
        *) PATH_VALUE="${PATH_VALUE:+${PATH_VALUE}:}${d}" ;;
    esac
done

# ---------------------------------------------------------------- 探测代码里用到的环境变量（只提示，不固化）

DETECTED_VARS="$(
    {
        grep -rhoE "os\.getenv\([[:space:]]*['\"][A-Z_][A-Z0-9_]*"   --include='*.py' "${PROJECT_ROOT}/server" 2>/dev/null || true
        grep -rhoE "os\.environ\.get\([[:space:]]*['\"][A-Z_][A-Z0-9_]*" --include='*.py' "${PROJECT_ROOT}/server" 2>/dev/null || true
        grep -rhoE "os\.environ\[[[:space:]]*['\"][A-Z_][A-Z0-9_]*"   --include='*.py' "${PROJECT_ROOT}/server" 2>/dev/null || true
    } | grep -oE "[A-Z][A-Z0-9_]*" | sort -u | paste -sd, - || true
)"

echo "============================================"
echo "安装 GameHub 开机自启（systemd）"
echo "============================================"
info "仓库：   ${PROJECT_ROOT}"
info "运行身份：${RUN_USER}（${RUN_GROUP}）"
info "代码中读取的环境变量：${DETECTED_VARS:-（无）}"
info "  以上地址/密钥一律走 server/config/config.json，不写入单元（避免盖掉 config）。"

# ---------------------------------------------------------------- 写环境文件

echo
info "[1/4] 写环境文件 ${ENV_FILE}"
"${SUDO[@]}" tee "${ENV_FILE}" >/dev/null <<EOF
# GameHub systemd 环境（由 scripts/install.sh 生成）
# 只放 systemd 无法继承的运行环境；地址与密钥以 server/config/config.json 为准。
HOME=${RUN_HOME}
USER=${RUN_USER}
LOGNAME=${RUN_USER}
SHELL=/bin/bash
LANG=en_US.UTF-8
PATH=${PATH_VALUE}
EOF
"${SUDO[@]}" chmod 0644 "${ENV_FILE}"
ok "已写入（PATH 含 $(printf '%s' "${PATH_VALUE}" | cut -d: -f1)）"

# ---------------------------------------------------------------- 写两个单元

write_unit() {  # <单元名> <描述> <After 行> <start 参数> <stop 参数>
    local name="$1" desc="$2" after="$3" start_arg="$4" stop_arg="$5"
    "${SUDO[@]}" tee "${UNIT_DIR}/${name}" >/dev/null <<EOF
[Unit]
Description=${desc}
After=${after}
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_ROOT}
EnvironmentFile=${ENV_FILE}
ExecStart=/bin/bash ${PROJECT_ROOT}/scripts/start.sh ${start_arg}
ExecStop=/bin/bash ${PROJECT_ROOT}/scripts/stop.sh ${stop_arg}
# 首次启动要等 HTTP 探活（start.sh 后端 40s / web 120s 上限），留足余量
TimeoutStartSec=300
TimeoutStopSec=120
Restart=no

[Install]
WantedBy=multi-user.target
EOF
    ok "已写入 ${UNIT_DIR}/${name}"
}

echo
info "[2/4] 写 systemd 单元"
write_unit "${SERVICE_PREFIX}-backend.service" \
    "GameHub 后端（uvicorn · 端口 50541）" \
    "network-online.target" backend backend
write_unit "${SERVICE_PREFIX}-web.service" \
    "GameHub Web（expo 生产模式 bundle · 端口 50540）" \
    "network-online.target ${SERVICE_PREFIX}-backend.service" web web

# ---------------------------------------------------------------- 启用

echo
info "[3/4] 重新加载 systemd 并设为开机自启"
"${SUDO[@]}" systemctl daemon-reload
"${SUDO[@]}" systemctl enable "${SERVICE_PREFIX}-backend.service" "${SERVICE_PREFIX}-web.service" >/dev/null
ok "已 enable（multi-user.target）"

echo
info "[4/4] 立即启动（已在运行的服务会被 start.sh 跳过，不会重复拉起）"
if ! "${SUDO[@]}" systemctl start "${SERVICE_PREFIX}-backend.service" "${SERVICE_PREFIX}-web.service"; then
    err "启动失败，排查：systemctl status ${SERVICE_PREFIX}-backend.service ${SERVICE_PREFIX}-web.service"
    err "日志：journalctl -u ${SERVICE_PREFIX}-backend.service -n 80   /  ${PROJECT_ROOT}/tmp/log/*.log"
    exit 1
fi
ok "服务已启动"

# ---------------------------------------------------------------- 收尾

echo
echo "============================================"
echo "安装完成。"
echo "============================================"
echo "  查看状态：systemctl status ${SERVICE_PREFIX}-backend.service ${SERVICE_PREFIX}-web.service"
echo "  开机自启：systemctl is-enabled ${SERVICE_PREFIX}-backend.service ${SERVICE_PREFIX}-web.service"
echo "  重启后端：sudo systemctl restart ${SERVICE_PREFIX}-backend.service"
echo "            （仍然走 scripts/start.sh，手工运维命令不受影响）"
echo "  关   闭：sudo systemctl stop ${SERVICE_PREFIX}-backend.service ${SERVICE_PREFIX}-web.service"
echo "  卸载服务：bash ${SCRIPT_DIR}/uninstall.sh"
echo "  服务日志：${PROJECT_ROOT}/tmp/log/{backend,web}.log   （systemd 侧：journalctl -u ${SERVICE_PREFIX}-*）"
