#!/usr/bin/env bash
# stop.sh —— 停止 GameHub 服务（只停本项目，绝不碰其他项目的进程）。
#
# 用法：
#   scripts/stop.sh              # 停全部
#   scripts/stop.sh backend      # 只停后端（50541）
#   scripts/stop.sh web          # 只停前端 web dev server（50540）
#
# 定位方式见 lib.sh 顶部注释（端口 + 工作目录 + 命令行三重指纹）。
# 先 TERM 整棵进程树，STOP_GRACE（默认 10s）后仍有存活才 KILL；
# 停完校验端口已释放。未在运行的服务按「已停止」处理（幂等）。

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
    sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

parse_targets "$@"
[[ "${1:-}" == "-h" || "${1:-}" == "--help" ]] && { usage; exit 0; }

stop_service() {
    local svc="$1" port pids p
    port="$(service_port "$svc")"
    pids="$(service_pids "$svc")"

    if [[ -z "$pids" ]]; then
        rm -f "$(pidfile "$svc")"
        ok "$svc 未在运行"
        # 端口还占着但不是我们的进程 —— 只提醒，绝不动手
        if port_busy "$port"; then
            p="$(port_pid "$port")"
            warn "端口 $port 仍被 pid ${p:-未知} 占用（指纹不符，不是本项目进程），未做任何处理"
        fi
        return 0
    fi

    while read -r p; do
        [[ -n "$p" ]] || continue
        info "停止 $svc 进程树：pid $p  $(cmdline_of "$p" | cut -c1-70)"
        kill_tree "$p" "$STOP_GRACE"
    done <<<"$pids"

    if ! wait_port_free "$port" 10; then
        p="$(port_pid "$port")"
        die "$svc 端口 $port 未释放（占用者 pid ${p:-未知}），请手动检查"
    fi
    rm -f "$(pidfile "$svc")"
    ok "$svc 已停止（端口 $port 已释放）"
}

for svc in "${TARGETS[@]}"; do
    case "$svc" in
        backend) stop_service backend ;;
        web)     stop_service web ;;
    esac
done
