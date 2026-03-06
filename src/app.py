import re
import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "EBH SQL Formatter"

# ------------------------------
# Konstansok / regexek
# ------------------------------

SEP_LINE = "-------------------------------------------------------------------------------"

_RX_SEP = re.compile(r"^-{5,}\s*$", re.MULTILINE)

# HTML entity dekódolás
_RX_HTML = [
    (re.compile(r"&lt;", re.IGNORECASE), "<"),
    (re.compile(r"&gt;", re.IGNORECASE), ">"),
    (re.compile(r"&amp;", re.IGNORECASE), "&"),
]

# NOLOCK normalizálás
_RX_WITH_NOLOCK = re.compile(r"\bWITH\s*\(\s*NOLOCK\s*\)", re.IGNORECASE)
_RX_BARE_NOLOCK = re.compile(r"\(\s*NOLOCK\s*\)", re.IGNORECASE)


# ------------------------------
# B) Pajzs: kommentek + stringek
# ------------------------------

def _shield_noformat_blocks(s: str):
    """
    No-format blokkok pajzsolása.
    Marker:
      -- formatting off
      ...
      -- formatting on
    A kettő közötti tartalmat placeholderre cseréljük, a végén visszaállítjuk.
    """
    lines = s.splitlines(True)  # newline megőrzés
    tokens = {}
    out = []
    buf = []
    in_off = False
    token_id = 0

    rx_off = re.compile(r"^\s*--\s*formatting\s+off\s*$", re.IGNORECASE)
    rx_on = re.compile(r"^\s*--\s*formatting\s+on\s*$", re.IGNORECASE)

    def flush_buf():
        nonlocal token_id
        if not buf:
            return
        token_id += 1
        key = f"__EBH_NOFMT_{token_id:06d}__"
        tokens[key] = "".join(buf)
        out.append(key)  # NINCS extra newline hozzáadva!
        buf.clear()

    for ln in lines:
        stripped = ln.rstrip("\n")
        if rx_off.match(stripped):
            flush_buf()
            in_off = True
            out.append(ln)
            continue
        if rx_on.match(stripped):
            flush_buf()
            in_off = False
            out.append(ln)
            continue

        if in_off:
            buf.append(ln)
        else:
            out.append(ln)

    flush_buf()
    return "".join(out), tokens
def _normalize_where_continuation_and_or(s: str) -> str:
    """
    A WHERE blokkban az elszabadult AND/OR sorokat is az AND-oszlop alá húzza,
    még akkor is, ha közben volt több soros feltétel (pl. IN ( SELECT ... )).

    Csak a WHERE *fő szintjén* (paren depth == 0) igazít.
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_where = re.compile(r"^(?P<ws>\s*)WHERE\s{4}(?P<rest>.*)$", re.IGNORECASE)
    rx_andor = re.compile(r"^(?P<ws>\s*)(?P<kw>AND|OR)\b(?P<rest>.*)$", re.IGNORECASE)

    # WHERE blokk vége (új clause)
    rx_break = re.compile(
        r"^\s*(GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|UNION\b|EXCEPT\b|INTERSECT\b|INSERT\b|UPDATE\b|DELETE\b|MERGE\b|WITH\b|RETURN\b)\b",
        re.IGNORECASE,
    )

    def paren_delta(line: str) -> int:
        # pajzs mellett elég egyszerűen számolni
        return line.count("(") - line.count(")")

    while i < len(lines):
        m = rx_where.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        # WHERE sor megy változatlanul
        out.append(lines[i])

        head_ws = m.group("ws")
        cond_start_prefix = head_ws + (" " * len("WHERE    "))

        i += 1
        depth = 0

        while i < len(lines):
            ln = lines[i]
            if rx_break.match(ln):
                break

            # depth-et frissítjük az előző sor alapján is, de egyszerűbb soronként
            # előbb igazítunk, aztán frissítünk – mindegy, csak konzisztens legyen
            m_andor = rx_andor.match(ln)

            if m_andor and depth == 0:
                kw = m_andor.group("kw").upper()
                rest = m_andor.group("rest").strip()
                out.append(f"{cond_start_prefix}{kw} {rest}")
            else:
                out.append(ln)

            depth += paren_delta(ln)
            if depth < 0:
                depth = 0

            i += 1

        # nem nyeljük el a break sort, outer loop kezeli
        continue

    return "\n".join(out)


def _normalize_in_subquery_blocks(s: str) -> str:
    """
    EBH-stílus:
    AND x IN (
    SELECT ...
    FROM ...
    WHERE ...
    )
    ->
    AND x IN ( SELECT ...
               FROM ...
               WHERE ...
             )

    - Csak azokat a blokkokat kezeli, ahol a sor IN ( -re végződik (nincs utána más),
      és a következő nem üres sor SELECT-kel indul.
    - A záró ')' sort azonos indentre húzza (a subquery blokk alá).
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_in_open = re.compile(r"^(?P<ws>\s*)(?P<prefix>.*\b(NOT\s+IN|IN)\s*\()\s*$", re.IGNORECASE)
    rx_select_line = re.compile(r"^\s*SELECT\s{3}\b", re.IGNORECASE)
    rx_close = re.compile(r"^\s*\)\s*[,;]?\s*$")

    while i < len(lines):
        m = rx_in_open.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        prefix = m.group("prefix").rstrip()  # "... IN ("

        # következő nem üres sor
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines) or not rx_select_line.match(lines[j]):
            out.append(lines[i])
            i += 1
            continue

        # subquery első sora a zárójel után ugyanazon sorba
        first = lines[j].strip()
        out.append(f"{ws}{prefix} {first}")

        # indent: pontosan oda, ahol a SELECT kezdődik (prefix + space után)
        indent_len = len(ws) + len(prefix) + 1
        indent = " " * indent_len

        # a subquery további sorai a záró ')' sorig
        k = j + 1
        while k < len(lines):
            ln = lines[k]

            # üres sor maradhat, de a példád szerint inkább nincs — átengedjük
            if ln.strip() == "":
                out.append(ln)
                k += 1
                continue

            if rx_close.match(ln):
                # záró ) ugyanarra az indentre
                tail = ln.strip()[1:].strip()  # ) utáni ,/; ha van
                if tail and re.fullmatch(r"[,;]{1,2}", tail):
                    out.append(f"{indent}){tail}")
                else:
                    out.append(f"{indent})")
                k += 1
                break

            out.append(indent + ln.strip())
            k += 1

        i = k
    return "\n".join(out)
def _split_top_level_and_or(expr: str):
    """
    Top-level (zárójel-depth 0) AND/OR mentén darabol.
    Pajzs mellett biztonságos (stringek/kommentek már placeholder-ek).
    Visszaad: [("HEAD","cond"), ("AND","cond2"), ("OR","cond3"), ...]
    """
    s = expr.strip()
    parts = []
    buf = []
    depth = 0
    i = 0
    n = len(s)

    def is_word_char(c: str) -> bool:
        return c.isalnum() or c == "_"

    while i < n:
        ch = s[i]
        if ch == "(":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            depth = max(depth - 1, 0)
            buf.append(ch)
            i += 1
            continue

        if depth == 0:
            # AND / OR felismerés szóhatárral
            if (i + 3 <= n) and s[i:i+3].upper() == "AND":
                prev_ok = (i == 0) or (not is_word_char(s[i-1]))
                next_ok = (i + 3 == n) or (not is_word_char(s[i+3]))
                if prev_ok and next_ok:
                    chunk = "".join(buf).strip()
                    if chunk:
                        parts.append(("HEAD" if not parts else "AND", chunk))
                    buf = []
                    i += 3
                    # opcionális whitespace átugrás
                    while i < n and s[i].isspace():
                        i += 1
                    continue

            if (i + 2 <= n) and s[i:i+2].upper() == "OR":
                prev_ok = (i == 0) or (not is_word_char(s[i-1]))
                next_ok = (i + 2 == n) or (not is_word_char(s[i+2]))
                if prev_ok and next_ok:
                    chunk = "".join(buf).strip()
                    if chunk:
                        parts.append(("HEAD" if not parts else "OR", chunk))
                    buf = []
                    i += 2
                    while i < n and s[i].isspace():
                        i += 1
                    continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        parts.append(("HEAD" if not parts else parts[-1][0] if False else "AND", tail))

    # a fenti tail hozzáadásnál a kw nem számít, inkább javítsuk:
    # első elem HEAD, továbbiak eredeti AND/OR-ral jönnek -> egyszerűsítünk:
    # ha csak 1 elem van, az HEAD; ha több, az első HEAD, a többit úgy hagyjuk, ahogy szétvált
    if parts:
        parts[0] = ("HEAD", parts[0][1])
    return parts


def _explode_where_lines_and_indent_in_blocks(s: str) -> str:
    """
    1) WHERE sorokból top-level AND/OR mentén több sort csinál (EBH indenttel).
    2) IN ( / NOT IN ( esetén a belső blokkot egységesen behúzza,
       és a záró ')' a nyitó '(' alá kerül.

    FONTOS: Ez a lépés az operátor-oszlop igazítás előtt fusson!
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_where = re.compile(r"^(?P<ws>\s*)WHERE\s{4}(?P<rest>.*)$", re.IGNORECASE)

    # IN ( blokk kezdete a sor végén
    rx_in_open = re.compile(r"^(?P<lhs>.*?\b(?:NOT\s+IN|IN))\s*\(\s*$", re.IGNORECASE)
    # záró ) sor (önálló)
    rx_close_only = re.compile(r"^\s*\)\s*[,;]?\s*$")

    while i < len(lines):
        m = rx_where.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        rest = m.group("rest").strip()

        # 1) WHERE szétdarabolás
        parts = _split_top_level_and_or(rest)

        # WHERE HEAD sor
        out.append(f"{ws}WHERE    {parts[0][1]}")
        # AND/OR sorok ugyanarra a „feltétel oszlopra”
        cond_ws = ws + (" " * len("WHERE    "))
        for kw, cond in parts[1:]:
            out.append(f"{cond_ws}{kw} {cond}")

        i += 1

        # 2) Ha a következő sorok közvetlenül a WHERE-hez tartoztak inline formában, akkor már nincs AND/OR sor a bemenetben.
        #    Viszont ha a bemenetben voltak már AND sorok, itt nem vettük át őket.
        #    Ezért csak a *közvetlen* WHERE sort kezeljük; a meglévő AND sorokat a későbbi igazítók kezelik.

    # 3) IN blokkok indentálása: második passz a már szétdarabolt szövegen
    lines2 = "\n".join(out).split("\n")
    out2 = []
    j = 0

    while j < len(lines2):
        line = lines2[j]
        # keressünk egy "IN (" sorvéget (akár WHERE/AND/OR sorban)
        m_in = rx_in_open.match(line.strip())
        if not m_in:
            out2.append(line)
            j += 1
            continue

        # A nyitó '(' oszlop (a sorban a '(' helye)
        open_paren_col = line.find("(")
        if open_paren_col < 0:
            out2.append(line)
            j += 1
            continue

        # Belső indent: a nyitó '(' alá + 9 space (EBH-szerű blokk)
        inner_ws = " " * (open_paren_col + 9)
        close_ws = " " * open_paren_col

        out2.append(line.rstrip())  # IN ( sor

        j += 1
        # gyűjtjük a blokkot a hozzá tartozó záró ) sorig (egyszerű, de jó: első önálló )-ig)
        while j < len(lines2):
            if rx_close_only.match(lines2[j]):
                # záró ) igazítva
                tail = lines2[j].strip()[1:].strip()  # ) utáni , ; ha van
                if tail and re.fullmatch(r"[,;]{1,2}", tail):
                    out2.append(f"{close_ws}){tail}")
                else:
                    out2.append(f"{close_ws})" + ((" " + tail) if tail else ""))
                j += 1
                break

            # belső sorok: bal oldali whitespace csere inner_ws-re
            if lines2[j].strip() == "":
                out2.append(lines2[j])
            else:
                out2.append(inner_ws + lines2[j].lstrip())
            j += 1

        continue

    return "\n".join(out2)


def _unshield_noformat_blocks(s: str, tokens: dict) -> str:
    for key, val in tokens.items():
        s = s.replace(key, val)
    return s

def _normalize_parenthesized_where_blocks(s: str) -> str:
    """
    WHERE    ( ... AND ... AND ... ) blokk szétbontása több sorra.
    - Csak akkor aktiválódik, ha a WHERE sor rest-je '('-el kezdődik és
      a külső (depth=1) szinten tartalmaz AND/OR kötőszót.
    - Nem bont bele belső zárójelekbe (pl. IN ( SELECT ... )).
    - A szétszedett feltételekben normalizálja az =, <>, !=, >=, <=, >, < operátorok körüli spacinget 1 space-re.
    """
    lines = s.split("\n")
    out = []
    rx_where = re.compile(r"^(?P<ws>\s*)WHERE\s{4}(?P<rest>.*)$", re.IGNORECASE)

    def normalize_ops(expr: str) -> str:
        # 1 space az alap összehasonlító operátorok körül
        x = expr.strip()
        x = re.sub(r"\s*(>=|<=|<>|!=|=|>|<)\s*", r" \1 ", x)
        x = re.sub(r"\s+", " ", x).strip()
        return x

    def split_top_level_bool(inner: str):
        """
        inner: zárójelen belüli rész (a külső '(' ')' nélkül)
        split AND/OR szerint csak depth=0-nál (inner szinten).
        Vissza: [ (None, cond1), ('AND', cond2), ('OR', cond3), ... ]
        """
        parts = []
        buf = []
        depth = 0

        i = 0
        n = len(inner)

        def starts_kw(pos: int, kw: str) -> bool:
            # szóhatáros illesztés: whitespace + kw + whitespace
            end = pos + len(kw)
            if end > n:
                return False
            if inner[pos:end].lower() != kw.lower():
                return False
            left_ok = (pos == 0) or inner[pos - 1].isspace()
            right_ok = (end == n) or inner[end].isspace()
            return left_ok and right_ok

        current_op = None

        while i < n:
            ch = inner[i]
            if ch == "(":
                depth += 1
                buf.append(ch)
                i += 1
                continue
            if ch == ")":
                depth = max(depth - 1, 0)
                buf.append(ch)
                i += 1
                continue

            if depth == 0:
                # AND / OR felismerés
                if starts_kw(i, "and"):
                    cond = "".join(buf).strip()
                    if cond:
                        parts.append((current_op, cond))
                    buf = []
                    current_op = "AND"
                    i += 3
                    continue
                if starts_kw(i, "or"):
                    cond = "".join(buf).strip()
                    if cond:
                        parts.append((current_op, cond))
                    buf = []
                    current_op = "OR"
                    i += 2
                    continue

            buf.append(ch)
            i += 1

        tail = "".join(buf).strip()
        if tail:
            parts.append((current_op, tail))

        # első elem op-ja legyen None
        if parts:
            parts[0] = (None, parts[0][1])
        return parts

    for ln in lines:
        m = rx_where.match(ln)
        if not m:
            out.append(ln)
            continue

        ws = m.group("ws")
        rest = m.group("rest").lstrip()

        # Csak a "WHERE ( ... )" formát bontjuk
        if not rest.startswith("("):
            out.append(ln)
            continue

        # Keressük a teljes külső zárójelet ugyanazon sorban
        # (ha több soros már, nem nyúlunk itt hozzá)
        if rest.count("(") == 0 or rest.count(")") == 0:
            out.append(ln)
            continue

        # Megpróbáljuk a külső zárójelet levágni, ha a sor végén van ')'
        # pl: "( a and b and c )"
        stripped = rest.strip()
        if not (stripped.startswith("(") and stripped.endswith(")")):
            out.append(ln)
            continue

        inner = stripped[1:-1].strip()

        # Ha nincs top-level AND/OR, nincs mit bontani
        parts = split_top_level_bool(inner)
        if len(parts) <= 1:
            out.append(ln)
            continue

        # Indentek:
        head_prefix = ws + "WHERE    "
        cont_prefix = ws + (" " * len("WHERE    ")) + "  "  # 2 space: a "( " oszlopáig

        # Első sor: WHERE    ( <cond1>
        first_cond = normalize_ops(parts[0][1])
        out.append(f"{head_prefix}( {first_cond}")

        # Következő sorok: <cont_prefix>AND/OR <cond>
        for op, cond in parts[1:]:
            cond_norm = normalize_ops(cond)
            out.append(f"{cont_prefix}{op} {cond_norm}")

        # Záró ) külön sorban, a "( " alá (11 space)
        out.append(f"{cont_prefix})")
        continue

    return "\n".join(out)

def _shield_comments_and_strings(s: str):
    """
    Kiszedi (shieldeli) a kommenteket és string literálokat placeholder-ekre.
    - -- ... (egysoros)
    - /* ... */ (blokk)
    - '...' (SQL string, '' escape)
    Visszaad: (shielded_text, tokens)
    """
    tokens = {}
    out = []
    i = 0
    n = len(s)
    token_id = 0

    def new_token(text: str) -> str:
        nonlocal token_id
        token_id += 1
        key = f"__EBH_SHIELD_{token_id:06d}__"
        tokens[key] = text
        return key

    while i < n:
        ch = s[i]

        # -- comment
        if ch == "-" and i + 1 < n and s[i + 1] == "-":
            j = i + 2
            while j < n and s[j] != "\n":
                j += 1
            out.append(new_token(s[i:j]))
            i = j
            continue

        # /* comment */
        if ch == "/" and i + 1 < n and s[i + 1] == "*":
            j = i + 2
            while j + 1 < n and not (s[j] == "*" and s[j + 1] == "/"):
                j += 1
            j = min(j + 2, n)
            out.append(new_token(s[i:j]))
            i = j
            continue

        # string literal
        if ch == "'":
            j = i + 1
            while j < n:
                if s[j] == "'":
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append(new_token(s[i:j]))
            i = j
            continue

        out.append(ch)
        i += 1

    return "".join(out), tokens


def _unshield(s: str, tokens: dict) -> str:
    for key, val in tokens.items():
        s = s.replace(key, val)
    return s


# ------------------------------
# C) CTE záró ')' igazítás a nyitó '(' alá
# ------------------------------

def _align_cte_closing_paren(s: str) -> str:
    """
    CTE-kben az AS ( ... ) záró ')' igazítása a nyitó '(' oszlopa alá.
    A kommentek/stringek ekkor már shieldelve vannak, így nem zavarják a zárójelek.
    """
    rx_as_open = re.compile(r"\bAS\s*\(", re.IGNORECASE)
    matches = list(rx_as_open.finditer(s))
    if not matches:
        return s

    patches = []  # (line_start, line_end, replacement)

    for m in reversed(matches):
        open_paren_pos = m.end() - 1
        line_start = s.rfind("\n", 0, open_paren_pos) + 1
        open_col = open_paren_pos - line_start

        # zárójel-szint keresés
        depth = 1
        i = open_paren_pos + 1
        n = len(s)
        close_pos = None

        while i < n:
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    close_pos = i
                    break
            i += 1

        if close_pos is None:
            continue

        cls = s.rfind("\n", 0, close_pos) + 1
        cle = s.find("\n", close_pos)
        if cle == -1:
            cle = n

        close_line = s[cls:cle]
        stripped = close_line.strip()

        # csak akkor igazítunk, ha a sorban csak ')' (+ opcionális ','/';')
        if not re.fullmatch(r"\)\s*[,;]?\s*[,;]?\s*", stripped):
            continue

        tail = stripped[1:].strip()  # ')' után: "", ",", ";", ",;" ...
        if tail and re.fullmatch(r"[,;]{1,2}", tail):
            repl = (" " * open_col) + ")" + tail
        else:
            repl = (" " * open_col) + ")" + ((" " + tail) if tail else "")

        patches.append((cls, cle, repl))

    for cls, cle, repl in patches:
        s = s[:cls] + repl + s[cle:]

    return s


# ------------------------------
# Alap normalizálások
# ------------------------------

def _decode_html_entities(s: str) -> str:
    for rx, repl in _RX_HTML:
        s = rx.sub(repl, s)
    return s


def _normalize_separator_nolock_and_prefixes(s: str) -> str:
    s = _RX_SEP.sub(SEP_LINE, s)
    s = _RX_WITH_NOLOCK.sub("( NOLOCK )", s)
    s = _RX_BARE_NOLOCK.sub("( NOLOCK )", s)

    # EBH prefixek
    s = re.sub(r"\bSELECT\s+", "SELECT   ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bFROM\s+", "FROM     ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bWHERE\s+", "WHERE    ", s, flags=re.IGNORECASE)

    # ORDER BY / GROUP BY uppercase (opcionális, de hasznos)
    s = re.sub(r"\border\s+by\b", "ORDER BY", s, flags=re.IGNORECASE)
    s = re.sub(r"\bgroup\s+by\b", "GROUP BY", s, flags=re.IGNORECASE)
    return s


def _split_select_from(s: str) -> str:
    """
    EBH: SELECT és FROM ne legyen egy sorban, ha a sor egyszerű 'SELECT ... FROM ...' alak.
    (Shield miatt string/komment nem zavar.)
    """
    lines = s.split("\n")
    out = []
    rx = re.compile(r"^(?P<ws>\s*)SELECT\s+(?P<sel>.+?)\s+FROM\s+(?P<frm>.+)$", re.IGNORECASE)

    for ln in lines:
        m = rx.match(ln)
        if not m:
            out.append(ln)
            continue
        ws = m.group("ws")
        out.append(f"{ws}SELECT {m.group('sel').rstrip()}")
        out.append(f"{ws}FROM {m.group('frm').rstrip()}")

    return "\n".join(out)


# ------------------------------
# JOIN / ON
# ------------------------------

def _normalize_join_on_indent(s: str) -> str:
    """
    JOIN a FROM forrás-oszlopa alá, ON a JOIN alá +2.
    """
    lines = s.split("\n")
    out = []

    rx_from = re.compile(r"^(?P<ws>\s*)FROM\s{5}\b", re.IGNORECASE)
    rx_clause_end = re.compile(
        r"^\s*(WHERE\s{4}\b|GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|UNION\b|EXCEPT\b|INTERSECT\b|INSERT\b|UPDATE\b|DELETE\b|MERGE\b)\b",
        re.IGNORECASE,
    )
    rx_join = re.compile(
        r"^\s*(INNER\s+JOIN|LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|FULL\s+(?:OUTER\s+)?JOIN|CROSS\s+APPLY|OUTER\s+APPLY|JOIN)\b",
        re.IGNORECASE,
    )
    rx_on = re.compile(r"^\s*ON\b", re.IGNORECASE)

    active = False
    join_ws = None
    on_ws = None

    for ln in lines:
        m_from = rx_from.match(ln)
        if m_from:
            ws = m_from.group("ws")
            join_ws = ws + (" " * 9)
            on_ws = ws + (" " * 11)
            active = True
            out.append(ln)
            continue

        if active and rx_clause_end.match(ln):
            active = False
            join_ws = None
            on_ws = None
            out.append(ln)
            continue

        if active and join_ws and rx_join.match(ln):
            out.append(join_ws + ln.lstrip())
            continue

        if active and on_ws and rx_on.match(ln):
            rest = re.sub(r"^\s*ON\s+", "", ln, flags=re.IGNORECASE)
            out.append(on_ws + "ON " + rest.strip())
            continue

        out.append(ln)

    return "\n".join(out)


def _normalize_on_spacing(s: str) -> str:
    """
    ON (és közvetlen alatta AND/OR) sorokban 1 space az operátorok körül.
    NINCS oszlopos igazítás az ON-ban.
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_on = re.compile(r"^(?P<ws>\s*)ON\s+(?P<rest>.*)$", re.IGNORECASE)
    rx_andor = re.compile(r"^(?P<ws>\s*)(?P<kw>AND|OR)\s+(?P<rest>.*)$", re.IGNORECASE)

    rx_sym = re.compile(r"\s*(>=|<=|<>|!=|=|>|<)\s*")
    rx_notin = re.compile(r"\s+(NOT\s+IN|IN)\s+", re.IGNORECASE)
    rx_like = re.compile(r"\s+(NOT\s+LIKE|LIKE)\s+", re.IGNORECASE)
    rx_isnull = re.compile(r"\s+(IS\s+NOT\s+NULL|IS\s+NULL)\b", re.IGNORECASE)
    rx_between = re.compile(r"\s+BETWEEN\s+", re.IGNORECASE)

    def norm_expr(expr: str) -> str:
        x = re.sub(r"[ \t]+", " ", expr.strip())
        x = rx_notin.sub(lambda m: " " + re.sub(r"\s+", " ", m.group(1).upper()) + " ", x)
        x = rx_like.sub(lambda m: " " + re.sub(r"\s+", " ", m.group(1).upper()) + " ", x)
        x = rx_isnull.sub(lambda m: " " + re.sub(r"\s+", " ", m.group(1).upper()), x)
        x = rx_between.sub(" BETWEEN ", x)
        x = rx_sym.sub(r" \1 ", x)
        x = re.sub(r"[ \t]+", " ", x).strip()
        return x

    while i < len(lines):
        m = rx_on.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        out.append(f"{ws}ON {norm_expr(m.group('rest'))}")
        i += 1

        while i < len(lines):
            m2 = rx_andor.match(lines[i])
            if not m2:
                break
            ws2 = m2.group("ws")
            kw = m2.group("kw").upper()
            out.append(f"{ws2}{kw} {norm_expr(m2.group('rest'))}")
            i += 1

    return "\n".join(out)


# ------------------------------
# SELECT lista: balvessző + '=' igazítás
# ------------------------------

def _normalize_select_list_commas(s: str) -> str:
    """
    SELECT listát balvesszőssé alakítja a FROM-ig.
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_sel = re.compile(r"^(?P<ws>\s*)SELECT\s{3}(?P<rest>.*)$", re.IGNORECASE)
    rx_from = re.compile(r"^\s*FROM\s{5}\b", re.IGNORECASE)

    def split_top_level_csv(expr: str):
        parts = []
        buf = []
        depth = 0
        for ch in expr:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(depth - 1, 0)
            if ch == "," and depth == 0:
                parts.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        tail = "".join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    while i < len(lines):
        m = rx_sel.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        first_prefix = ws + "SELECT   "
        rest0 = m.group("rest").strip()

        block_parts = []
        if rest0:
            # ha inline vesszők vannak
            block_parts.append(rest0)

        i += 1
        while i < len(lines) and not rx_from.match(lines[i]):
            ln = lines[i].strip()
            if ln:
                if ln.startswith(","):
                    ln = ln[1:].lstrip()
                block_parts.append(ln)
            i += 1

        joined = " ".join(block_parts).strip()
        items = split_top_level_csv(joined) if joined else []

        comma_ws = " " * max(len(first_prefix) - 2, 0)

        if items:
            out.append(first_prefix + items[0])
            for it in items[1:]:
                out.append(f"{comma_ws}, {it}")
        else:
            out.append(first_prefix.rstrip())

        if i < len(lines) and rx_from.match(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)


def _align_select_equals(s: str) -> str:
    """
    SELECT listában '=' oszlop igazítás (balvesszős sorokra).
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_sel = re.compile(r"^\s*SELECT\s{3}", re.IGNORECASE)
    rx_from = re.compile(r"^\s*FROM\s{5}\b", re.IGNORECASE)
    rx_item = re.compile(r"^(?P<pref>\s*,\s*)(?P<left>[^=]+?)\s*=\s*(?P<right>.+)$")

    while i < len(lines):
        if not rx_sel.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        block = [lines[i]]
        i += 1
        while i < len(lines) and not rx_from.match(lines[i]):
            block.append(lines[i])
            i += 1

        max_left = 0
        for ln in block:
            m = rx_item.match(ln)
            if m:
                max_left = max(max_left, len(m.group("left").strip()))

        for ln in block:
            m = rx_item.match(ln)
            if not m or max_left == 0:
                out.append(ln)
                continue
            pref = m.group("pref")
            left = m.group("left").strip().ljust(max_left)
            right = m.group("right").strip()
            out.append(f"{pref}{left} = {right}")

        if i < len(lines) and rx_from.match(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)


# ------------------------------
# WHERE igazítás (csak WHERE), IN/NOT IN kivétel + HEAD kompenzáció
# ------------------------------

def _align_where_on_ops(s: str) -> str:
    """
    EBH WHERE blokk:
    - AND/OR az első feltétel alá igazodik (nem a WHERE alá)
    - Operátor-oszlop igazítás (minden operátorra),
    - KIVÉTEL: IN / NOT IN -> csak 1 space, nincs oszlopos igazítás
    - HEAD (WHERE) sor '=' oszlopa igazodjon az AND/OR sorokéhoz: +4 kompenzáció a HEAD sorban.
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_where = re.compile(r"^(?P<ws>\s*)WHERE\s{4}(?P<rest>.*)$", re.IGNORECASE)
    rx_andor = re.compile(r"^(?P<ws>\s*)(?P<kw>AND|OR)\b(?P<rest>.*)$", re.IGNORECASE)

    rx_op = re.compile(
        r"^(?P<lhs>.+?)\s+(?P<op>NOT\s+IN|IN|IS\s+NOT\s+NULL|IS\s+NULL|NOT\s+LIKE|LIKE|BETWEEN|>=|<=|<>|!=|=|>|<)\s+(?P<rhs>.+)$",
        re.IGNORECASE,
    )

    while i < len(lines):
        m_where = rx_where.match(lines[i])
        if not m_where:
            out.append(lines[i])
            i += 1
            continue

        head_ws = m_where.group("ws")
        head_prefix = head_ws + "WHERE    "
        head_rest = m_where.group("rest").lstrip()
        cond_start_prefix = head_ws + (" " * len("WHERE    "))

        block = [("HEAD", "", head_rest)]
        i += 1

        while i < len(lines):
            m = rx_andor.match(lines[i])
            if not m:
                break
            kw = m.group("kw").upper()
            rest = m.group("rest").strip()
            block.append((kw, kw + " ", rest))
            i += 1

        max_lhs = 0
        parsed = []
        for kind, kw_text, rest in block:
            mo = rx_op.match(rest)
            if not mo:
                parsed.append((kind, kw_text, None, rest))
                continue
            lhs = mo.group("lhs").rstrip()
            op = re.sub(r"\s+", " ", mo.group("op").upper())
            rhs = mo.group("rhs").strip()
            parsed.append((kind, kw_text, (lhs, op, rhs), None))
            if op in ("IN", "NOT IN"):
                continue
            max_lhs = max(max_lhs, len(lhs))

        for kind, kw_text, parts, raw_rest in parsed:
            if parts is None:
                if kind == "HEAD":
                    out.append(head_prefix + (raw_rest or ""))
                else:
                    out.append(cond_start_prefix + kw_text + (raw_rest or ""))
                continue

            lhs, op, rhs = parts
            if op in ("IN", "NOT IN"):
                line = f"{lhs} {op} {rhs}"
            else:
                pad = (max_lhs - len(lhs)) + (4 if kind == "HEAD" else 0)
                line = f"{lhs}{' ' * pad} {op} {rhs}"

            if kind == "HEAD":
                out.append(head_prefix + line)
            else:
                out.append(cond_start_prefix + kw_text + line)

    return "\n".join(out)


# ------------------------------
# CASE WHEN kompakt
# ------------------------------

def _compact_case_when(s: str) -> str:
    """
    CASE\\nWHEN ...\\nTHEN ...\\nELSE ...\\nEND
    -> CASE WHEN ...\\n    THEN ...\\n    ELSE ...\\nEND
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_case_only = re.compile(r"^(?P<ws>\s*)CASE\s*$", re.IGNORECASE)
    rx_when = re.compile(r"^\s*WHEN\b", re.IGNORECASE)
    rx_then = re.compile(r"^\s*THEN\b", re.IGNORECASE)
    rx_else = re.compile(r"^\s*ELSE\b", re.IGNORECASE)
    rx_end = re.compile(r"^\s*END\b", re.IGNORECASE)

    while i < len(lines):
        m = rx_case_only.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        if i + 1 >= len(lines) or not rx_when.match(lines[i + 1]):
            out.append(lines[i])
            i += 1
            continue

        indent = m.group("ws")
        out.append(f"{indent}CASE {lines[i + 1].lstrip()}")
        i += 2

        when_indent = indent + (" " * 4)
        while i < len(lines):
            t = lines[i].lstrip()
            if rx_then.match(t):
                out.append(f"{when_indent}{t}")
                i += 1
                continue
            if rx_else.match(t):
                out.append(f"{when_indent}{t}")
                i += 1
                continue
            if rx_end.match(t):
                out.append(f"{indent}{t}")
                i += 1
                break
            out.append(lines[i])
            i += 1

    return "\n".join(out)


# ------------------------------
# A) CREATE TABLE oszlop/típus igazítás
# ------------------------------

def _align_create_table_columns(s: str) -> str:
    """
    CREATE TABLE (...) blokkokban:
    - oszlopnév igazítás a leghosszabb oszlopnévre
    - típus/constraint rész egységes oszlopból indul
    - kommentet (--) érintetlenül hagyjuk
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_create = re.compile(r"^\s*CREATE\s+TABLE\b", re.IGNORECASE)
    rx_open = re.compile(r"^\s*\(\s*$")
    rx_close = re.compile(r"^\s*\)\s*;?\s*$")

    rx_col = re.compile(r"^(?P<indent>\s*)(?P<comma>,\s*)?(?P<name>\[[^\]]+\]|[A-Za-z0-9_#]+)\s+(?P<rest>.+)$")

    while i < len(lines):
        if not rx_create.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        out.append(lines[i])
        i += 1

        if i >= len(lines) or not rx_open.match(lines[i]):
            continue

        out.append(lines[i])
        i += 1

        block_start = i
        col_rows = []
        max_name_len = 0

        while i < len(lines) and not rx_close.match(lines[i]):
            ln = lines[i]
            m = rx_col.match(ln)
            if m and not ln.lstrip().startswith("--") and not ln.lstrip().startswith("/*"):
                indent = m.group("indent")
                comma = m.group("comma") or ""
                name = m.group("name")
                rest = m.group("rest")

                comment = ""
                if "--" in rest:
                    a, b = rest.split("--", 1)
                    rest = a.rstrip()
                    comment = "--" + b

                max_name_len = max(max_name_len, len(name))
                col_rows.append((i, indent, comma, name, rest, comment))
            i += 1

        idx_map = {idx: (indent, comma, name, rest, comment) for idx, indent, comma, name, rest, comment in col_rows}

        for j in range(block_start, i):
            if j not in idx_map:
                out.append(lines[j])
                continue
            indent, comma, name, rest, comment = idx_map[j]
            rebuilt = f"{indent}{comma}{name.ljust(max_name_len)} {rest.lstrip()}"
            if comment:
                rebuilt = f"{rebuilt} {comment}".rstrip()
            out.append(rebuilt)

        if i < len(lines) and rx_close.match(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)


# ------------------------------
# INSERT oszloplista (EBH: vesszős sorok -2 indent)
# ------------------------------

def _normalize_insert_column_list(s: str) -> str:
    """
    INSERT INTO ... ( oszloplista ):

         (
              col1
            , col2
            , col3
         )
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_insert = re.compile(r"^\s*INSERT\s+INTO\b", re.IGNORECASE)
    rx_open = re.compile(r"^\s*\(\s*$")
    rx_close = re.compile(r"^\s*\)\s*$")
    rx_col = re.compile(r"^(?P<ws>\s*)(?P<comma>,\s*)?(?P<name>\[[^\]]+\]|[A-Za-z0-9_#]+)\s*$")

    while i < len(lines):
        if not rx_insert.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        out.append(lines[i])
        i += 1

        while i < len(lines) and lines[i].strip() == "":
            out.append(lines[i])
            i += 1

        if i >= len(lines) or not rx_open.match(lines[i]):
            continue

        out.append(lines[i])
        i += 1

        block_start = i
        col_idxs = []
        base_ws = None

        while i < len(lines) and not rx_close.match(lines[i]):
            ln = lines[i]
            m = rx_col.match(ln)
            if m:
                if base_ws is None and m.group("comma") is None:
                    base_ws = m.group("ws")
                col_idxs.append(i)
            i += 1

        if base_ws is None and col_idxs:
            m0 = rx_col.match(lines[col_idxs[0]])
            base_ws = m0.group("ws") if m0 else ""

        comma_ws = base_ws[:-2] if base_ws and len(base_ws) >= 2 else (base_ws or "")

        for j in range(block_start, i):
            ln = lines[j]
            if j not in col_idxs:
                out.append(ln)
                continue
            m = rx_col.match(ln)
            if not m:
                out.append(ln)
                continue
            name = m.group("name")
            if m.group("comma"):
                out.append(f"{comma_ws}, {name}")
            else:
                out.append(f"{base_ws}{name}")

        if i < len(lines) and rx_close.match(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)


# ------------------------------
# VALUES tuple lista (EBH: vesszős sorok -2 indent)
# ------------------------------

def _normalize_values_list(s: str) -> str:
    """
    VALUES
         ( ... )
       , ( ... )
       , ( ... )
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_values = re.compile(r"^\s*VALUES\s*$", re.IGNORECASE)
    rx_tuple = re.compile(r"^(?P<ws>\s*)(?P<comma>,\s*)?\(.*$", re.IGNORECASE)

    while i < len(lines):
        if not rx_values.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        out.append(lines[i])
        i += 1

        while i < len(lines) and lines[i].strip() == "":
            out.append(lines[i])
            i += 1

        if i >= len(lines) or not rx_tuple.match(lines[i]):
            continue

        m_first = rx_tuple.match(lines[i])
        base_ws = m_first.group("ws")
        comma_ws = base_ws[:-2] if len(base_ws) >= 2 else base_ws

        # első tuple sor: vessző nélkül
        out.append(base_ws + lines[i].lstrip().lstrip(",").lstrip())
        i += 1

        while i < len(lines):
            ln = lines[i]
            if ln.strip() == "":
                out.append(ln)
                i += 1
                continue
            if not rx_tuple.match(ln):
                break

            stripped = ln.lstrip()
            if stripped.startswith(","):
                rest = stripped[1:].lstrip()
                out.append(f"{comma_ws}, {rest}")
            else:
                out.append(base_ws + stripped)

            i += 1

        continue

    return "\n".join(out)


# ------------------------------
# D) GROUP BY / ORDER BY lista (balvessző + indent)
# ------------------------------

def _normalize_group_order_by_lists(s: str) -> str:
    """
    ORDER BY col1
           , col2
           , col3
    GROUP BY col1
           , col2
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_clause = re.compile(r"^(?P<ws>\s*)(?P<kw>(ORDER|GROUP)\s+BY)\s+(?P<rest>.*)$", re.IGNORECASE)
    rx_break = re.compile(
        r"^\s*(SELECT\b|FROM\b|WHERE\b|HAVING\b|ORDER\s+BY\b|GROUP\s+BY\b|UNION\b|EXCEPT\b|INTERSECT\b|INSERT\b|UPDATE\b|DELETE\b|MERGE\b|WITH\b|RETURN\b)\b",
        re.IGNORECASE,
    )

    def split_csv(expr: str):
        items = []
        buf = []
        depth = 0
        for ch in expr:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(depth - 1, 0)
            if ch == "," and depth == 0:
                items.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        tail = "".join(buf).strip()
        if tail:
            items.append(tail)
        return items

    while i < len(lines):
        m = rx_clause.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        kw = m.group("kw")
        rest = m.group("rest").strip()

        first_expr_col = len(ws) + len(kw) + 1
        comma_ws = " " * max(first_expr_col - 2, 0)

        items = split_csv(rest)
        if items:
            out.append(f"{ws}{kw} {items[0]}")
            for it in items[1:]:
                out.append(f"{comma_ws}, {it}")
        else:
            out.append(lines[i])

        i += 1

        while i < len(lines):
            ln = lines[i]
            if ln.strip() == "":
                out.append(ln)
                i += 1
                continue
            if rx_break.match(ln):
                break

            stripped = ln.strip()
            if stripped.startswith(","):
                out.append(f"{comma_ws}, {stripped[1:].strip()}")
            else:
                out.append(f"{comma_ws}, {stripped}")
            i += 1

    return "\n".join(out)


# ------------------------------
# Fő formázó pipeline
# ------------------------------

def format_sql(sql: str) -> str:
    s = sql.replace("\r", "")

    # noformat blokkok pajzs
    s, nofmt_tokens = _shield_noformat_blocks(s)

    # CTE záró ')' igazítás még a komment/string pajzs előtt? -> itt már OK, de
    # string/komment pajzsot úgyis használjuk; viszont a CTE-zárójel számolás
    # biztonságosabb pajzsolt állapotban.
    # Előbb komment/string pajzs:
    s, shield_tokens = _shield_comments_and_strings(s)

    # HTML entity dekód (string/komment nem sérül)
    s = _decode_html_entities(s)

    # szeparátor + nolock + prefix
    s = _normalize_separator_nolock_and_prefixes(s)

    # SELECT ... FROM egy sorban -> szét
    s = _split_select_from(s)

    # ÚJ: WHERE tördelés + IN blokk indent
    s = _explode_where_lines_and_indent_in_blocks(s)

    # JOIN/ON indent + ON spacing
    s = _normalize_join_on_indent(s)
    s = _normalize_on_spacing(s)

    # ÚJ: IN (subquery) blokk összehúzás + indent
    s = _normalize_in_subquery_blocks(s)

    # >>> EZ ÚJ (D előtt, WHERE align előtt):
    s = _normalize_parenthesized_where_blocks(s)

    # SELECT listák (balvessző + '=' igazítás)
    s = _normalize_select_list_commas(s)
    s = _align_select_equals(s)

    # WHERE igazítás (csak WHERE)
    s = _align_where_on_ops(s)
    s = _normalize_where_continuation_and_or(s)  # <-- ez húzza be az elszabadult AND-ot
    # CASE
    s = _compact_case_when(s)

    # CREATE TABLE
    s = _align_create_table_columns(s)

    # CTE záró ')'
    s = _align_cte_closing_paren(s)

    # INSERT oszloplista + VALUES lista
    s = _normalize_insert_column_list(s)
    s = _normalize_values_list(s)

    # D: GROUP/ORDER BY lista
    s = _normalize_group_order_by_lists(s)

    # pajzsok vissza
    s = _unshield(s, shield_tokens)
    s = _unshield_noformat_blocks(s, nofmt_tokens)

    # placeholder ne maradjon
    if "__EBH_SHIELD_" in s or "__EBH_NOFMT_" in s:
        raise RuntimeError("UNSHIELD hibás: placeholder maradt a kimenetben")

    return s.rstrip() + "\n"


# ------------------------------
# GUI (kétpaneles)
# ------------------------------

def main():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1200x700")

    outer = ttk.Frame(root, padding=10)
    outer.pack(fill=tk.BOTH, expand=True)

    top = ttk.Frame(outer)
    top.pack(fill=tk.X, pady=(0, 8))

    paned = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
    paned.pack(fill=tk.BOTH, expand=True)

    left = ttk.Labelframe(paned, text="Eredeti (Input)")
    right = ttk.Labelframe(paned, text="Formázott (Output)")
    paned.add(left, weight=1)
    paned.add(right, weight=1)

    # Input
    input_text = tk.Text(left, wrap=tk.NONE, undo=True)
    input_text.grid(row=0, column=0, sticky="nsew")

    in_y = ttk.Scrollbar(left, orient=tk.VERTICAL, command=input_text.yview)
    in_x = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=input_text.xview)
    input_text.configure(yscrollcommand=in_y.set, xscrollcommand=in_x.set)

    in_y.grid(row=0, column=1, sticky="ns")
    in_x.grid(row=1, column=0, sticky="ew")

    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)

    # Output (read-only)
    output_text = tk.Text(right, wrap=tk.NONE)
    output_text.grid(row=0, column=0, sticky="nsew")
    output_text.configure(state=tk.DISABLED)

    out_y = ttk.Scrollbar(right, orient=tk.VERTICAL, command=output_text.yview)
    out_x = ttk.Scrollbar(right, orient=tk.HORIZONTAL, command=output_text.xview)
    output_text.configure(yscrollcommand=out_y.set, xscrollcommand=out_x.set)

    out_y.grid(row=0, column=1, sticky="ns")
    out_x.grid(row=1, column=0, sticky="ew")

    right.rowconfigure(0, weight=1)
    right.columnconfigure(0, weight=1)

    def set_output(text: str):
        output_text.configure(state=tk.NORMAL)
        output_text.delete("1.0", tk.END)
        output_text.insert(tk.END, text)
        output_text.configure(state=tk.DISABLED)

    def copy_output():
        root.clipboard_clear()
        root.clipboard_append(output_text.get("1.0", tk.END))

    def do_format():
        try:
            src = input_text.get("1.0", tk.END)
            res = format_sql(src)
            set_output(res)
        except Exception as ex:
            messagebox.showerror("Hiba", str(ex))

    ttk.Button(top, text="Formázás", command=do_format).pack(side=tk.LEFT)
    ttk.Button(top, text="Másolás (Output)", command=copy_output).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Label(
        top,
        text="Tipp: Ctrl+V / Ctrl+A az Input mezőben, Output másolás a gombbal.",
        foreground="#666",
    ).pack(side=tk.RIGHT)

    root.mainloop()


if __name__ == "__main__":
    main()
