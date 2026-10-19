#!/usr/bin/env bash
# GameHub 运维公共库 —— 由 scripts/{start,stop,restart,status}.sh source，不单独执行。
#
# 为什么需要它：本机同时跑着好几个项目的 uvicorn / vite / expo
# （novel_reader 50526、quant_view 50026、team_tools 50035/50040/50031 等），
# `ps aux | grep uvicorn | xargs kill` 这类写法会把别人的进程一起杀掉。
#
# 本库一律用**三重指纹**定位本项目的进程，三项全中才允许 kill：
#   ① 端口     —— 后端端口从 server/config/config.json 读，只认监听该端口的进程
#   ② 工作目录 —— 必须是 <repo>/server 或 <repo>/app（读 /proc/<pid>/cwd）
#   ③ 命令行   —— 必须匹配 "uvicorn ... app.main:app ... --port 50541"
#                 或 "expo start ... --port 50540"
# 任何一项对不上就只告警、绝不动手——宁可报错，也不误杀。

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$REPO_ROOT/server"
APP_DIR="$REPO_ROOT/app"
RUN_DIR="$REPO_ROOT/tmp/run"   # pid 文件（tmp/ 已 gitignore）
LOG_DIR="$REPO_ROOT/tmp/log"   # 服务日志

UVICORN_BIN="${UVICORN_BIN:-/home/dory/miniconda3/envs/quant/bin/uvicorn}"

WEB_PORT="${WEB_PORT:-50540}"
BACKEND_PORT="${BACKEND_PORT:-$(grep -oP '"port"\s*:\s*\K[0-9]+' \
    "$SERVER_DIR/config/config.json" 2>/dev/null | head -n1)}"
BACKEND_PORT="${BACKEND_PORT:-50541}"

# 公网基址（客户端与下载链接用）。取值优先级：
#   ① 环境变量 PUBLIC_BASE（完整 URL） ② 环境变量 IP（只给主机，端口取 BACKEND_PORT）
#   ③ server/config/config.json 的 public_base（该文件已 gitignore，是本地真值的落点）
# 仓库里**不存任何具体地址**，所以换机器/换域名只改配置或环境变量。
public_base() {
    local base
    if [[ -n "${PUBLIC_BASE:-}" ]]; then
        base="$PUBLIC_BASE"
    elif [[ -n "${IP:-}" ]]; then
        base="http://${IP}:${BACKEND_PORT}"
    else
        base="$(grep -oP '"public_base"\s*:\s*"\K[^"]+' \
            "$SERVER_DIR/config/config.json" 2>/dev/null | head -n1)"
    fi
    printf '%s' "${base%/}"
}

# 导出 EXPO_PUBLIC_* —— expo 在**打包时**把这两个变量内联进 bundle，
# 所以起 web dev server / 打 APK 之前必须设好，之后再改进程环境是没用的。
# 拿不到地址时返回 1（调用方据此告警或直接失败，绝不让 bundle 静默落回 localhost）。
export_client_env() {
    local base
    base="$(public_base)"
    [[ -n "$base" ]] || return 1
    export EXPO_PUBLIC_API_BASE="${EXPO_PUBLIC_API_BASE:-$base}"
    export EXPO_PUBLIC_WS_BASE="${EXPO_PUBLIC_WS_BASE:-${EXPO_PUBLIC_API_BASE/http/ws}}"
}

STOP_GRACE="${STOP_GRACE:-10}"        # TERM 后等待秒数，超时才 KILL
BACKEND_TIMEOUT="${BACKEND_TIMEOUT:-40}"
WEB_TIMEOUT="${WEB_TIMEOUT:-120}"

# ---------------------------------------------------------------- 输出

if [[ -t 1 ]]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_END=$'\033[0m'
else
    C_OK=; C_WARN=; C_ERR=; C_END=
fi

info() { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '%s!%s %s\n' "$C_WARN" "$C_END" "$*" >&2; }
err()  { printf '%s✗%s %s\n' "$C_ERR" "$C_END" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ---------------------------------------------------------------- 服务元数据

service_dir() {  # 服务的工作目录（指纹 ②）
    case "$1" in
        backend) echo "$SERVER_DIR" ;;
        web)     echo "$APP_DIR" ;;
        *)       return 1 ;;
    esac
}

service_port() {
    case "$1" in
        backend) echo "$BACKEND_PORT" ;;
        web)     echo "$WEB_PORT" ;;
        *)       return 1 ;;
    esac
}

service_pattern() {  # 命令行正则（指纹 ③），带端口号做硬隔离
    case "$1" in
        backend) echo "uvicorn.*app\.main:app.*--port[= ]$BACKEND_PORT" ;;
        web)     echo "expo start.*--port[= ]$WEB_PORT" ;;
        *)       return 1 ;;
    esac
}

service_health_url() {
    case "$1" in
        backend) echo "http://127.0.0.1:$BACKEND_PORT/api/app/version" ;;
        web)     echo "http://127.0.0.1:$WEB_PORT/" ;;
        *)       return 1 ;;
    esac
}

pidfile() { echo "$RUN_DIR/$1.pid"; }

# ---------------------------------------------------------------- 进程原语

pid_running() {  # 存在且不是僵尸
    local st
    st="$(ps -o stat= -p "$1" 2>/dev/null)" || return 1
    [[ -n "$st" && "$st" != Z* ]]
}

pid_cwd() {
    readlink "/proc/$1/cwd" 2>/dev/null | sed 's/ (deleted)$//'
}

cmdline_of() {
    tr '\0' ' ' <"/proc/$1/cmdline" 2>/dev/null
}

cmdline_matches() {  # <pid> <ERE>
    local cmd
    cmd="$(cmdline_of "$1")" || return 1
    [[ "$cmd" =~ $2 ]]
}

port_pid() {  # <port> → 监听者 pid（别人的进程看不到 pid，会返回空）
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -n1
}

port_busy() {
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
}

collect_tree() {  # 自身 + 全部后代（父在前）
    local pid="$1" child
    echo "$pid"
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        collect_tree "$child"
    done
}

kill_tree() {  # <root_pid> [grace] —— 先 TERM 整棵树（深的先），超时才 KILL
    local root="$1" grace="${2:-$STOP_GRACE}"
    local tree=() i p alive t
    mapfile -t tree < <(collect_tree "$root")
    for (( i=${#tree[@]}-1; i>=0; i-- )); do
        kill -TERM "${tree[$i]}" 2>/dev/null || true
    done
    for (( t=0; t<grace*4; t++ )); do
        alive=""
        for p in "${tree[@]}"; do
            pid_running "$p" && alive="$p"
        done
        [[ -z "$alive" ]] && return 0
        sleep 0.25
    done
    warn "TERM ${grace}s 后仍有存活进程（${alive}），改发 KILL"
    for (( i=${#tree[@]}-1; i>=0; i-- )); do
        kill -KILL "${tree[$i]}" 2>/dev/null || true
    done
    sleep 0.5
    return 0
}

# ---------------------------------------------------------------- 服务定位

is_service_pid() {  # <svc> <pid> —— 三重指纹校验，全部通过才算本项目的进程
    local svc="$1" pid="$2" cwd
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    pid_running "$pid" || return 1
    cwd="$(pid_cwd "$pid")"
    [[ "$cwd" == "$(service_dir "$svc")" ]] || return 1
    cmdline_matches "$pid" "$(service_pattern "$svc")"
}

service_pids() {  # <svc> → 换行分隔的已校验 pid 列表（可能为空）
    local svc="$1" pf p out=()
    pf="$(pidfile "$svc")"

    if [[ -f "$pf" ]]; then
        p="$(cat "$pf" 2>/dev/null)"
        if is_service_pid "$svc" "$p"; then
            out+=("$p")
        elif [[ -n "$p" ]] && pid_running "$p"; then
            warn "pid 文件记录的 $p 不是本项目 $svc（工作目录/命令行不符），已忽略"
        fi
    fi

    p="$(port_pid "$(service_port "$svc")")"
    if [[ -n "$p" ]] && is_service_pid "$svc" "$p"; then
        out+=("$p")
    fi

    # 兜底：pid 文件丢了、端口还没起来（如 expo 仍在打包）时靠命令行捞
    while read -r p; do
        [[ -n "$p" ]] || continue
        is_service_pid "$svc" "$p" && out+=("$p")
    done < <(pgrep -f "$(service_pattern "$svc")" 2>/dev/null || true)

    (( ${#out[@]} == 0 )) && return 0
    printf '%s\n' "${out[@]}" | sort -un
}

# ---------------------------------------------------------------- 等待

wait_port_free() {  # <port> <timeout>
    local port="$1" timeout="$2" i
    for (( i=0; i<timeout*4; i++ )); do
        port_busy "$port" || return 0
        sleep 0.25
    done
    return 1
}

wait_ready() {  # <svc> <timeout> —— 端口起来且能回 HTTP（任何状态码都算活着）
    local svc="$1" timeout="$2" port url code i
    port="$(service_port "$svc")"
    url="$(service_health_url "$svc")"
    printf '  等待 %s 就绪（最多 %ss）' "$svc" "$timeout"
    for (( i=0; i<timeout*2; i++ )); do
        if port_busy "$port"; then
            code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)"
            if [[ -n "$code" && "$code" != "000" ]]; then
                printf '\n'
                return 0
            fi
        fi
        printf '.'
        sleep 0.5
    done
    printf '\n'
    return 1
}

# ---------------------------------------------------------------- 杂项

rotate_log() {  # 单文件超 8MB 就轮转一份 .1，避免长期运行的 dev server 日志无限涨
    local f="$1"
    [[ -f "$f" ]] || return 0
    local size
    size="$(stat -c%s "$f" 2>/dev/null || echo 0)"
    (( size > 8 * 1024 * 1024 )) && mv -f "$f" "$f.1"
    return 0
}

ensure_dirs() {
    mkdir -p "$RUN_DIR" "$LOG_DIR"
}

tail_log() {  # <svc> <lines>
    local f="$LOG_DIR/$1.log"
    [[ -f "$f" ]] && { info "—— 最近 $2 行 $f ——"; tail -n "$2" "$f" >&2; }
    return 0
}

parse_targets() {  # 把参数里的服务名解析成 TARGETS 数组（默认全部）
    TARGETS=()
    local a
    for a in "$@"; do
        case "$a" in
            backend|server) TARGETS+=(backend) ;;
            web|expo)       TARGETS+=(web) ;;
            all)            TARGETS=(backend web) ;;
            --clear|-c)     ;;
            -h|--help)      ;;
            *)              die "未知参数：$a（可用：backend / web / all）" ;;
        esac
    done
    (( ${#TARGETS[@]} == 0 )) && TARGETS=(backend web)
    return 0
}
