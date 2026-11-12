"""WebUI: favorites management page."""
import json

from e2proxy.logs import log
from e2proxy.favorites import get_favorites, save_favorites
from e2proxy.webui.base import html_page

def build_favorites_ui(channels):
    all_json = json.dumps([{"ref": c["ref"], "name": c["name"], "bouquet": c.get("bouquet","")} for c in channels], ensure_ascii=False)
    # Auto-Repair: Favoriten-Namen aus Channel-Cache korrigieren
    # Refs normalisieren: Favoriten nutzen _ (Underscores), Channels nutzen : (Doppelpunkte)
    def _norm_ref(r):
        return r.replace(":", "_").rstrip("_").rstrip("/")
    all_by_ref = {_norm_ref(c["ref"]): c["name"] for c in channels}
    raw_favs = get_favorites()
    repaired = 0
    for fav in raw_favs:
        ref_norm = _norm_ref(fav.get("ref", ""))
        stored_name = fav.get("name", "")
        correct_name = all_by_ref.get(ref_norm)
        # Repariere wenn: Name fehlt, zu kurz, ODER nicht mit Channel-Cache übereinstimmt
        if correct_name and stored_name != correct_name and (len(stored_name) <= 3 or stored_name != correct_name):
            if correct_name != stored_name:
                fav["name"] = correct_name
                repaired += 1
    if repaired:
        save_favorites(raw_favs)
        log.info(f"Favorites names repaired: {repaired} entries corrected")
    favs_json = json.dumps(raw_favs, ensure_ascii=False)

    css = """
.main{display:flex;height:calc(100vh - 48px);overflow:hidden;}
.panel{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border);overflow:hidden;}
.panel-hdr{padding:10px 14px;background:var(--surface);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;}
.panel-title{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;color:var(--accent);letter-spacing:1px;text-transform:uppercase;}
.panel-count{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);}
.search-input{flex:1;background:var(--surface2);border:1px solid var(--border);color:var(--text);padding:5px 8px;border-radius:4px;font-size:12px;outline:none;}
.search-input:focus{border-color:var(--accent);}
.ch-list{flex:1;overflow-y:auto;padding:3px 6px;}
.ch-list::-webkit-scrollbar{width:3px;}
.ch-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.ch-item{display:flex;align-items:center;justify-content:space-between;padding:6px 6px;border-radius:4px;transition:background 0.1s;}
.ch-item:hover{background:var(--surface2);}
.ch-item.is-fav{opacity:0.35;}
.ch-item.hidden{display:none;}
.ch-name{font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.ch-bouquet{font-size:9px;color:var(--muted);font-family:'JetBrains Mono',monospace;margin-left:6px;flex-shrink:0;}
.btn-add{background:none;border:1px solid var(--border);color:var(--green);width:22px;height:22px;border-radius:3px;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-left:6px;transition:all 0.1s;}
.btn-add:hover{background:var(--green);color:white;border-color:var(--green);}
.btn-add:disabled{opacity:0.25;cursor:default;}
.fav-panel{width:340px;flex-shrink:0;display:flex;flex-direction:column;overflow:hidden;}
.fav-list{flex:1;overflow-y:auto;padding:3px 6px;}
.fav-list::-webkit-scrollbar{width:3px;}
.fav-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.fav-item{display:flex;align-items:center;gap:6px;padding:6px 6px;border-radius:4px;background:var(--surface2);margin-bottom:2px;cursor:grab;border:1px solid transparent;transition:all 0.1s;user-select:none;}
.fav-item:hover{border-color:var(--border);}
.fav-item.dragging{opacity:0.45;border-color:var(--accent);}
.fav-item.drag-over{border-color:var(--accent);background:var(--surface);}
.drag-handle{color:var(--muted);font-size:13px;cursor:grab;flex-shrink:0;}
.fav-name{font-size:12px;flex:1 1 auto;min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.fav-num{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);width:18px;flex-shrink:0;text-align:right;}
.btn-remove{background:none;border:1px solid transparent;color:var(--red);width:20px;height:20px;border-radius:3px;cursor:pointer;font-size:11px;display:flex;align-items:center;justify-content:center;flex-shrink:0;opacity:0;transition:all 0.1s;}
.fav-group-sel{background:var(--surface);border:1px solid var(--border);color:var(--muted);padding:2px 4px;border-radius:3px;font-size:9px;font-family:'JetBrains Mono',monospace;cursor:pointer;outline:none;flex-shrink:0;width:80px;}
.fav-item:hover .btn-remove{opacity:1;}
.btn-remove:hover{background:var(--red);color:white;border-color:var(--red);}
.empty-fav{padding:30px 16px;text-align:center;color:var(--muted);font-size:12px;font-family:'JetBrains Mono',monospace;}
@media(max-width:700px){.main{flex-direction:column;height:auto;}.fav-panel{width:100%;border-top:1px solid var(--border);height:50vh;}.panel{height:50vh;}}
"""

    body = f"""
<div class="header">
  <a class="logo" href="/" style="text-decoration:none">e2<span>proxy</span></a>
  <div class="header-right">
    <a class="btn" href="/" data-i18n="nav.mainpage">← Main page</a>
    <a class="btn" href="/playlist.m3u?profile=Web-SD&list=favorites" data-i18n="fav.export_m3u">↓ M3U Favorites</a>
    <button class="btn btn-primary" onclick="saveFavorites()">💾 <span data-i18n="common.save">Save</span></button>
  </div>
</div>
<div class="main">
  <div class="panel">
    <div class="panel-hdr">
      <span class="panel-title" data-i18n="fav.all_channels">ALL CHANNELS</span>
      <span class="panel-count" id="all-count"></span>
      <input class="search-input" id="search" type="text" data-i18n-ph="common.search" placeholder="Search…" oninput="filterCh(this.value)" autocomplete="off">
    </div>
    <div class="ch-list" id="all-channels"></div>
  </div>
  <div class="fav-panel">
    <div class="panel-hdr">
      <span class="panel-title">⭐ <span data-i18n="fav.title">Favorites</span></span>
      <span class="panel-count" id="fav-count">0</span>
    </div>
    <div class="fav-list" id="fav-list">
      <div class="empty-fav" id="fav-empty">Noch keine Favoriten.<br>+ drücken zum Hinzufügen.</div>
    </div>
  </div>
</div>
<script>
const ALL = {all_json};
let favs = {favs_json};
let dragSrcIdx = null;

function getFavRefs() {{ return new Set(favs.map(f=>f.ref)); }}

function renderAll(filter) {{
  const favRefs = getFavRefs();
  const cont = document.getElementById('all-channels');
  const q = (filter||'').toLowerCase().trim();
  cont.innerHTML = '';
  let visible = 0;
  ALL.forEach(ch => {{
    if (q && !ch.name.toLowerCase().includes(q) && !ch.bouquet.toLowerCase().includes(q)) return;
    visible++;
    const isFav = favRefs.has(ch.ref);
    const div = document.createElement('div');
    div.className = 'ch-item' + (isFav ? ' is-fav' : '');
    div.dataset.ref = ch.ref;
    div.innerHTML = `<span class="ch-name">${{ch.name}}</span><span class="ch-bouquet">${{ch.bouquet.replace(/_/g,' ')}}</span><button class="btn-add" onclick="addFav('${{ch.ref}}','${{ch.name.replace(/'/g,"\\\\'")}}')" ${{isFav?'disabled title="Bereits Favorit"':'title="Hinzufügen"'}}>+</button>`;
    cont.appendChild(div);
  }});
  document.getElementById('all-count').textContent = visible + ' Sender';
}}

const GROUPS = ['', 'Hauptprogramme', '\u00d6ffentlich-Rechtlich', 'Nachrichten', 'Sport', 'Entertainment', 'Doku', 'Musik', 'Kinder', '\u00d6sterreich', 'International'];
const CATEGORIES = [
  {{v:'',       l:'— Kategorie —'}},
  {{v:'series',       l:'📺 Serie'}},
  {{v:'movie',        l:'🎬 Film'}},
  {{v:'news',         l:'📰 Nachrichten'}},
  {{v:'sports',       l:'⚽ Sport'}},
  {{v:'kids',         l:'🧒 Kinder'}},
  {{v:'talk',         l:'🎤 Talkshow'}},
  {{v:'reality',      l:'👥 Reality'}},
  {{v:'documentary',  l:'🎥 Dokumentation'}},
  {{v:'music',        l:'🎵 Musik'}},
];

function renderFavs() {{
  const list = document.getElementById('fav-list');
  Array.from(list.children).forEach(el => {{ if (el.id !== 'fav-empty') el.remove(); }});
  const empty = document.getElementById('fav-empty');
  if (favs.length === 0) {{ empty.style.display = 'block'; document.getElementById('fav-count').textContent = '0'; return; }}
  empty.style.display = 'none';
  favs.forEach((fav, idx) => {{
    const div = document.createElement('div');
    div.className = 'fav-item';
    div.draggable = true;
    const grpOpts = GROUPS.map(g => `<option value="${{g}}" ${{(fav.group||'')==g?'selected':''}}>` + (g || '\u2014 Gruppe \u2014') + `</option>`).join('');
    const catOpts = CATEGORIES.map(c => `<option value="${{c.v}}" ${{(fav.category||'')==c.v?'selected':''}}>${{c.l}}</option>`).join('');
    div.innerHTML = `<span class="drag-handle">\u2837</span><span class="fav-num">${{idx+1}}</span><span class="fav-name">${{fav.name}}</span><select class="fav-group-sel" onchange="setGroup(${{idx}},this.value)" title="Gruppe">${{grpOpts}}</select><select class="fav-group-sel" onchange="setCategory(${{idx}},this.value)" title="EPG-Kategorie" style="max-width:120px">${{catOpts}}</select><button class="btn-remove" onclick="removeFav(${{idx}})">\u2715</button>`;
    div.addEventListener('dragstart', e => {{ dragSrcIdx=idx; div.classList.add('dragging'); e.dataTransfer.effectAllowed='move'; }});
    div.addEventListener('dragend', () => div.classList.remove('dragging'));
    div.addEventListener('dragover', e => {{ e.preventDefault(); div.classList.add('drag-over'); }});
    div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
    div.addEventListener('drop', e => {{
      e.preventDefault(); div.classList.remove('drag-over');
      if (dragSrcIdx !== null && dragSrcIdx !== idx) {{
        const moved = favs.splice(dragSrcIdx, 1)[0];
        favs.splice(idx, 0, moved);
        dragSrcIdx = null;
        renderFavs(); renderAll(document.getElementById('search').value);
      }}
    }});
    list.appendChild(div);
  }});
  document.getElementById('fav-count').textContent = favs.length;
}}

function setGroup(idx, group) {{
  favs[idx].group = group;
}}
function setCategory(idx, cat) {{
  favs[idx].category = cat;
}}

function addFav(ref, name) {{
  if (getFavRefs().has(ref)) return;
  favs.push({{ref, name}});
  renderFavs(); renderAll(document.getElementById('search').value);
}}
function removeFav(idx) {{
  favs.splice(idx, 1);
  renderFavs(); renderAll(document.getElementById('search').value);
}}
function filterCh(q) {{ renderAll(q); }}
function saveFavorites() {{
  apiPost('/api/favorites', favs).then(d => {{
    showToast(d.ok ? '✓ Gespeichert!' : 'Fehler: '+(d.message||'?'), d.ok ? 'success' : 'error');
  }});
}}
renderAll(); renderFavs();
</script>
"""
    return html_page("Favoriten", body, css)


