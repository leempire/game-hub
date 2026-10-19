#!/usr/bin/env bash
# restart.sh —— 重启 GameHub 服务（= stop.sh + start.sh，改完后端代码/config.json 后执行）。
#
# 用法：
#   scripts/restart.sh              # 重启全部
#   scripts/restart.sh backend      # 只重启后端（改 server/ 代码或 config.json 后必须执行，
#                                   #   config 在 import 时加载，不重启不生效）
#   scripts/restart.sh web --clear  # 重启前端并清 Metro 缓存（页面漏更时用）
#
# 本脚本只做编排，停止/启动逻辑全在 stop.sh / start.sh 里，避免两份实现漂移。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
fi

if ! bash "$SCRIPT_DIR/stop.sh" "$@"; then
    echo "✗ 停止阶段失败，已中止重启" >&2
    exit 1
fi

exec bash "$SCRIPT_DIR/start.sh" "$@"
