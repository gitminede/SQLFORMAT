import re
import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "EBH SQL Formatter"

# ------------------------------
# EBH-stílusú pragmatikus formázó
# ------------------------------

_RX_SEP = re.compile(r"^-{5,}\s*$", re.MULTILINE)
_RX_WITH_NOLOCK = re.compile(r"\bWITH\s*\(\s*NOLOCK\s*\)", re.IGNORECASE)
_RX_BARE_NOLOCK = re.compile(r"\(\s*NOLOCK\s*\)", re.IGNORECASE)

_RX_HTML = [
    (re.compile(r"&lt;", re.IGNORECASE), "<"),
    (re.compile(r"&gt;", re.IGNORECASE), ">"),
    (re.compile(r"&amp;", re.IGNORECASE), "&"),
]


def format_sql(sql: str) -> str:
    # CRLF normalizálás (ne legyenek \r-ek)
    s = sql.replace("\r", "")

    # 0) Pajzs: kommentek + stringek kivétele
    s, tokens = _shield_comments_and_strings(s)


    s = _split_select_from(s)
    s = _uppercase_order_group(s)


    # 1) HTML entity visszaalakítás (MOST már biztonságos, mert string/komment nem érintett)
    for rx, repl in _RX_HTML:
        s = rx.sub(repl, s)

    # 2) szeparátor normalizálás
    s = _RX_SEP.sub("-------------------------------------------------------------------------------", s)

    # 3) NOLOCK normalizálás
    s = _RX_WITH_NOLOCK.sub("( NOLOCK )", s)
    s = _RX_BARE_NOLOCK.sub("( NOLOCK )", s)

    # 4) Prefixek
    s = re.sub(r"\bSELECT\s+", "SELECT   ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bFROM\s+", "FROM     ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bWHERE\s+", "WHERE    ", s, flags=re.IGNORECASE)

    # 5) JOIN / ON indent + ON spacing (a korábbi javításaid szerint)
    s = _normalize_join_on_indent(s)
    s = _normalize_on_spacing(s)
    s = _normalize_select_list_commas(s)  # <-- ÚJ: SELECT lista balvesszőssé
    # 6) SELECT '=' igazítás
    s = _align_select_equals(s)

    # 7) WHERE operátor-oszlop igazítás (csak WHERE, ON-t nem)
    s = _align_where_on_ops(s)

    
    s = _normalize_parenthesized_where_groups(s)  # <-- új
    s = _normalize_set_lists(s)                   # <-- ú


    # 8) CASE WHEN kompakt
    s = _compact_case_when(s)

    # 9) CREATE TABLE oszlop/típus igazítás (A pontból)
    s = _align_create_table_columns(s)
    s = _align_cte_closing_paren(s)
    s = _normalize_values_list(s)
    s = _normalize_insert_column_list(s)
    s = _normalize_group_order_by_lists(s)   
    s = _normalize_update_set_list(s)   # <-- UPDATE SET patch
    # 10) Pajzs vissza:ek + stringek eredetije
    s = _unshield(s, tokens)

    return s.rstrip() + "\n"



def _normalize_join_on_indent(s: str) -> str:
    """
    EBH mintára:
    - JOIN a FROM utáni forrás oszlopa alá (FROM sor behúzását figyelembe véve)
    - ON a JOIN alá +2 szóközzel
    Példa:
        <ws>FROM     <src>
        <ws>         JOIN <src2>
        <ws>           ON <cond>
    ahol <ws> a FROM sor eleji whitespace.
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

    active_ws = None
    join_ws = None
    on_ws = None

    for ln in lines:
        m_from = rx_from.match(ln)
        if m_from:
            active_ws = m_from.group("ws")
            # FROM + 5 space = 9 karakter “prefix”; JOIN ennek az oszlopnak megfelelően indul
            join_ws = active_ws + (" " * 9)
            on_ws = active_ws + (" " * 11)  # JOIN +2
            out.append(ln)
            continue

        # Ha új clause kezdődik, FROM-blokk vége
        if active_ws and rx_clause_end.match(ln):
            active_ws = None
            join_ws = None
            on_ws = None
            out.append(ln)
            continue

        if join_ws and rx_join.match(ln):
            out.append(join_ws + ln.lstrip())
            continue

        if on_ws and rx_on.match(ln):
            rest = re.sub(r"^\s*ON\s+", "", ln, flags=re.IGNORECASE)
            out.append(on_ws + "ON " + rest.strip())
            continue

        out.append(ln)

    return "\n".join(out)

def _normalize_set_lists(s: str) -> str:
    """
    UPDATE SET / MERGE ... UPDATE SET lista EBH-stílusú formázása:

    UPDATE t
    SET    col1 = expr1
         , col2 = expr2
    WHERE ...

    illetve MERGE esetén:
    WHEN MATCHED THEN UPDATE
    SET    col1 = expr1
         , col2 = expr2

    Szabályok:
    - 'SET ' után az első assignment ugyanabban a sorban marad (SET prefix + első elem)
    - további elemek balvesszősen: ', <assignment>'
    - '=' oszlop igazítás a SET listában (lhs-ek alapján)
    - nem bontjuk szét a vesszőket zárójel-depth > 0 esetén (pl. függvényhívások)
    """
    lines = s.split("\n")
    out = []
    i = 0

    # SET sor felismerése
    rx_set = re.compile(r"^(?P<ws>\s*)SET\s+(?P<rest>.*)$", re.IGNORECASE)

    # hol ér véget a SET blokk
    rx_break = re.compile(
        r"^\s*(WHERE\b|FROM\b|OUTPUT\b|WHEN\b|GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|UNION\b|EXCEPT\b|INTERSECT\b|RETURN\b|;)\b",
        re.IGNORECASE,
    )

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

    def parse_assignment(a: str):
        # egyszerű assignment bontás a legelső '=' mentén (zárójel nélkül már shieldelt)
        # ha nincs '=', None
        if "=" not in a:
            return None
        left, right = a.split("=", 1)
        return left.strip(), right.strip()

    while i < len(lines):
        m = rx_set.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        first_expr_col = len(ws) + len("SET ")  # SET után egy space már benne
        comma_col = max(first_expr_col - 2, 0)
        comma_ws = " " * comma_col
        head_prefix = ws + "SET "

        # 1) begyűjtjük a teljes SET listát (aktuális sor + következő vesszős sorok)
        items_raw = []
        rest0 = m.group("rest").strip()
        items_raw.extend(split_top_level_csv(rest0))
        i += 1

        while i < len(lines):
            ln = lines[i]
            if ln.strip() == "":
                # üres sort átengedjük, de SET listát lezárjuk (EBH-ban nem szokott üres sor a listában)
                break
            if rx_break.match(ln):
                break

            stripped = ln.strip()
            # tipikus folytatás: ", something"
            if stripped.startswith(","):
                items_raw.extend(split_top_level_csv(stripped[1:].strip()))
                i += 1
                continue

            # ha valaki vessző nélkül folytatta, tekintsük további elemnek
            items_raw.extend(split_top_level_csv(stripped))
            i += 1

        if not items_raw:
            out.append(lines[i])
            continue

        # 2) '=' oszlop igazítás (lhs max)
        parsed = []
        max_lhs = 0
        for it in items_raw:
            pr = parse_assignment(it)
            if pr is None:
                parsed.append((it, None))
                continue
            lhs, rhs = pr
            max_lhs = max(max_lhs, len(lhs))
            parsed.append((it, (lhs, rhs)))

        # 3) kiírás
        # első elem a SET sorban
        first_it, first_parts = parsed[0]
        if first_parts is None:
            out.append(f"{head_prefix}{first_it}")
        else:
            lhs, rhs = first_parts
            out.append(f"{head_prefix}{lhs.ljust(max_lhs)} = {rhs}")

        # további elemek balvesszővel, egységes indenttel
        for it, parts in parsed[1:]:
            if parts is None:
                out.append(f"{comma_ws}, {it}")
            else:
                lhs, rhs = parts
                out.append(f"{comma_ws}, {lhs.ljust(max_lhs)} = {rhs}")

        # a while loop már i-n áll a SET blokk utáni soron; outer folytatja
        continue

    return "\n".join(out)

def _normalize_parenthesized_where_groups(s: str) -> str:
    """
    Zárójelezett WHERE csoportok tipikus EBH-formázása:
      WHERE    ( cond1
                 AND cond2 )
               OR ( cond3 )

    Csak a leggyakoribb esetekre lő:
    - WHERE sorban a feltétel '('-sel indul, és a blokkon belül AND/OR sorok vannak
    - OR top-level csoportokat is támogat (OR ( ... ) )

    Megjegyzés:
    - A stringek/kommentek már shieldeltek legyenek (B).
    - Nem teljes SQL boolean pretty-printer, de a ti tipikus mintáitokat jól hozza.
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_where = re.compile(r"^(?P<ws>\s*)WHERE\s{4}(?P<rest>.*)$", re.IGNORECASE)
    rx_andor_line = re.compile(r"^(?P<ws>\s*)(AND|OR)\b(?P<rest>.*)$", re.IGNORECASE)

    # belső AND split a zárójelen belül (depth 0)
    def split_top_level_and(expr: str):
        parts = []
        buf = []
        depth = 0
        tokens = expr.strip()

        # egyszerű tokenizálás: ' AND ' mentén depth=0-nál
        j = 0
        while j < len(tokens):
            ch = tokens[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(depth - 1, 0)

            # AND szó keresés (depth=0)
            if depth == 0 and tokens[j:j+4].upper() == "AND " and (j == 0 or tokens[j-1].isspace()):
                parts.append("".join(buf).strip())
                buf = []
                j += 4
                continue

            buf.append(ch)
            j += 1

        tail = "".join(buf).strip()
        if tail:
            parts.append(tail)
        return parts

    # 1-space operátor normalizálás (egyszerű, de shield miatt biztonságos)
    rx_sym = re.compile(r"\s*(>=|<=|<>|!=|=|>|<)\s*")
    rx_notin = re.compile(r"\s+(NOT\s+IN|IN)\s+", re.IGNORECASE)

    def norm_ops(x: str) -> str:
        x = re.sub(r"\s+", " ", x.strip())
        x = rx_notin.sub(lambda m: " " + re.sub(r"\s+", " ", m.group(1).upper()) + " ", x)
        x = rx_sym.sub(r" \1 ", x)
        x = re.sub(r"\s+", " ", x).strip()
        return x

    while i < len(lines):
        m = rx_where.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        prefix = ws + "WHERE    "
        rest = m.group("rest").lstrip()

        # csak akkor nyúlunk hozzá, ha '('-sel indul
        if not rest.startswith("("):
            out.append(lines[i])
            i += 1
            continue

        # levágjuk a külső zárójelet, ha a sorban már záródik
        # (ha nem záródik, akkor is kezeljük)
        rest_clean = rest

        # Megpróbáljuk kinyerni a csoport tartalmát a sorból:
        # "( ... )" -> "...", és jelöljük, hogy volt-e záró
        had_close = rest_clean.rstrip().endswith(")")
        inner = rest_clean.strip()
        if inner.startswith("("):
            inner = inner[1:].strip()
        if inner.endswith(")"):
            inner = inner[:-1].strip()

        # belső AND szétválasztás
        and_parts = split_top_level_and(inner)
        if not and_parts:
            out.append(lines[i])
            i += 1
            continue

        # első feltétel kezdő oszlopa: prefix + "( " -> +2
        cond_start_col = len(prefix) + 2
        cond_ws = " " * cond_start_col

        # kiírjuk az első sort: WHERE    ( <cond1>
        out.append(prefix + "( " + norm_ops(and_parts[0]))
        # további AND sorok: AND a cond_start_col alatt
        for p in and_parts[1:]:
            out.append(cond_ws + "AND " + norm_ops(p))

        # záró ) a legutolsó sor végére
        out[-1] = out[-1] + " )"

        # Most átnézzük a következő sorokat, ha vannak top-level OR csoportok:
        i += 1
        while i < len(lines):
            ln = lines[i]
            m2 = rx_andor_line.match(ln)
            if not m2:
                break

            kw = m2.group(2).upper()
            r2 = m2.group("rest").strip()

            # csak OR ( ... ) / AND ( ... ) csoportokat kezelünk itt
            if not r2.startswith("("):
                break

            inner2 = r2.strip()
            inner2 = inner2[1:].strip()
            if inner2.endswith(")"):
                inner2 = inner2[:-1].strip()

            parts2 = split_top_level_and(inner2)

            # OR keyword a WHERE után az első feltétel oszlop alá igazodjon (prefix nélküli rész)
            # a te stílusodban az OR a feltétel oszlopa alá kerül (nem WHERE alá)
            # ezért: ws + ' ' * len("WHERE    ") + 2 -> ugyanaz a cond_start_col
            or_ws = " " * (len(ws) + len("WHERE    "))
            # OR sor első: OR ( cond
            out.append(or_ws + "OR ( " + norm_ops(parts2[0]) if parts2 else (or_ws + "OR " + r2))

            if parts2:
                # további AND sorok a csoportban
                for p in parts2[1:]:
                    out.append(cond_ws + "AND " + norm_ops(p))
                out[-1] = out[-1] + " )"

            i += 1

        continue

    return "\n".join(out)




def _normalize_select_list_commas(s: str) -> str:
    """
    SELECT lista balvesszőssé alakítása:

    SELECT   col1
           , col2
           , alias = expr
    FROM     ...

    - SELECT blokkot a következő FROM-ig formáz (ugyanabban a szinten, a prefix alapján).
    - A listaelemeket top-level vesszők mentén szedi szét (zárójel depth 0).
    - A komment/string pajzs mellett biztonságos (placeholder-ben nincs top-level ',').
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
        rest = m.group("rest").strip()

        # DISTINCT / TOP kezelése (opcionális): az expr oszlopot ezek után számoljuk
        sel_prefix = ws + "SELECT   "
        extra = ""
        rest_up = rest.upper()
        if rest_up.startswith("DISTINCT "):
            extra = "DISTINCT "
            rest = rest[len("DISTINCT "):].lstrip()
        elif rest_up.startswith("TOP "):
            # TOP (n) / TOP n esetet nem bontunk szét részletesen: a TOP ... előtagot megtartjuk
            # Példa: TOP (10) col1, col2 -> extra = 'TOP (10) '
            # megpróbáljuk az első whitespace-ig venni a TOP utáni részt
            # (praktikus, pajzs mellett biztonságos)
            # TOP ... prefix végét az első olyan rész után vesszük, ami után legalább 1 space van
            # Egyszerűsítés: a TOP utáni első token(ek)-et a következő space-ig vesszük
            # (ha nem sikerül, marad simán)
            parts = rest.split(None, 2)
            if len(parts) >= 2:
                # parts[0] = TOP, parts[1] = (10) vagy 10
                extra = f"TOP {parts[1]} "
                rest = parts[2].lstrip() if len(parts) == 3 else ""

        sel_prefix = ws + "SELECT   " + extra

        # A folytató sorok vesszője 2-vel balrább legyen
        first_expr_col = len(sel_prefix)
        comma_ws = " " * max(first_expr_col - 2, 0)

        # Gyűjtjük a SELECT listát a FROM sorig
        block_lines = [rest] if rest else []
        i += 1
        while i < len(lines) and not rx_from.match(lines[i]):
            ln = lines[i].strip()
            if ln:
                # ha már balvesszős: ", ..." -> vessző levág
                if ln.startswith(","):
                    ln = ln[1:].lstrip()
                block_lines.append(ln)
            i += 1

        # Egységesítjük egy stringgé és szétvágjuk top-level vesszőkkel
        joined = " ".join(block_lines).strip()
        items = split_top_level_csv(joined) if joined else []

        if not items:
            # ha üres a lista (ritka), csak a prefix sor
            out.append(sel_prefix.rstrip())
        else:
            out.append(sel_prefix + items[0])
            for item in items[1:]:
                out.append(f"{comma_ws}, {item}")

        # FROM sor (ha van) vissza
        if i < len(lines) and rx_from.match(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)

def _normalize_update_set_list(s: str) -> str:
    """
    UPDATE ... SET lista EBH-stílusú formázása:

    SET      col1               = expr
           , col2               = expr
           , col3               = expr

    - Az első értékadás a SET sorban van
    - A további értékadások bal vesszős sorok
    - '=' oszlop igazítás a SET blokkon belül
    - Csak olyan SET blokkokra fut, ahol ténylegesen 'col = expr' van (nem SET NOCOUNT ON)
    """
    lines = s.split("\n")
    out = []
    i = 0

    # SET sor: indent + SET + rest
    rx_set = re.compile(r"^(?P<ws>\s*)SET\b(?P<rest>.*)$", re.IGNORECASE)

    # Clause/break: mikor ér véget a SET lista
    rx_break = re.compile(
        r"^\s*(WHERE\s{4}\b|FROM\s{5}\b|JOIN\b|LEFT\b|RIGHT\b|FULL\b|CROSS\b|OUTER\b|INNER\b|GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|OUTPUT\b|RETURN\b|UNION\b|EXCEPT\b|INTERSECT\b|;)\b",
        re.IGNORECASE,
    )

    # értékadás sor: opcionális leading comma + lhs = rhs
    rx_assign = re.compile(
        r"^(?P<comma>,\s*)?(?P<lhs>.+?)\s*=\s*(?P<rhs>.+?)\s*$"
    )

    while i < len(lines):
        m = rx_set.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        rest = m.group("rest").strip()

        # Csak akkor kezeljük, ha ténylegesen van '=' a SET sorban vagy közvetlenül utána jövő sorokban.
        looks_like_assign = ("=" in rest)
        if not looks_like_assign:
            # nézzük meg a következő 1-2 sort gyorsan
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and "=" in lines[j]:
                looks_like_assign = True

        if not looks_like_assign:
            # pl. SET NOCOUNT ON – hagyjuk
            out.append(lines[i])
            i += 1
            continue

        # EBH SET prefix: 'SET' + 6 space (összesen 9 char, mint WHERE    / FROM     logika)
        set_prefix = ws + "SET      "
        # A vesszős sorok indentje: SET prefix hossza - 2
        comma_ws = ws + (" " * (len("SET      ") - 2))

        # Gyűjtjük a SET blokk sorait:
        # - első értékadás: SET sor rest-je
        # - majd a további sorok, amíg break clause nem jön
        block = []
        block.append(("HEAD", rest))
        i += 1

        while i < len(lines):
            ln = lines[i]
            if ln.strip() == "":
                # üres sor -> a SET listát általában nem törjük, de engedjük és álljunk meg
                break
            if rx_break.match(ln):
                break

            # tipikusan: ", col = expr" vagy "col = expr"
            # (ha valami teljesen más, inkább megállunk)
            if "=" not in ln:
                break

            block.append(("ITEM", ln.strip()))
            i += 1

        # Parse: lhs/rhs + max lhs hossz (csak '='-os sorok)
        parsed = []
        max_lhs = 0

        for kind, txt in block:
            if kind == "HEAD":
                src = txt
            else:
                src = txt

            ma = rx_assign.match(src.lstrip())
            if not ma:
                parsed.append((kind, None, src))
                continue

            lhs = ma.group("lhs").strip()
            rhs = ma.group("rhs").strip()

            max_lhs = max(max_lhs, len(lhs))
            parsed.append((kind, (lhs, rhs), None))

        # Rebuild
        for idx, (kind, parts, raw) in enumerate(parsed):
            if parts is None:
                # ha valamiért nem parse-olható, hagyjuk “ahogy van”
                if kind == "HEAD":
                    out.append(ws + "SET " + (raw or ""))
                else:
                    out.append(comma_ws + ", " + (raw or ""))
                continue

            lhs, rhs = parts
            if kind == "HEAD":
                out.append(f"{set_prefix}{lhs.ljust(max_lhs)} = {rhs}")
            else:
                out.append(f"{comma_ws}, {lhs.ljust(max_lhs)} = {rhs}")

        # ha a SET blokk után üres sor jött, azt itt még kiírjuk, és léptetünk
        # (a ciklus elején a break miatt nem nyelte el)
        if i < len(lines) and lines[i].strip() == "":
            out.append(lines[i])
            i += 1

    # maradék
    while i < len(lines):
        out.append(lines[i])
        i += 1

    return "\n".join(out)


def _align_select_equals(s: str) -> str:
    lines = s.split("\n")
    out = []
    i = 0

    rx_sel = re.compile(r"^\s*SELECT\s{3}", re.IGNORECASE)
    rx_from = re.compile(r"^\s*FROM\s{5}", re.IGNORECASE)
    rx_item = re.compile(r"^(?P<pref>\s*,\s*)(?P<left>[^=]+?)\s*=\s*(?P<right>.+)$")

    while i < len(lines):
        if not rx_sel.search(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        block = [lines[i]]
        i += 1
        while i < len(lines) and not rx_from.search(lines[i]):
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

        if i < len(lines) and rx_from.search(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)


def _align_where_on_ops(s: str) -> str:
    """
    EBH WHERE blokk:
    - AND/OR az első feltétel alá igazodik (nem a WHERE alá)
    - Operátor-oszlop igazítás (minden operátorra)
    - KIVÉTEL: IN / NOT IN -> csak 1 szóköz, nincs oszlopos igazítás
    Megjegyzés: ON blokkot NEM igazítunk oszlopba (külön normalizáljuk).
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
                # HEAD sor kompenzáció: +4, hogy egyvonalban legyen az AND sorok '=' oszlopával
                pad = (max_lhs - len(lhs)) + (4 if kind == "HEAD" else 0)
                line = f"{lhs}{' ' * pad} {op} {rhs}"

            if kind == "HEAD":
                out.append(head_prefix + line)
            else:
                out.append(cond_start_prefix + kw_text + line)

    return "\n".join(out)

def _normalize_on_spacing(s: str) -> str:
    """
    ON (és az ON alatti AND/OR) feltételekben:
    - az operátorok körül 1 szóköz legyen
    - NOT IN / IN között is 1 szóköz logikusan: 'lhs NOT IN ( ... )'
    - NINCS oszlopos igazítás (nem padolunk)
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_on = re.compile(r"^(?P<ws>\s*)ON\s+(?P<rest>.*)$", re.IGNORECASE)
    rx_andor = re.compile(r"^(?P<ws>\s*)(?P<kw>AND|OR)\s+(?P<rest>.*)$", re.IGNORECASE)

    # szimbolikus operátorok köré 1 space
    rx_sym = re.compile(r"\s*(>=|<=|<>|!=|=|>|<)\s*")
    # IN/NOT IN/LIKE/BETWEEN/IS NULL alakok
    rx_wordops = [
        (re.compile(r"\s+(NOT\s+IN|IN)\s+", re.IGNORECASE), lambda m: f" {re.sub(r'\\s+', ' ', m.group(1).upper())} "),
        (re.compile(r"\s+(NOT\s+LIKE|LIKE)\s+", re.IGNORECASE), lambda m: f" {re.sub(r'\\s+', ' ', m.group(1).upper())} "),
        (re.compile(r"\s+(IS\s+NOT\s+NULL|IS\s+NULL)\b", re.IGNORECASE), lambda m: f" {re.sub(r'\\s+', ' ', m.group(1).upper())}"),
        (re.compile(r"\s+(BETWEEN)\s+", re.IGNORECASE), lambda m: " BETWEEN "),
    ]

    def norm_expr(expr: str) -> str:
        # összespacelés 1-re (de stringek védelme még nincs – később B pontnál hozzuk)
        x = re.sub(r"\s+", " ", expr.strip())
        # word operátorok
        for rx, repl in rx_wordops:
            x = rx.sub(repl, x)
        # szimbolikus operátorok
        x = rx_sym.sub(r" \1 ", x)
        # többszörös space vissza 1-re
        x = re.sub(r"\s+", " ", x).strip()
        return x

    while i < len(lines):
        m = rx_on.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        rest = norm_expr(m.group("rest"))
        out.append(f"{ws}ON {rest}")
        i += 1

        # ON alatti AND/OR sorok (ha vannak)
        while i < len(lines):
            m2 = rx_andor.match(lines[i])
            if not m2:
                break
            ws2 = m2.group("ws")
            kw = m2.group("kw").upper()
            rest2 = norm_expr(m2.group("rest"))
            out.append(f"{ws2}{kw} {rest2}")
            i += 1

    return "\n".join(out)

def _uppercase_order_group(s: str) -> str:
    s = re.sub(r"\border\s+by\b", "ORDER BY", s, flags=re.IGNORECASE)
    s = re.sub(r"\bgroup\s+by\b", "GROUP BY", s, flags=re.IGNORECASE)
    return s
    
def _split_select_from(s: str) -> str:
    """
    EBH-stílus: a SELECT és FROM SOHA nem lehet egy sorban.
    Ha ilyen:    SELECT   * FROM     aaa a
    akkor legyen:
                 SELECT   *
                 FROM     aaa a
    """
    lines = s.split("\n")
    out = []
    rx_sel_from = re.compile(r"^(?P<ws>\s*)SELECT\s+(?P<sel>.+?)\s+FROM\s+(?P<from>.+)$", re.IGNORECASE)

    for ln in lines:
        m = rx_sel_from.match(ln)
        if not m:
            out.append(ln)
            continue

        ws = m.group("ws")
        sel = m.group("sel").rstrip()
        frm = m.group("from").rstrip()
        out.append(f"{ws}SELECT   {sel}")
        out.append(f"{ws}FROM     {frm}")

    return "\n".join(out)
    
def _normalize_group_order_by_lists(s: str) -> str:
    """
    GROUP BY / ORDER BY lista EBH-stílusú formázása:

    ORDER BY col1
           , col2
           , col3

    GROUP BY col1
           , col2

    - Ha a clause sorban több elem van vesszővel: szétszedi több sorra (zárójel depth 0-nál).
    - Ha már több soros: a vesszős sorok behúzását egységesíti.
    - A vesszős sorok indentje: (első elem oszlopa) - 2.
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_clause = re.compile(r"^(?P<ws>\s*)(?P<kw>(ORDER|GROUP)\s+BY)\s+(?P<rest>.*)$", re.IGNORECASE)

    # Hol ér véget a GROUP/ORDER BY blokk (ha új clause kezdődik)
    rx_break = re.compile(
        r"^\s*(SELECT\b|FROM\b|WHERE\b|HAVING\b|ORDER\s+BY\b|GROUP\s+BY\b|UNION\b|EXCEPT\b|INTERSECT\b|INSERT\b|UPDATE\b|DELETE\b|MERGE\b|WITH\b|RETURN\b)\b",
        re.IGNORECASE,
    )

    def split_top_level_csv(expr: str):
        """Vessző mentén split csak zárójel-depth 0-nál (pajzs mellett biztonságos)."""
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
        m = rx_clause.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        ws = m.group("ws")
        kw = m.group("kw")
        rest = m.group("rest").strip()

        # Az első elem kezdőoszlopa: ws + kw + ' ' hossza
        first_expr_col = len(ws) + len(kw) + 1
        comma_col = max(first_expr_col - 2, 0)
        comma_ws = " " * comma_col

        # Clause sor felbontása: ha több elem van, szétszedjük
        items = split_top_level_csv(rest)

        # Írjuk ki az első sort
        if items:
            out.append(f"{ws}{kw} {items[0]}")
        else:
            out.append(lines[i])

        # További elemek -> bal vesszős sorok
        for item in items[1:]:
            out.append(f"{comma_ws}, {item}")

        i += 1

        # Ha a következő sorokban már voltak vesszős elemek (multi-line ORDER/GROUP), normalizáljuk őket is,
        # de csak addig, amíg nem jön új clause / statement rész.
        while i < len(lines):
            ln = lines[i]

            if ln.strip() == "":
                out.append(ln)
                i += 1
                continue

            if rx_break.match(ln):
                break

            # Csak azokat a sorokat igazítjuk, amelyek listatagnak néznek ki:
            # - ", ..." vagy sima "..." (ritkán), mindkettőt vesszős sorra húzzuk, kivéve ha már items-ből jött
            stripped = ln.strip()
            if stripped.startswith(","):
                item = stripped[1:].strip()
                out.append(f"{comma_ws}, {item}")
                i += 1
                continue

            # Ha valaki vessző nélkül írta a következő sort, tegyük vesszősre (EBH lista)
            # Pl. ORDER BY col1 \n col2  -> , col2
            out.append(f"{comma_ws}, {stripped}")
            i += 1

    return "\n".join(out)
    
def _normalize_insert_column_list(s: str) -> str:
    """
    INSERT INTO ... ( oszloplista ) blokkokban EBH stílus:
      (
              col1
            , col2
            , col3
      )

    Szabály:
    - az első (vessző nélküli) oszlopsor behúzása legyen base_ws
    - a vesszős sorok behúzása legyen base_ws - 2 space (comma_ws)
    - vessző bal oldalon: '<comma_ws>, <name>'
    - csak a sor eleji whitespace-t és a vessző előtti elcsúszásokat javítja
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_insert = re.compile(r"^\s*INSERT\s+INTO\b", re.IGNORECASE)
    rx_open = re.compile(r"^\s*\(\s*$")
    rx_close = re.compile(r"^\s*\)\s*$")

    # oszlopsor (csak név + opcionális leading comma)
    # tail: ritkán lehet, de hagyjuk (pl. shield placeholder vagy egyéb)
    rx_col = re.compile(
        r"^(?P<ws>\s*)(?P<comma>,\s*)?(?P<name>\[[^\]]+\]|[A-Za-z0-9_#]+)\s*(?P<tail>.*)?$"
    )

    while i < len(lines):
        if not rx_insert.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        # INSERT INTO sor
        out.append(lines[i])
        i += 1

        # whitespace sorok átengedése
        while i < len(lines) and lines[i].strip() == "":
            out.append(lines[i])
            i += 1

        # nyitó (
        if i >= len(lines) or not rx_open.match(lines[i]):
            continue

        out.append(lines[i])  # '('
        i += 1

        # gyűjtés a záró ')' sorig
        block_start = i
        col_idxs = []
        base_ws = None

        while i < len(lines) and not rx_close.match(lines[i]):
            ln = lines[i]
            if ln.strip() == "":
                i += 1
                continue

            m = rx_col.match(ln)
            if m:
                # első nem-vesszős sorból vesszük a base_ws-t (EBH minta szerint)
                if base_ws is None and m.group("comma") is None:
                    base_ws = m.group("ws")
                col_idxs.append(i)
            i += 1

        # ha nem találtunk base_ws-t (pl. minden sor vesszős), fallback: első col sor indentje
        if base_ws is None and col_idxs:
            m0 = rx_col.match(lines[col_idxs[0]])
            base_ws = m0.group("ws") if m0 else ""

        comma_ws = base_ws[:-2] if base_ws and len(base_ws) >= 2 else (base_ws or "")

        # újraépítés a blokkban
        for j in range(block_start, i):
            ln = lines[j]

            if j not in col_idxs or ln.strip() == "":
                out.append(ln)
                continue

            m = rx_col.match(ln)
            if not m:
                out.append(ln)
                continue

            name = m.group("name")
            comma = m.group("comma")
            tail = (m.group("tail") or "").rstrip()

            # tail-ben sokszor csak üres/whitespace; ha van tartalom, egy space-szel elválasztjuk
            tail_out = ""
            if tail:
                tail_out = " " + tail.strip()

            if comma:
                out.append(f"{comma_ws}, {name}{tail_out}".rstrip())
            else:
                out.append(f"{base_ws}{name}{tail_out}".rstrip())

        # záró ')'
        if i < len(lines) and rx_close.match(lines[i]):
            out.append(lines[i])
            i += 1

    # maradék
    while i < len(lines):
        out.append(lines[i])
        i += 1

    return "\n".join(out)


def _normalize_values_list(s: str) -> str:
    """
    VALUES blokkok sorainak EBH-stílusú igazítása.

    Elv:
      VALUES
               ( ... )
             , ( ... )
             , ( ... )

    - a VALUES utáni első tuple sor indentjét vesszük alapnak
    - a következő sorok: ugyanarra az indentre kerüljenek, bal oldali vesszővel: '<indent>, ( ... )'
    - csak azokra a sorokra hat, amelyek VALUES után közvetlenül tuple-ok: ( ... ) vagy , ( ... )
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_values = re.compile(r"^\s*VALUES\s*$", re.IGNORECASE)
    rx_tuple_line = re.compile(r"^(?P<ws>\s*)(?P<comma>,\s*)?\(\s*.*$", re.IGNORECASE)

    while i < len(lines):
        if not rx_values.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        # VALUES sort kiírjuk
        out.append(lines[i])
        i += 1

        # üres sorok átengedése, amíg el nem érünk az első tuple sorig
        while i < len(lines) and lines[i].strip() == "":
            out.append(lines[i])
            i += 1

        if i >= len(lines):
            break

        m_first = rx_tuple_line.match(lines[i])
        if not m_first:
            # nem klasszikus VALUES tuple lista -> nem nyúlunk hozzá
            continue

        base_ws = m_first.group("ws")
        comma_ws = base_ws[:-2] if len(base_ws) >= 2 else base_ws  # a te mintád: ', ' sorok 2-vel balrább
        # első sor: vessző nélkül
        first_line = base_ws + lines[i].lstrip().lstrip(",").lstrip()
        out.append(first_line)
        i += 1

        # további sorok: ', (' sorok igazítása
        while i < len(lines):
            ln = lines[i]
            if ln.strip() == "":
                out.append(ln)
                i += 1
                continue

            m = rx_tuple_line.match(ln)
            if not m:
                # kiléptünk a VALUESából
                break

            # ha a sor elején van vessző, akkor kötelezően: "<comma_ws>, ( ... )"
            stripped = ln.lstrip()
            if stripped.startswith(","):
                rest = stripped[1:].lstrip()
                out.append(f"{comma_ws}, {rest}")
            else:
                # ha nincs vessző (ritkább), hagyjuk az alap indenttel
                out.append(base_ws + stripped)

            i += 1

        # a while fent megállt egy nem-tuple soron -> az outer loop folytatja onnan
        continue

    # maradék sorok (ha i < len(lines) és a while nem futott le teljesen)
    while i < len(lines):
        out.append(lines[i])
        i += 1

    return "\n".join(out)

def _align_create_table_columns(s: str) -> str:
    """
    CREATE TABLE (...) blokkokban:
    - az oszlopnév és a típus/constraint rész igazítása oszlopokra
    - vessző bal oldalon marad
    - kommentet (--) a sor végén érintetlenül hagyjuk (csak a bal oldali spacinget rendezzük)
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_create = re.compile(r"^\s*CREATE\s+TABLE\b", re.IGNORECASE)
    rx_open = re.compile(r"^\s*\(\s*$")
    rx_close = re.compile(r"^\s*\)\s*;?\s*$")

    # oszlopdef sor: "       , col_name   TYPE ..."
    rx_col = re.compile(r"^(?P<indent>\s*)(?P<comma>,\s*)?(?P<name>\[[^\]]+\]|[A-Za-z0-9_#]+)\s+(?P<rest>.+)$")

    while i < len(lines):
        if not rx_create.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        # CREATE TABLE sort kiírjuk
        out.append(lines[i])
        i += 1

        # ha nincs nyitó zárójel a következő sorban, nincs mit igazítani
        if i >= len(lines) or not rx_open.match(lines[i]):
            continue

        # nyitó zárójel sor
        out.append(lines[i])
        i += 1

        # gyűjtjük az oszlopdef sorokat a zárójel bezárásáig
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

                # kommentet leválasztjuk, hogy a spacing ne nyúljon bele
                comment = ""
                if "--" in rest:
                    # csak az első '--' utáni részt tekintjük kommentnek
                    parts = rest.split("--", 1)
                    rest = parts[0].rstrip()
                    comment = "--" + parts[1]

                max_name_len = max(max_name_len, len(name))
                col_rows.append((i, indent, comma, name, rest, comment))
            i += 1

        # újraépítjük a blokkot
        col_idx_map = {idx: (indent, comma, name, rest, comment) for idx, indent, comma, name, rest, comment in col_rows}

        for j in range(block_start, i):
            if j not in col_idx_map:
                out.append(lines[j])
                continue

            indent, comma, name, rest, comment = col_idx_map[j]
            # 1 space a padded név és a rest között, és a rest balról trimelve
            rebuilt = f"{indent}{comma}{name.ljust(max_name_len)} {rest.lstrip()}"
            if comment:
                rebuilt = f"{rebuilt} {comment}".rstrip()
            out.append(rebuilt)

        # záró sor: ) vagy );
        if i < len(lines) and rx_close.match(lines[i]):
            out.append(lines[i])
            i += 1

    return "\n".join(out)
    
def _shield_comments_and_strings(s: str):
    """
    Kiszedi (shieldeli) a kommenteket és string literálokat placeholder-ekre.
    Visszaad: (shielded_text, tokens)
    tokens: {placeholder: original_text}
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

        # 1) Egysoros komment: -- ... \n
        if ch == "-" and i + 1 < n and s[i + 1] == "-":
            j = i + 2
            while j < n and s[j] != "\n":
                j += 1
            comment = s[i:j]          # \n nélkül
            out.append(new_token(comment))
            i = j
            continue

        # 2) Blokk komment: /* ... */
        if ch == "/" and i + 1 < n and s[i + 1] == "*":
            j = i + 2
            while j + 1 < n and not (s[j] == "*" and s[j + 1] == "/"):
                j += 1
            j = min(j + 2, n)         # záró */-ig (ha nincs, EOF)
            comment = s[i:j]
            out.append(new_token(comment))
            i = j
            continue

        # 3) String literál: '...' (SQL Server: '' escape)
        if ch == "'":
            j = i + 1
            while j < n:
                if s[j] == "'":
                    # '' -> escape, lépjünk tovább
                    if j + 1 < n and s[j + 1] == "'":
                        j += 2
                        continue
                    j += 1  # záró '
                    break
                j += 1
            string_lit = s[i:j]
            out.append(new_token(string_lit))
            i = j
            continue

        # default
        out.append(ch)
        i += 1

    return "".join(out), tokens


def _unshield(s: str, tokens: dict) -> str:
    """
    Placeholder-ek visszacserélése az eredeti tartalomra.
    """
    # a kulcsok egyediek és nincs bennük whitespace; sima replace elég és gyors
    for key, val in tokens.items():
        s = s.replace(key, val)
    return s

def _compact_case_when(s: str) -> str:
    """
    CASE\nWHEN ...\nTHEN ...\nELSE ...\nEND
    -> CASE WHEN ...\n    THEN ...\n    ELSE ...\nEND
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

def _align_cte_closing_paren(s: str) -> str:
    """
    CTE-kben az AS ( ... ) záró ')' igazítása a nyitó '(' oszlop alá.

    - Megkeresi az összes 'AS (' előfordulást (case-insensitive).
    - Onnan zárójel-szint számlálással megkeresi a hozzá tartozó záró ')'-t.
    - Csak akkor nyúl a záró sorhoz, ha a záró ')' a sorban önálló (esetleg ',' vagy ';' követi):
        )      vagy ),      vagy );      vagy ),;
      (whitespace körülötte lehet)
    - Ha a záró ')' nem önálló (pl. ') x' derived subquery alias), nem módosítja.
    """
    # Gyűjtsük ki előre az AS ( pozíciókat, hogy utólag visszafelé dolgozzunk
    rx_as_open = re.compile(r"\bAS\s*\(", re.IGNORECASE)
    matches = list(rx_as_open.finditer(s))
    if not matches:
        return s

    # Visszafelé módosítunk, hogy az indexek ne csússzanak el
    for m in reversed(matches):
        open_paren_pos = m.end() - 1  # '(' pozíció
        # Nyitó zárójel oszlopának (column) meghatározása
        line_start = s.rfind("\n", 0, open_paren_pos) + 1
        open_col = open_paren_pos - line_start

        # Zárójel-szint számlálás a nyitó '(' után
        depth = 1
        i = open_paren_pos + 1
        n = len(s)
        close_paren_pos = None

        while i < n:
            ch = s[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_paren_pos = i
                    break
            i += 1

        if close_paren_pos is None:
            continue  # nincs meg a párja (hibás SQL), hagyjuk

        # A záró ')' sorának meghatározása
        close_line_start = s.rfind("\n", 0, close_paren_pos) + 1
        close_line_end = s.find("\n", close_paren_pos)
        if close_line_end == -1:
            close_line_end = len(s)

        close_line = s[close_line_start:close_line_end]

        # Csak akkor igazítunk, ha a sor (strip után) csak ')' + opcionális ','/';'
        stripped = close_line.strip()
        # Megengedjük: ")", "),", ");", "),;", valamint whitespace körülötte
        if not re.fullmatch(r"\)\s*[,;]?\s*[,;]?\s*", stripped):
            continue

        # Vegyük ki a ')' utáni írásjeleket pontosan (pl. "),")
        # (stripeltből dolgozunk, mert a line eleji indentet úgyis újraírjuk)
        tail = stripped[1:].strip()  # ')' utáni rész: "", ",", ";", ",;" stb.
        new_line = (" " * open_col) + ")" + ((" " + tail) if tail else "")
        # Megjegyzés: a ",;" előtt nem akarunk plusz space-t -> ezért kis trükk:
        # ha tail csak írásjel(ek), akkor ne tegyünk közé szóközt
        if tail and re.fullmatch(r"[,;]{1,2}", tail):
            new_line = (" " * open_col) + ")" + tail

        # Cseréljük a sort az eredeti sorvégi newline megtartásával
        s = s[:close_line_start] + new_line + s[close_line_end:]

    return s

# ------------------------------
# GUI
# ------------------------------
def main():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1200x700")

    outer = ttk.Frame(root, padding=10)
    outer.pack(fill=tk.BOTH, expand=True)

    # Felső gombsor
    top = ttk.Frame(outer)
    top.pack(fill=tk.X, pady=(0, 8))

    # Középső rész: két panel egymás mellett
    paned = ttk.PanedWindow(outer, orient=tk.HORIZONTAL)
    paned.pack(fill=tk.BOTH, expand=True)

    left = ttk.Labelframe(paned, text="Eredeti (Input)")
    right = ttk.Labelframe(paned, text="Formázott (Output)")
    paned.add(left, weight=1)
    paned.add(right, weight=1)

    # --- Left text + scrollbars ---
    input_text = tk.Text(left, wrap=tk.NONE, undo=True)
    input_text.grid(row=0, column=0, sticky="nsew")

    in_y = ttk.Scrollbar(left, orient=tk.VERTICAL, command=input_text.yview)
    in_x = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=input_text.xview)
    input_text.configure(yscrollcommand=in_y.set, xscrollcommand=in_x.set)

    in_y.grid(row=0, column=1, sticky="ns")
    in_x.grid(row=1, column=0, sticky="ew")

    left.rowconfigure(0, weight=1)
    left.columnconfigure(0, weight=1)

    # --- Right text + scrollbars (read-only) ---
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

    # Helper: output mező írása read-only módban
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

    # Gombok
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
