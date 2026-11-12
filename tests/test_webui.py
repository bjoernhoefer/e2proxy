"""WebUI integrity: embedded JS must parse on every page (v4.1 regression:
a stray escape killed switchTab() and every Settings tab but one)."""
import re
import shutil
import subprocess

import pytest

from e2proxy.webui.selfcheck import webui_selfcheck


def _pages():
    from e2proxy.webui.page_main import build_web_ui
    from e2proxy.webui.page_help import build_help_ui
    from e2proxy.webui.page_favorites import build_favorites_ui
    from e2proxy.webui.page_epg import build_epg_browser
    from e2proxy.webui.page_settings import build_settings_ui
    return {"main": lambda: build_web_ui([]), "help": build_help_ui,
            "favorites": lambda: build_favorites_ui([]), "epg": build_epg_browser,
            "settings": build_settings_ui}


@pytest.fixture(params=[False, True], ids=["recorder-off", "recorder-on"])
def recorder_mode(request, monkeypatch):
    import e2proxy.recorder as rec
    monkeypatch.setattr(rec, "is_enabled", lambda: request.param)
    import e2proxy.webui.page_epg as pe
    monkeypatch.setattr(pe, "recorder_enabled", lambda: request.param)
    return request.param


def test_builtin_selfcheck_clean(recorder_mode):
    assert webui_selfcheck() == []


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("page", list(_pages()))
def test_inline_scripts_parse_with_node(page, recorder_mode, tmp_path):
    html = _pages()[page]()
    for i, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        f = tmp_path / f"{page}{i}.js"
        f.write_text(js)
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{page} script #{i}: {r.stderr[:500]}"


def test_settings_has_switchtab_and_recorder_tab():
    html = _pages()["settings"]()
    assert "function switchTab(" in html
    assert "switchTab('recorder'" in html and 'id="tab-recorder"' in html


def test_epg_recorder_controls_hidden_when_disabled(recorder_mode):
    html = _pages()["epg"]()
    assert f"const RCD_ENABLED = {'true' if recorder_mode else 'false'};" in html
    assert ('id="rcd-detail" style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border);display:none"'
            in html) is (not recorder_mode)
