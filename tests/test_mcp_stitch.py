"""Прогон mcp-stitch и install.sh с заглушками curl/caddy/systemctl.

Хаб — временный каталог (Caddyfile + nexus-mcp.env), Stitch — заглушка curl.
"""
import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "mcp-stitch"
STUBS = Path(__file__).resolve().parent / "stubs"
KEY = "AIzaTestKey_0123456789-abcdefXYZ"
UPSTREAM = "https://stitch.test/mcp"


@pytest.fixture
def hub(tmp_path):
    caddy = tmp_path / "caddy"
    (caddy / "sites").mkdir(parents=True)
    (caddy / "Caddyfile").write_text("hub:443 {\n    import sites/*.caddy\n}\n")
    env = tmp_path / "nexus-mcp.env"
    env.write_text("NEXUS_MCP_PUBLIC_HOSTS=72-56-76-42.sslip.io,other.example\nNEXUS_MCP_PUBLIC_PORT=443\n")
    keyfile = tmp_path / "tty"
    keyfile.write_text(KEY + "\n")
    log = tmp_path / "calls.log"
    log.write_text("")
    for s in STUBS.iterdir():
        s.chmod(s.stat().st_mode | stat.S_IXUSR)
    e = {
        "PATH": f"{STUBS}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "MCP_STITCH_HUB_CADDY": str(caddy),
        "MCP_STITCH_HUB_ENV": str(env),
        "MCP_STITCH_UPSTREAM": UPSTREAM,
        "MCP_STITCH_TTY": str(keyfile),
        "MCP_STITCH_ALLOW_NONROOT": "1",
        "STUB_GOOD_KEY": KEY,
        "STUB_HUB_OK": "1",
        "STUB_LOG": str(log),
    }
    return {"env": e, "site": caddy / "sites" / "mcp-stitch.caddy", "log": log, "tty": keyfile, "tmp": tmp_path}


def run(hub, *args, stdin="", **extra):
    env = {**hub["env"], **extra}
    return subprocess.run(["bash", str(CLI), *args], env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)


def secret_of(site: Path) -> str:
    m = re.search(r"^handle /stitch/mcp/([A-Za-z0-9]+) \{", site.read_text(), re.M)
    assert m, site.read_text()
    return m.group(1)


def test_syntax():
    for f in (CLI, ROOT / "install.sh"):
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0, f


def test_set_key_writes_route_and_prints_connector(hub):
    r = run(hub, "set-key")
    assert r.returncode == 0, r.stderr + r.stdout
    site = hub["site"]
    assert stat.S_IMODE(site.stat().st_mode) == 0o600
    text = site.read_text()
    assert f'header_up X-Goog-Api-Key "{KEY}"' in text
    assert "rewrite * /mcp" in text and "header_up -Authorization" in text
    secret = secret_of(site)
    assert len(secret) == 40
    # Первый домен хаба, порт 443 в ссылку не идёт.
    assert f"https://72-56-76-42.sslip.io/stitch/mcp/{secret}" in r.stdout
    assert "инструментов Stitch: 3" in r.stdout


def test_key_never_in_argv(hub):
    assert run(hub, "set-key").returncode == 0
    calls = hub["log"].read_text()
    assert KEY not in calls, "ключ в командной строке curl/caddy — его видно в ps"
    # И ключ реально ушёл в апстрим — заголовком из файла.
    ups = [json.loads(ln) for ln in calls.splitlines() if ln.startswith("[") and UPSTREAM in ln]
    assert ups and all(any(a.startswith("@") for a in c) for c in ups)


def test_rejected_key_leaves_hub_untouched(hub):
    r = run(hub, "set-key", STUB_GOOD_KEY="SomeOtherKey_000000000000")
    assert r.returncode != 0
    assert "HTTP 401" in r.stderr and "не принял ключ" in r.stderr
    assert not hub["site"].exists()


@pytest.mark.parametrize("bad,why", [
    ('AIza"; respond 200 #xxxxxxxx', "«\"#;»"),
    ("AIza{env.HOME}xxxxxxxxxxxxxxxx", "«{}»"),
    ("ключ_русскими_буквами_длинный", "не латиница"),
    ("short", "всего 5 символов"),
    ("", "ничего не введено"),
])
def test_malformed_key_rejected_before_network(hub, bad, why):
    hub["tty"].write_text(bad + "\n")
    r = run(hub, "set-key")
    assert r.returncode != 0
    assert why in r.stderr, r.stderr
    if len(bad) > 5:
        assert bad not in r.stderr + r.stdout, "ключ в выводе"
    assert not hub["site"].exists()
    assert UPSTREAM not in hub["log"].read_text()


@pytest.mark.parametrize("key", [
    "AQ.Ab8RN6Lx_new-format-key.0123456789abcdef",  # новые ключи Google — с точкой
    "AIzaSyD-legacyFormat_0123456789abcdefgh",
])
def test_google_key_formats_accepted(hub, key):
    hub["tty"].write_text(key + "\n")
    r = run(hub, "set-key", STUB_GOOD_KEY=key)
    assert r.returncode == 0, r.stderr
    assert f'X-Goog-Api-Key "{key}"' in hub["site"].read_text()


def test_bracketed_paste_markers_stripped(hub):
    """Терминал с «вставкой в скобках» присылает ESC[200~ключESC[201~."""
    hub["tty"].write_text(f"\x1b[200~ {KEY} \x1b[201~\n")
    r = run(hub, "set-key")
    assert r.returncode == 0, r.stderr
    assert f'X-Goog-Api-Key "{KEY}"' in hub["site"].read_text()


def test_key_from_stdin_without_terminal(hub):
    r = run(hub, "set-key", stdin=KEY + "\n", MCP_STITCH_TTY=str(hub["tmp"] / "no-tty"))
    assert r.returncode == 0, r.stderr
    assert KEY in hub["site"].read_text()


def test_caddy_rejects_route_new_is_removed(hub):
    r = run(hub, "set-key", STUB_CADDY_FAIL="1")
    assert r.returncode != 0 and "вернул как было" in r.stderr
    assert not hub["site"].exists()


def test_caddy_rejects_route_old_is_restored(hub):
    assert run(hub, "set-key").returncode == 0
    before = hub["site"].read_text()
    r = run(hub, "rotate-secret", STUB_CADDY_FAIL="1")
    assert r.returncode != 0
    assert hub["site"].read_text() == before


def test_set_key_again_keeps_connector_url(hub):
    assert run(hub, "set-key").returncode == 0
    s1 = secret_of(hub["site"])
    new_key = "AIzaSecondKey_9876543210-zyxw"
    hub["tty"].write_text(new_key + "\n")
    assert run(hub, "set-key", STUB_GOOD_KEY=new_key).returncode == 0
    assert secret_of(hub["site"]) == s1
    assert new_key in hub["site"].read_text() and KEY not in hub["site"].read_text()


def test_rotate_secret_changes_url_keeps_key(hub):
    assert run(hub, "set-key").returncode == 0
    s1 = secret_of(hub["site"])
    r = run(hub, "rotate-secret")
    assert r.returncode == 0, r.stderr
    s2 = secret_of(hub["site"])
    assert s1 != s2 and s2 in r.stdout
    assert KEY in hub["site"].read_text()


def test_status_masks_key(hub):
    assert "не подключён" in run(hub).stdout
    assert run(hub, "set-key").returncode == 0
    r = run(hub, "status")
    assert KEY not in r.stdout and "AIza…" in r.stdout
    assert run(hub, "url").stdout.strip().endswith(secret_of(hub["site"]))


def test_check_reports_hub_failure(hub):
    assert run(hub, "set-key").returncode == 0
    r = run(hub, "check", STUB_HUB_OK="0")
    assert r.returncode != 0 and "HTTP 502" in r.stderr


def test_off_removes_route(hub):
    assert run(hub, "set-key").returncode == 0
    r = run(hub, "off")
    assert r.returncode == 0
    assert not hub["site"].exists()
    assert "caddy reload" in hub["log"].read_text()


def test_no_hub(hub, tmp_path):
    r = run(hub, "set-key", MCP_STITCH_HUB_CADDY=str(tmp_path / "nope"))
    assert r.returncode != 0 and "хаб nexus-mcp" in r.stderr


def test_installer_installs_and_asks_key(hub):
    target = hub["tmp"] / "bin" / "mcp-stitch"
    target.parent.mkdir()
    env = {**hub["env"], "MCP_STITCH_SRC": f"file://{ROOT}", "MCP_STITCH_BIN": str(target)}
    # Как под curl | bash: stdin — сам скрипт, ключ — с терминала.
    r = subprocess.run(["bash"], input=(ROOT / "install.sh").read_text(), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout
    assert os.access(target, os.X_OK)
    assert KEY in hub["site"].read_text()
    assert "прервана" not in r.stderr


def test_installer_rerun_keeps_key_and_checks(hub):
    """Повтор и переезд: маршрут уже есть — ключ не спрашиваем, только проверка."""
    target = hub["tmp"] / "bin" / "mcp-stitch"
    target.parent.mkdir()
    env = {**hub["env"], "MCP_STITCH_SRC": f"file://{ROOT}", "MCP_STITCH_BIN": str(target)}
    script = (ROOT / "install.sh").read_text()
    assert subprocess.run(["bash"], input=script, env=env, capture_output=True, text=True).returncode == 0
    before = hub["site"].read_text()
    hub["tty"].write_text("")  # ключа на терминале нет — спроси его установщик, был бы отказ
    r = subprocess.run(["bash"], input=script, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "уже подключён" in r.stdout and "инструментов Stitch: 3" in r.stdout
    assert hub["site"].read_text() == before


def test_installer_is_braced_with_finish_marker():
    text = (ROOT / "install.sh").read_text()
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert body[0] == "{" and body[-1] == "}"
    assert "FINISHED=1" in text and "trap on_exit EXIT" in text


def test_truncated_installer_does_nothing(hub):
    """Обрыв скачивания: половина скрипта без закрывающей скобки не выполняется."""
    target = hub["tmp"] / "bin" / "mcp-stitch"
    text = (ROOT / "install.sh").read_text()
    half = text[: text.index('"$BIN" set-key')]
    env = {**hub["env"], "MCP_STITCH_SRC": f"file://{ROOT}", "MCP_STITCH_BIN": str(target)}
    r = subprocess.run(["bash"], input=half, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert not target.exists()
