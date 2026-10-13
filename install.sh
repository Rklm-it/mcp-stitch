#!/usr/bin/env bash
# =============================================================================
# Установка mcp-stitch на хаб nexus-mcp:
#   curl -fsSL https://raw.githubusercontent.com/Rklm-it/mcp-stitch/main/install.sh | sudo bash
#
# Ставит команду mcp-stitch и сразу спрашивает ключ Stitch (ввод скрыт). В конце
# печатает ссылку коннектора для claude.ai. Повторный запуск обновляет команду
# и проверяет маршрут, ключ не трогает (сменить — mcp-stitch set-key).
#
# Весь скрипт в { … } и кончается отметкой: обрыв скачивания посреди файла
# не выполнит его наполовину.
# =============================================================================
{
set -uo pipefail

SRC="${MCP_STITCH_SRC:-https://raw.githubusercontent.com/Rklm-it/mcp-stitch/main}"
BIN="${MCP_STITCH_BIN:-/usr/local/bin/mcp-stitch}"
FINISHED=0

say() { echo "› $*"; }
die() { echo "✗ $*" >&2; exit 1; }
on_exit() {
    local rc=$?
    [ "$FINISHED" = 1 ] || echo "✗ установка mcp-stitch прервана (код $rc) — запустите ещё раз" >&2
}
trap on_exit EXIT
trap 'exit 130' INT TERM

[ "$(id -u)" = "0" ] || [ -n "${MCP_STITCH_ALLOW_NONROOT:-}" ] || die "нужен root: … | sudo bash"
command -v curl >/dev/null || die "нет curl"

say "скачиваю mcp-stitch"
TMP="$(mktemp)"
timeout 60 curl -fsSL --connect-timeout 15 -o "$TMP" "$SRC/bin/mcp-stitch" \
    || { rm -f "$TMP"; die "не скачалось с GitHub ($SRC)"; }
bash -n "$TMP" || { rm -f "$TMP"; die "скачанное не проходит bash -n — не ставлю"; }
install -m 0755 "$TMP" "$BIN" && rm -f "$TMP"
echo "✓ установлено: $BIN"

# Маршрут уже есть (обновление команды, хаб переехал с бэкапом) — ключ не
# спрашиваем, только проверяем. Нет — set-key: проверит хаб, ключ (живым
# запросом в Stitch) и Caddy.
if "$BIN" url >/dev/null 2>&1; then
    say "Stitch уже подключён — проверяю"
    "$BIN" check
    RC=$?
    FINISHED=1
    [ "$RC" = 0 ] || echo "Не отвечает. Ключ отозван — mcp-stitch set-key" >&2
    "$BIN" status
    exit "$RC"
fi
"$BIN" set-key
RC=$?
FINISHED=1
if [ "$RC" != 0 ]; then
    echo "Команда стоит; ключ не принят. Повторить: mcp-stitch set-key" >&2
    exit "$RC"
fi
echo "Дальше: mcp-stitch (состояние) · mcp-stitch check · mcp-stitch help"
exit 0
}
