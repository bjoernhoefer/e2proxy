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


# ── Receiver roles Stream / Record (v5.2) ─────────────────────────────────
_RX_CFG = {"receivers": [
    {"id": "rx1", "name": "Box 1", "ip": "10.0.0.1", "enabled": True, "default": True, "stream": True},
    {"id": "rx2", "name": "Box 2", "ip": "10.0.0.2", "enabled": True, "record": True},
]}


def _settings_with(monkeypatch, cfg):
    import e2proxy.webui.page_settings as ps
    monkeypatch.setattr(ps, "get_config", lambda: cfg)
    return ps.build_settings_ui()


def test_settings_receiver_table_shows_stream_and_record(monkeypatch):
    html = _settings_with(monkeypatch, _RX_CFG)
    assert "<th title=\"Bevorzugter Receiver für Live-Streams\">Stream</th>" in html
    assert "<th title=\"Bevorzugter Receiver für Aufnahmen\">Record</th>" in html
    rows = re.findall(r"<tr>\s*<td><span class=\"tag\">(rx\d)</span></td>(.*?)</tr>", html, re.S)
    roles = {rid: (">stream<" in body, ">record<" in body) for rid, body in rows}
    assert roles == {"rx1": (True, False), "rx2": (False, True)}


# Click test: runs the real modal/CRUD functions of the settings page in node
# against a tiny DOM stub, ticks checkboxes and clicks "Save".
_CLICK_HARNESS = r"""
const els = {};
let modalEl = null;
function mkEl(id) { return {id, value: '', checked: false, style: {}, remove() { if (this === modalEl) { modalEl = null; for (const k in els) delete els[k]; } }}; }
const document = {
  getElementById: id => id === 'crud-modal' ? modalEl : (els[id] || null),
  createElement: () => mkEl(''),
  body: { appendChild(m) {
    modalEl = m;
    for (const t of m.innerHTML.match(/<input[^>]*>/g) || []) {
      const id = (t.match(/id="([^"]+)"/) || [])[1];
      const e = mkEl(id);
      e.type = /type="checkbox"/.test(t) ? 'checkbox' : 'text';
      e.checked = /\schecked[\s>]/.test(t);
      e.value = ((t.match(/value="([^"]*)"/) || [])[1]) || (e.type === 'checkbox' ? 'on' : '');
      els[id] = e;
    }
    m.saveOnclick = (m.innerHTML.match(/<button class="btn btn-primary" onclick="([^"]+)"/) || [])[1];
  }},
};
const window = globalThis;
let SERVER_CFG = JSON.parse(process.argv[2]);
let POSTED = null;
const fetch = async () => ({ json: async () => JSON.parse(JSON.stringify(SERVER_CFG)) });
const apiPost = async (url, body) => { POSTED = {url, body}; return {ok: true}; };
const showToast = () => {};
const setTimeout = () => {};
const location = { reload() {} };
__FUNCS__
async function click(fnCall, ticks) {
  eval(fnCall);
  for (const [k, v] of Object.entries(ticks)) { const e = document.getElementById('f-' + k); if (!e) throw new Error('no field ' + k); e.checked = v; }
  await eval(modalEl.saveOnclick.replace(/^\(async\(\)=>/, 'async function _s()').replace(/\)\(\)$/, '') + '; _s()');
  await new Promise(r => globalThis.setImmediate(r));
}
(async () => {
  const steps = JSON.parse(process.argv[3]);
  const out = [];
  for (const s of steps) {
    POSTED = null;
    await click(s.call, s.ticks);
    out.push(POSTED && POSTED.body.receivers.map(r => [r.id, !!r.stream, !!r.record, !!r.default, r.locked]));
    if (POSTED) SERVER_CFG = POSTED.body;
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


def _extract_js_funcs(html, names):
    js = "\n".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    out = []
    for n in names:
        m = re.search(r"^(async )?function " + n + r"\(.*?^\}", js, re.S | re.M)
        assert m, f"function {n} missing"
        out.append(m.group(0))
    return "\n".join(out)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_click_receiver_roles_saved_exclusively(monkeypatch, tmp_path):
    import json
    html = _settings_with(monkeypatch, _RX_CFG)
    funcs = _extract_js_funcs(html, ["showModal", "getVal", "patchConfig", "addReceiver",
                                     "editReceiver", "applyReceiverRoles"])
    script = tmp_path / "click.js"
    script.write_text(_CLICK_HARNESS.replace("__FUNCS__", funcs))
    rx2 = json.dumps(_RX_CFG["receivers"][1])
    steps = [
        # rx2 becomes stream receiver → rx1 loses the stream role, record stays on rx2
        {"call": f"editReceiver({rx2})", "ticks": {"stream": True}},
        # rx2 drops record; nobody records by preference afterwards
        {"call": f"editReceiver({rx2})", "ticks": {"stream": True, "record": False}},
        # new receiver rx3 claims record and stream
        {"call": "addReceiver()", "ticks": {"stream": True, "record": True}},
    ]
    r = subprocess.run(["node", str(script), json.dumps(_RX_CFG), json.dumps(steps)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-1500:]
    s1, s2, s3 = json.loads(r.stdout)
    # rx1 untouched (no "locked" key); edited/added receivers stay unlocked
    # (v5.2 fix: the checkbox value "on" was saved as locked)
    assert s1 == [["rx1", False, False, True, None], ["rx2", True, True, False, False]]
    assert s2 == [["rx1", False, False, True, None], ["rx2", True, False, False, False]]
    assert [x[1:3] + x[4:] for x in s3] == [[False, False, None], [False, False, False], [True, True, False]]
