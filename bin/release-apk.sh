#!/usr/bin/env bash
# release-apk.sh - Build and release a new GameHub Android APK.
#
# Usage: ./bin/release-apk.sh <versionName>
# Example: ./bin/release-apk.sh 1.1.0
#
# What it does:
#   1. Reads current versionCode from app.json, increments by 1
#   2. Writes versionCode + versionName into both:
#      - app/app.json (android.versionCode + root version)
#      - server/config/config.json (app.version_code + app.version_name)
#   3. Runs expo prebuild --platform android --clean
#   4. Runs gradle assembleRelease
#   5. Copies APK to server/data/app-release/gamehub-latest.apk
#   6. Curls /api/app/version to verify

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/../scripts/lib.sh"   # public_base() / export_client_env()
APP_DIR="$PROJECT_ROOT/app"
SERVER_DIR="$PROJECT_ROOT/server"
APP_JSON="$APP_DIR/app.json"
SERVER_CONFIG="$SERVER_DIR/config/config.json"
APK_DEST="$SERVER_DIR/data/app-release/gamehub-latest.apk"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <versionName>"
    echo "Example: $0 1.1.0"
    exit 1
fi

VERSION_NAME="$1"

# Read current versionCode from app.json
OLD_VC=$(python3 -c "import json; d=json.load(open('$APP_JSON')); print(d['expo']['android']['versionCode'])")
NEW_VC=$((OLD_VC + 1))

echo "=== GameHub APK Release ==="
echo "Version: $VERSION_NAME (code: $NEW_VC, was: $OLD_VC)"

# Update app.json (android.versionCode and root version)
python3 -c "
import json
with open('$APP_JSON') as f:
    d = json.load(f)
d['expo']['android']['versionCode'] = $NEW_VC
d['expo']['version'] = '$VERSION_NAME'
with open('$APP_JSON', 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
"
echo "Updated $APP_JSON"

# Update server config.json (app.version_code and app.version_name)
python3 -c "
import json
with open('$SERVER_CONFIG') as f:
    d = json.load(f)
d.setdefault('app', {})
d['app']['version_code'] = $NEW_VC
d['app']['version_name'] = '$VERSION_NAME'
with open('$SERVER_CONFIG', 'w') as f:
    json.dump(d, f, indent=4)
    f.write('\n')
"
echo "Updated $SERVER_CONFIG"

# 客户端地址：expo 在打包时把 EXPO_PUBLIC_* 内联进 JS bundle，必须在这里设好 ——
# 之后再改进程环境对已经打好的包没有影响。拿不到地址就**直接失败**，绝不产出一个
# 指向 localhost、装到手机上才发现连不上的包。
echo ""
if ! export_client_env; then
    echo "ERROR: 拿不到公网地址（PUBLIC_BASE / IP / server/config/config.json 的 public_base 都是空的）"
    echo "       APK 会指向 localhost，已中止。请先设好地址再发布。"
    exit 1
fi
echo "客户端地址：$EXPO_PUBLIC_API_BASE"

# Prebuild
echo ""
echo "=== Running expo prebuild ==="
cd "$APP_DIR"
npx expo prebuild --platform android --clean

# Build APK
echo ""
echo "=== Building release APK ==="
cd "$APP_DIR/android"
ANDROID_HOME=/opt/android-sdk ./gradlew assembleRelease --no-daemon

# Find the APK
APK_SRC=$(find "$APP_DIR/android/app/build/outputs/apk" -name "*.apk" -type f | head -1)
if [ -z "$APK_SRC" ]; then
    echo "ERROR: No APK found in build outputs"
    exit 1
fi

echo ""
echo "Found APK: $APK_SRC"
APK_SIZE=$(stat -c%s "$APK_SRC")
echo "APK size: $((APK_SIZE / 1024 / 1024)) MB"

# 防呆：确认地址**真的**进了包（release 是 Hermes 字节码，字符串表里仍能捞到）。
# 地址打错的包要等装到手机上才发现，那时已经发出去了。
# ⚠️ 这里必须用 `grep -c` 而不是 `grep -q`：脚本开了 `pipefail`，`grep -q`
# 匹配到第一条就退出，上游 `strings` 收到 SIGPIPE（141）—— 明明地址在包里，
# 整个管道却被判成失败（v1.18 发布时实际踩到）。
if unzip -p "$APK_SRC" assets/index.android.bundle 2>/dev/null | strings | grep -cF "$EXPO_PUBLIC_API_BASE" > /dev/null; then
    echo "✓ APK 内已内联 $EXPO_PUBLIC_API_BASE"
else
    echo "ERROR: APK 的 JS bundle 里找不到 $EXPO_PUBLIC_API_BASE —— 打包环境没带上 EXPO_PUBLIC_API_BASE，已中止"
    exit 1
fi

# Copy APK
mkdir -p "$(dirname "$APK_DEST")"
cp "$APK_SRC" "$APK_DEST"
echo "Copied to $APK_DEST"

# Verify via API (server might not be running, so just try)
echo ""
echo "=== Verifying API ==="
if curl -s -o /dev/null -w "%{http_code}" "http://localhost:50541/api/app/version" | grep -q "200"; then
    echo "API /api/app/version response:"
    curl -s "http://localhost:50541/api/app/version" | python3 -m json.tool
else
    echo "WARNING: Server not running at localhost:50541, skipping API verification"
    echo "To verify manually after starting server:"
    echo "  curl http://localhost:50541/api/app/version"
    echo "  curl -I $(public_base)/media/app-release/gamehub-latest.apk"
fi

echo ""
echo "=== Release complete ==="
echo "Version: $VERSION_NAME (code: $NEW_VC)"
echo "APK: $APK_DEST ($((APK_SIZE / 1024 / 1024)) MB)"
