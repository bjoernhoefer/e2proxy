"""WebUI self-check: static validation of every page's embedded JavaScript."""
import re as _re_selfcheck

from e2proxy.channels import get_channels
from e2proxy.webui.page_main import build_web_ui
from e2proxy.webui.page_help import build_help_ui
from e2proxy.webui.page_favorites import build_favorites_ui
from e2proxy.webui.page_epg import build_epg_browser
from e2proxy.webui.page_settings import build_settings_ui

# ── WebUI self-check ───────────────────────────────────────────────────────────
# Guards against the recurring class of bug where JS embedded in Python f-strings
# gets corrupted (e.g. a literal "\n" inside a '...' string becomes a real newline
# and breaks the whole <script> block, silently disabling every tab/button on the
# page). This renders each UI page and scans its <script> blocks for quoted string
# literals that run across a physical newline — a reliable, dependency-free signal
# for exactly that breakage. Call at startup and via `--selfcheck` before rollout.

_SCRIPT_RE = _re_selfcheck.compile(r"<script\b[^>]*>(.*?)</script>", _re_selfcheck.S | _re_selfcheck.I)

# Attribut-Start in eingebettetem HTML, z.B.  onclick="  oder  style='
_ATTR_RE = _re_selfcheck.compile(r"""([A-Za-z_:][-\w:.]*)\s*=\s*(["'])""")

# Nach einem String-Literal legal folgende Wort-Operatoren ('x' in obj)
_STR_FOLLOW_OK = {"in", "instanceof"}

def _token_after_string(code, i, n):
    """Nächstes signifikantes Zeichen nach einem String-Literal auf DERSELBEN
    Zeile (Zeilenumbruch → ASI, dann ist alles erlaubt). Liefert (char, wort)."""
    while i < n and code[i] in " \t":
        i += 1
    if i >= n or code[i] == "\n":
        return None, ""
    c = code[i]
    if c.isalpha() or c == "_" or c == "$":
        j = i
        while j < n and (code[j].isalnum() or code[j] in "_$"):
            j += 1
        return c, code[i:j]
    return c, ""


def _skip_template(code, i, n, line):
    """Überspringt ein Template-Literal ab code[i] == '`' — inklusive
    ${…}-Ausdrücken, die ihrerseits Strings und verschachtelte Template-
    Literale enthalten dürfen. Liefert (index_nach_backtick, zeile)."""
    i += 1
    while i < n:
        c = code[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "`":
            return i + 1, line
        if c == "$" and i + 1 < n and code[i + 1] == "{":
            i, line = _skip_template_expr(code, i + 2, n, line)
            continue
        i += 1
    return i, line


def _skip_template_expr(code, i, n, line):
    """Überspringt den Ausdruck in ${…} bis zur passenden schließenden Klammer."""
    depth = 1
    prev_sig = ""
    while i < n:
        c = code[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "`":
            i, line = _skip_template(code, i, n, line)
            prev_sig = "`"
            continue
        if c in "'\"":
            q = c
            i += 1
            while i < n and code[i] != "\n":
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == q:
                    i += 1
                    break
                i += 1
            prev_sig = q
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            if j < 0:
                return n, line
            line += code.count("\n", i, j)
            i = j + 2
            continue
        if c == "/" and (prev_sig in "(,=:[!&|?{};~+-*%<>^" or prev_sig == ""):
            # Regex-Literal (z.B. .replace(/'/g, …)) — bis zum schließenden '/'
            i += 1
            in_class = False
            while i < n and code[i] != "\n":
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == "[":
                    in_class = True
                elif code[i] == "]":
                    in_class = False
                elif code[i] == "/" and not in_class:
                    i += 1
                    break
                i += 1
            prev_sig = "/"
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1, line
        if not c.isspace():
            prev_sig = c
        i += 1
    return i, line

def _scan_js_broken_strings(code):
    """Return a list of (line_no, reason, snippet) for '/" string literals that
    are not closed before a raw newline. Handles // and /* */ comments, backtick
    template literals (which may legally span lines) and /regex/ literals.

    Zusätzlich (seit v4.4) werden zwei weitere Fehlerklassen erkannt, die schon
    einmal eine tote UI verursacht haben:
      • unbalancierte (), [], {} — z.B. wenn eine `function x() {`-Zeile beim
        Editieren verloren geht und ein Rumpf frei im Script hängt
      • verschachtelte Quotes in eingebettetem HTML (siehe _html_attr_left_open)
    """
    problems = []
    i, n = 0, len(code)
    line = 1
    quote = None          # currently open ' or " string
    q_start_line = 0
    q_start_idx = 0
    prev_sig = ""         # last significant char in normal code (for regex/division)
    depth = {"(": 0, "[": 0, "{": 0}
    _closer = {")": "(", "]": "[", "}": "{"}
    neg_reported = set()
    while i < n:
        c = code[i]
        if c == "\n":
            if quote is not None:
                problems.append((q_start_line,
                                 f"unterminated {quote!r} string literal spans a newline",
                                 code[max(0, i - 40):i].split("\n")[-1]))
                quote = None
            line += 1
            i += 1
            continue
        if quote is not None:
            if c == "\\":       # escape (incl. legal line-continuation) → skip next char
                i += 2
                continue
            if c == quote:
                nxt, word = _token_after_string(code, i + 1, n)
                if nxt is not None and (
                    (nxt.isalpha() or nxt in "_$") and word not in _STR_FOLLOW_OK
                    or nxt.isdigit() or nxt in "'\"`"
                ):
                    problems.append((line,
                                     f"string literal is followed directly by {(word or nxt)!r} "
                                     f"without an operator (nested/escaped quote bug?)",
                                     code[q_start_idx:i][-60:]))
                quote = None
            i += 1
            continue
        # not inside a ' or " string
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            if j < 0:
                break
            line += code.count("\n", i, j)
            i = j + 2
            continue
        if c == "/" and prev_sig in "(,=:[!&|?{};~+-*%<>^" or (c == "/" and prev_sig == "" ):
            # regex literal — skip to the closing unescaped '/', honouring [char classes]
            i += 1
            in_class = False
            while i < n and code[i] != "\n":
                if code[i] == "\\":
                    i += 2
                    continue
                if code[i] == "[":
                    in_class = True
                elif code[i] == "]":
                    in_class = False
                elif code[i] == "/" and not in_class:
                    i += 1
                    break
                i += 1
            prev_sig = "/"
            continue
        if c == "`":            # template literal — may span lines legally
            i, line = _skip_template(code, i, n, line)
            prev_sig = "`"
            continue
        if c in "'\"":
            quote = c
            q_start_line = line
            q_start_idx = i + 1
            i += 1
            continue
        if c in depth:
            depth[c] += 1
        elif c in _closer:
            o = _closer[c]
            depth[o] -= 1
            if depth[o] < 0 and o not in neg_reported:
                neg_reported.add(o)
                problems.append((line,
                                 f"unbalanced {c!r} — more closing than opening {o!r} "
                                 f"(a declaration line may have been deleted)",
                                 code[max(0, i - 60):i].split("\n")[-1]))
                depth[o] = 0
        if not c.isspace():
            prev_sig = c
        i += 1
    for o, d in depth.items():
        if d > 0:
            problems.append((line, f"unbalanced {o!r} — {d} never closed", ""))
    return problems

# ── Referenz-Prüfung: aufgerufene, aber nirgends definierte Funktionen ──
# Der Syntax-Scan oben findet nur kaputte Zeichen. Ein Aufruf wie escHtml(…)
# in einer Seite, die escHtml gar nicht ausliefert, ist syntaktisch perfekt und
# stirbt erst zur Laufzeit — genau so ist der Auswertung-Tab beim ersten Anlauf
# leer geblieben. Darum hier zusätzlich die Namen abgleichen.

# Browser-/Sprach-Globals, die nie im Seiten-JS deklariert werden.
_JS_GLOBALS = {
    # Sprache
    "if", "for", "while", "switch", "return", "catch", "function", "typeof", "new",
    "await", "delete", "void", "throw", "else", "do", "try", "finally", "case",
    "yield", "in", "of", "instanceof", "class", "extends", "super", "this", "async",
    # Built-ins
    "Object", "Array", "String", "Number", "Boolean", "Math", "JSON", "Date",
    "RegExp", "Error", "TypeError", "Promise", "Map", "Set", "WeakMap", "WeakSet",
    "Symbol", "BigInt", "Proxy", "Reflect", "Intl", "parseInt", "parseFloat",
    "isNaN", "isFinite", "encodeURIComponent", "decodeURIComponent", "encodeURI",
    "decodeURI", "escape", "unescape", "eval", "structuredClone",
    # DOM / BOM
    "window", "document", "console", "alert", "confirm", "prompt", "fetch",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval", "requestAnimationFrame",
    "cancelAnimationFrame", "localStorage", "sessionStorage", "navigator", "location",
    "history", "screen", "getComputedStyle", "matchMedia", "atob", "btoa",
    "FormData", "URL", "URLSearchParams", "Blob", "File", "FileReader", "Image",
    "Audio", "Event", "CustomEvent", "AbortController", "Headers", "Request",
    "Response", "WebSocket", "EventSource", "IntersectionObserver", "MutationObserver",
    "ResizeObserver", "Notification", "queueMicrotask", "reportError",
    # CSS-Funktion in Style-Strings (var(--x)) und häufige Attribut-Namen
    "var", "calc", "url", "rgb", "rgba", "hsl", "translate", "scale", "rotate",
}

_JS_IDENT = _re_selfcheck.compile(r"[A-Za-z_$][\w$]*")
# Aufruf: name(  — aber nicht  obj.name(  und nicht  new Foo(
_JS_CALL_RE = _re_selfcheck.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
_JS_DECL_RES = [
    _re_selfcheck.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)"),       # function foo()
    _re_selfcheck.compile(r"\bclass\s+([A-Za-z_$][\w$]*)"),                # class Foo
    _re_selfcheck.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)"),    # const foo =
    _re_selfcheck.compile(r"\bwindow\.([A-Za-z_$][\w$]*)\s*="),            # window.foo =
    _re_selfcheck.compile(r"\bcatch\s*\(\s*([A-Za-z_$][\w$]*)"),           # catch (e)
]
# Parameterlisten: function f(a, b), (a, b) =>, a =>
_JS_PARAMS_RES = [
    _re_selfcheck.compile(r"\bfunction\s*\*?\s*[A-Za-z_$][\w$]*\s*\(([^)]*)\)"),
    _re_selfcheck.compile(r"\bfunction\s*\(([^)]*)\)"),
    _re_selfcheck.compile(r"\(([^()]*)\)\s*=>"),
    _re_selfcheck.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*=>"),
]


def _js_strip_literals(code):
    """Blendet String-/Template-/Regex-Literale und Kommentare aus, damit die
    Namensanalyse nicht über Text in Anführungszeichen stolpert. Länge und
    Zeilenstruktur bleiben erhalten (Leerzeichen statt Inhalt)."""
    out = list(code)
    i, n = 0, len(code)
    prev_sig = ""

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = code[i]
        if c == "/" and i + 1 < n and code[i + 1] == "/":
            j = code.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and code[i + 1] == "*":
            j = code.find("*/", i + 2)
            if j < 0:
                blank(i, n)
                break
            blank(i, j + 2)
            i = j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == c or code[j] == "\n":
                    break
                j += 1
            blank(i, j + 1)
            i = j + 1
            prev_sig = "'"
            continue
        if c == "`":
            # Template-Literal: ${…} muss sichtbar bleiben (dort steht echter Code)
            j = i + 1
            blank(i, i + 1)
            while j < n:
                if code[j] == "\\":
                    blank(j, j + 2)
                    j += 2
                    continue
                if code[j] == "`":
                    blank(j, j + 1)
                    j += 1
                    break
                if code[j] == "$" and j + 1 < n and code[j + 1] == "{":
                    depth = 1
                    blank(j, j + 2)
                    k = j + 2
                    while k < n and depth:
                        if code[k] == "{":
                            depth += 1
                        elif code[k] == "}":
                            depth -= 1
                            if not depth:
                                blank(k, k + 1)
                                break
                        k += 1
                    j = k + 1
                    continue
                blank(j, j + 1)
                j += 1
            i = j
            prev_sig = "`"
            continue
        if c == "/" and (prev_sig in "(,=:[!&|?{};~+-*%<>^" or prev_sig == ""):
            j = i + 1
            in_class = False
            while j < n and code[j] != "\n":
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "[":
                    in_class = True
                elif code[j] == "]":
                    in_class = False
                elif code[j] == "/" and not in_class:
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
            prev_sig = "/"
            continue
        if not c.isspace():
            prev_sig = c
        i += 1
    return "".join(out)


def _js_declared_names(code):
    """Alle Namen, die in diesem JS deklariert werden (Funktionen, Variablen,
    Parameter, Klassen, window.X)."""
    names = set()
    for rx in _JS_DECL_RES:
        names.update(rx.findall(code))
    for rx in _JS_PARAMS_RES:
        for grp in rx.findall(code):
            for part in grp.split(","):
                # Default-Werte, Rest/Spread und Destructuring auflösen
                part = part.split("=")[0]
                names.update(_JS_IDENT.findall(part))
    # Destructuring-Deklarationen: const {a, b} = x  /  const [a, b] = y
    for m in _re_selfcheck.finditer(r"\b(?:const|let|var)\s*[\{\[]([^\}\]]*)[\}\]]", code):
        for part in m.group(1).split(","):
            part = part.split(":")[-1].split("=")[0]
            names.update(_JS_IDENT.findall(part))
    return names


def _scan_js_undefined_calls(code, extra_defined=frozenset()):
    """Liefert [(name, zeile)] für Funktionen, die aufgerufen, aber weder im
    Seiten-JS noch als Browser-Global definiert sind."""
    stripped = _js_strip_literals(code)
    defined = _js_declared_names(stripped) | set(extra_defined) | _JS_GLOBALS
    seen = {}
    for m in _JS_CALL_RE.finditer(stripped):
        name = m.group(1)
        if name in defined or name in seen:
            continue
        seen[name] = stripped.count("\n", 0, m.start()) + 1
    return sorted(seen.items(), key=lambda kv: kv[1])


_HTML_HANDLER_RE = _re_selfcheck.compile(
    r'\bon(click|change|input|submit|keyup|keydown|keypress|focus|blur|mouseover|'
    r'mouseout|dblclick|contextmenu)\s*=\s*"([^"]*)"', _re_selfcheck.I)


def _scan_html_handler_calls(html, page_js):
    """Liefert [(name, attribut)] für onclick="foo()"-Handler, deren Funktion
    auf dieser Seite nicht existiert. Genau diese Klasse hat schon einmal alle
    Settings-Tabs unklickbar gemacht."""
    defined = _js_declared_names(_js_strip_literals(page_js)) | _JS_GLOBALS
    bad = {}
    for m in _HTML_HANDLER_RE.finditer(html):
        attr, body = "on" + m.group(1).lower(), m.group(2)
        # Handler-Body wie JS behandeln: Literale ausblenden, dann Aufrufe suchen
        body_code = _js_strip_literals(body)
        local = _js_declared_names(body_code)
        for c in _JS_CALL_RE.finditer(body_code):
            fn = c.group(1)
            if fn not in defined and fn not in local and fn not in bad:
                bad[fn] = attr
    return sorted(bad.items())


def webui_selfcheck():
    """Render every UI page and validate its embedded JavaScript. Returns a list
    of human-readable problem strings (empty == all good)."""
    problems = []
    try:
        chans = get_channels()
    except Exception:
        chans = []
    pages = {
        "/ (Live TV)":   lambda: build_web_ui(chans),
        "/help":         build_help_ui,
        "/favorites":    lambda: build_favorites_ui(chans),
        "/epg":          build_epg_browser,
        "/settings":     build_settings_ui,
    }
    for name, builder in pages.items():
        try:
            html = builder()
        except Exception as e:
            problems.append(f"{name}: failed to render ({e})")
            continue
        blocks = [m.group(1) for m in _SCRIPT_RE.finditer(html)]
        for si, code in enumerate(blocks, 1):
            for ln, reason, snippet in _scan_js_broken_strings(code):
                problems.append(
                    f"{name}: <script> #{si} line ~{ln}: {reason} → ...{snippet.strip()!r}"
                )
        # Namensprüfung über ALLE Blöcke der Seite zusammen (ein Block darf
        # eine Funktion aus einem anderen aufrufen).
        alljs = "\n".join(blocks)
        for fn, ln in _scan_js_undefined_calls(alljs):
            problems.append(
                f"{name}: JS line ~{ln}: calls {fn}() which is not defined on this page"
            )
        for fn, attr in _scan_html_handler_calls(html, alljs):
            problems.append(
                f"{name}: inline {attr} handler calls {fn}() which is not defined on this page"
            )
    return problems


