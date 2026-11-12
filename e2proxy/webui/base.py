"""WebUI: base stylesheet and the shared page shell."""
from e2proxy.webui.i18n import I18N_JS

CSS_BASE = """
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@300;400;500&display=swap');
:root {
  --bg:#0a0a0f; --surface:#12121a; --surface2:#1a1a26;
  --border:#2a2a3d; --accent:#6366f1; --accent2:#818cf8;
  --text:#e2e2f0; --muted:#6b6b8a;
  --green:#22c55e; --red:#ef4444; --amber:#f59e0b;
}
[data-theme="light"] {
  --bg:#f4f5f7; --surface:#ffffff; --surface2:#f0f1f5;
  --border:#d1d5db; --accent:#4f46e5; --accent2:#4f46e5;
  --text:#1a1a2e; --muted:#6b7280;
  --green:#16a34a; --red:#dc2626; --amber:#d97706;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;font-weight:300;min-height:100vh;}
.header{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:100;}
.logo{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:17px;color:var(--accent2);}
.logo span{color:var(--muted);font-weight:400;}
.header-right{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap;}
.btn{padding:6px 12px;border-radius:6px;font-size:11px;font-family:'JetBrains Mono',monospace;cursor:pointer;border:1px solid var(--border);color:var(--text);background:var(--surface2);text-decoration:none;display:inline-block;transition:all 0.15s;white-space:nowrap;}
.btn:hover{border-color:var(--accent);color:var(--accent2);}
.btn-primary{background:var(--accent);border-color:var(--accent);color:white;}
.btn-primary:hover{background:var(--accent2);border-color:var(--accent2);color:white;}
.btn-danger{color:var(--red);border-color:var(--red);}
.btn-danger:hover{background:var(--red);color:white;}
.select{background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:11px;cursor:pointer;outline:none;}
.select:focus{border-color:var(--accent);}
.toast{position:fixed;bottom:20px;right:20px;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:9px 14px;border-radius:7px;font-size:12px;font-family:'JetBrains Mono',monospace;opacity:0;transform:translateY(8px);transition:all 0.25s;z-index:9999;}
.toast.show{opacity:1;transform:translateY(0);}
.toast.success{border-color:var(--green);color:var(--green);}
.toast.error{border-color:var(--red);color:var(--red);}
.theme-toggle{background:none;border:1px solid var(--border);color:var(--muted);padding:4px 8px;border-radius:5px;cursor:pointer;font-size:13px;transition:all 0.15s;}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent2);}
"""

def html_page(title, body, head_extra=""):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>e2proxy · {title}</title>
<script>(function(){{var t=localStorage.getItem('e2proxy-theme');if(t==='light')document.documentElement.setAttribute('data-theme','light');}})();</script>
<style>{CSS_BASE}{head_extra}</style>
{I18N_JS}
</head>
<body>
<div id="epg-status-bar" style="display:none;position:sticky;top:0;z-index:200;background:#f59e0b;color:#1a1200;padding:5px 20px;font-family:'JetBrains Mono',monospace;font-size:11px;align-items:center;gap:8px;">
  <span style="animation:pulse 1s infinite">⟳</span>
  <span id="epg-status-text" data-i18n="status.epg_updating">EPG updating…</span>
</div>
<style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.5}}}}</style>
{body}
<div class="toast" id="toast"></div>
<script>
function showToast(msg, type) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (type ? ' ' + type : '');
  setTimeout(() => t.className = 'toast', 3000);
}}
function apiPost(url, data) {{
  return fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(data)}}).then(r=>r.json());
}}
function toggleTheme() {{
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  if (isLight) {{
    document.documentElement.removeAttribute('data-theme');
    localStorage.setItem('e2proxy-theme', 'dark');
  }} else {{
    document.documentElement.setAttribute('data-theme', 'light');
    localStorage.setItem('e2proxy-theme', 'light');
  }}
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) btn.textContent = isLight ? '☀' : '🌙';
}}

// ── EPG Status Indicator ─────────────────────────────
(function() {{
  function checkEpg() {{
    fetch('/api/epg/status').then(r=>r.json()).then(d=>{{
      const el = document.getElementById('epg-status-bar');
      if (!el) return;
      if (d.running) {{
        el.style.display = 'flex';
        el.querySelector('#epg-status-text').textContent =
          (typeof t === 'function' ? t('status.epg_updating') : 'EPG updating…') + ' ' + (d.phase||'') + (d.progress ? ' ' + d.progress + '%' : '');
      }} else {{
        el.style.display = 'none';
      }}
    }}).catch(()=>{{}});
  }}
  setInterval(checkEpg, 5000);
  checkEpg();
}})();
</script>
</body>
</html>"""


