#!/usr/bin/env bash
# status.sh —— 查看 GameHub 服务状态（进程 / 端口 / HTTP 探活 / 日志位置）。
#
# 用法：
#   scripts/status.sh            # 全部
#   scripts/status.sh backend    # 只看后端
#
# 全部服务都在运行 → 退出码 0，否则 1（方便脚本里 `scripts/status.sh || scripts/start.sh`）。

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

parse_targets "$@"
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
fi

rc=0
info "GameHub 服务状态（仓库 $REPO_ROOT）"
for svc in "${TARGETS[@]}"; do
    port="$(service_port "$svc")"
    pids="$(service_pids "$svc")"
    if [[ -z "$pids" ]]; then
        printf '  %-8s %s未运行%s' "$svc" "$C_WARN" "$C_END"
        if port_busy "$port"; then
            printf '   %s端口 %s 被 pid %s 占用（非本项目）%s' \
                "$C_ERR" "$port" "$(port_pid "$port")" "$C_END"
        fi
        printf '\n'
        rc=1
        continue
    fi
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$(service_health_url "$svc")" 2>/dev/null)"
    printf '  %-8s %s运行中%s  pid %-24s 端口 %-6s HTTP %s\n' \
        "$svc" "$C_OK" "$C_END" "$(echo "$pids" | tr '\n' ',')" "$port" "${code:-无响应}"
done

info "  日志：$LOG_DIR/{backend,web}.log    pid：$RUN_DIR/{backend,web}.pid"
exit $rc
