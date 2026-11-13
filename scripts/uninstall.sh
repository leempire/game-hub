#!/usr/bin/env bash
# uninstall.sh —— 卸载 scripts/install.sh 注册的 GameHub 开机自启服务。
#
# 用法（在仓库根执行，脚本内部自己调 sudo）：
#   bash scripts/uninstall.sh
#
# 停止并 disable 两个单元，删除 /etc/systemd/system/gamehub*.service 与 gamehub.env。
# 停止时单元自己的 ExecStop 会调用 scripts/stop.sh，把实际进程也收干净。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SERVICE_PREFIX="gamehub"
UNIT_DIR="/etc/systemd/system"
UNITS=("${SERVICE_PREFIX}-backend.service" "${SERVICE_PREFIX}-web.service")

if [[ "${EUID}" -eq 0 ]]; then SUDO=(); else SUDO=(sudo); fi

info() { printf '%s\n' "$*"; }

echo "============================================"
echo "卸载 GameHub 开机自启（systemd）"
echo "============================================"

info "[1/3] 停止服务（单元 ExecStop 会调 scripts/stop.sh 收掉实际进程）"
"${SUDO[@]}" systemctl stop "${UNITS[@]}" 2>/dev/null || true

info "[2/3] 取消开机自启并删除单元文件"
"${SUDO[@]}" systemctl disable "${UNITS[@]}" 2>/dev/null || true
"${SUDO[@]}" rm -f "${UNIT_DIR}/${SERVICE_PREFIX}-backend.service" \
                     "${UNIT_DIR}/${SERVICE_PREFIX}-web.service" \
                     "${UNIT_DIR}/${SERVICE_PREFIX}.env"
"${SUDO[@]}" systemctl daemon-reload

info "[3/3] 确认"
if systemctl list-unit-files "${SERVICE_PREFIX}-*.service" 2>/dev/null | grep -q "${SERVICE_PREFIX}"; then
    info "! 仍能查到 ${SERVICE_PREFIX} 单元，请手动检查 ${UNIT_DIR}"
else
    info "✓ 单元已移除"
fi
bash "${PROJECT_ROOT}/scripts/status.sh" || true

echo
echo "卸载完成。之后仍可手工启停：scripts/{start,stop,restart,status}.sh"
