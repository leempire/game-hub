#!/usr/bin/env bash
# start.sh —— 启动 GameHub 服务（后端 uvicorn / 前端 expo web dev server）。
#
# 用法：
#   scripts/start.sh              # 启动全部（已在运行的服务会跳过，不会重启）
#   scripts/start.sh backend      # 只启动后端（50541）
#   scripts/start.sh web          # 只启动前端 web dev server（50540）
#   scripts/start.sh web --clear  # expo 带 --clear（Metro 缓存异常/漏更时用）
#   scripts/start.sh web --dev    # 开发模式 bundle（React 开发版 + 不压缩，调试用）
#
# web 默认跑**生产模式** bundle（--no-dev --minify）：手机浏览器玩家连的就是这个
# 端口，开发版 React 每次渲染都多做一轮校验，深空休眠在中端机上实测每 8 秒被
# 卡住 2.8 秒（生产版为 0），bundle 也从 5.2MB 降到 2.0MB。
#
# 日志：tmp/log/{backend,web}.log      pid：tmp/run/{backend,web}.pid
# 要重启已在运行的服务用 scripts/restart.sh，本脚本只负责「拉起没跑的」。
# 端口被**别的项目**占着时会直接报错退出，绝不抢杀。

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

CLEAR=0
DEV=0
for a in "$@"; do
    case "$a" in
        --clear|-c) CLEAR=1 ;;
        --dev)      DEV=1 ;;
    esac
done
parse_targets "$@"

ensure_dirs

# ---------------------------------------------------------------- 启动

assert_port_free() {  # <svc> —— 端口被非本项目进程占用时拒绝启动
    local svc="$1" port p
    port="$(service_port "$svc")"
    port_busy "$port" || return 0
    p="$(port_pid "$port")"
    die "端口 $port 已被 pid ${p:-未知} 占用，且指纹不是本项目进程，拒绝启动 $svc（请先自行处理该进程）"
}

launch() {  # <svc> <cwd> <log> <cmd...>
    local svc="$1" dir="$2" log="$3"; shift 3
    rotate_log "$log"
    cd "$dir" || die "进不去 $dir"
    setsid nohup "$@" >>"$log" 2>&1 </dev/null &
    echo $! >"$(pidfile "$svc")"
    cd "$REPO_ROOT" || true
}

start_backend() {
    local running pid real
    running="$(service_pids backend)"
    if [[ -n "$running" ]]; then
        warn "backend 已在运行（pid $(echo "$running" | tr '\n' ' ')），跳过；要重启请用 scripts/restart.sh backend"
        return 0
    fi
    assert_port_free backend
    [[ -x "$UVICORN_BIN" ]] || die "找不到 uvicorn：${UVICORN_BIN:-（server/.venv 与 PATH 都没有）}（可用 UVICORN_BIN=... 覆盖）"

    info "启动 backend：uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT"
    launch backend "$SERVER_DIR" "$LOG_DIR/backend.log" \
        "$UVICORN_BIN" app.main:app --host 0.0.0.0 --port "$BACKEND_PORT"
    pid="$(cat "$(pidfile backend)")"

    if ! wait_ready backend "$BACKEND_TIMEOUT"; then
        tail_log backend 30
        die "backend 启动失败（pid $pid）"
    fi
    # setsid 在个别情况下会 fork，用端口监听者校准 pid 文件
    real="$(port_pid "$BACKEND_PORT")"
    [[ -n "$real" ]] && echo "$real" >"$(pidfile backend)"
    ok "backend 就绪：pid ${real:-$pid}，端口 $BACKEND_PORT，日志 $LOG_DIR/backend.log"
}

start_web() {
    local running pid real args=(npx expo start --web --port "$WEB_PORT")
    running="$(service_pids web)"
    if [[ -n "$running" ]]; then
        warn "web 已在运行（pid $(echo "$running" | tr '\n' ' ')），跳过；要重启请用 scripts/restart.sh web"
        return 0
    fi
    assert_port_free web
    (( CLEAR )) && args+=(--clear)
    (( DEV )) || args+=(--no-dev --minify)

    # expo 打包时内联 EXPO_PUBLIC_*，没设的话 bundle 会指向 localhost
    if export_client_env; then
        info "客户端地址：$EXPO_PUBLIC_API_BASE"
    else
        warn "拿不到公网地址（PUBLIC_BASE / IP / config.json 的 public_base 都为空）——"
        warn "web bundle 将回落到 http://localhost:50541，只有本机能用。"
    fi

    info "启动 web：${args[*]}"
    launch web "$APP_DIR" "$LOG_DIR/web.log" "${args[@]}"
    pid="$(cat "$(pidfile web)")"

    if ! wait_ready web "$WEB_TIMEOUT"; then
        tail_log web 30
        die "web dev server 启动失败（pid $pid）"
    fi
    real="$(port_pid "$WEB_PORT")"
    [[ -n "$real" ]] && echo "$real" >"$(pidfile web)"
    ok "web 就绪：pid ${real:-$pid}，端口 $WEB_PORT，日志 $LOG_DIR/web.log"
}

# ---------------------------------------------------------------- main

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

for svc in "${TARGETS[@]}"; do
    case "$svc" in
        backend) start_backend ;;
        web)     start_web ;;
    esac
done
