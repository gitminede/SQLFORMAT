import re

_RX_SEP = re.compile(r"^-{5,}\s*$", re.MULTILINE)
_RX_WITH_NOLOCK = re.compile(r"\bWITH\s*\(\s*NOLOCK\s*\)", re.IGNORECASE)
_RX_BARE_NOLOCK = re.compile(r"\(\s*NOLOCK\s*\)", re.IGNORECASE)

_RX_HTML = [
    (re.compile(r"&lt;", re.IGNORECASE), "<"),
    (re.compile(r"&gt;", re.IGNORECASE), ">"),
    (re.compile(r"&amp;", re.IGNORECASE), "&"),
]


def format_sql(sql: str) -> str:
    """EBH-stílusú, pragmatikus formázás (regex-alapú)."""

    s = sql.replace("
", "
").replace("", "
")

    for rx, repl in _RX_HTML:
        s = rx.sub(repl, s)

    s = _RX_SEP.sub("-------------------------------------------------------------------------------", s)

    s = _RX_WITH_NOLOCK.sub("( NOLOCK )", s)
    s = _RX_BARE_NOLOCK.sub("( NOLOCK )", s)

    s = re.sub(r"\bSELECT\s+", "SELECT   ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bFROM\s+",   "FROM     ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bWHERE\s+",  "WHERE    ", s, flags=re.IGNORECASE)

    join_indent = " " * 9
    on_indent = " " * 11
    s = re.sub(
        r"^\s*(INNER\s+JOIN|LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|FULL\s+(?:OUTER\s+)?JOIN|CROSS\s+APPLY|OUTER\s+APPLY|JOIN)\b",
        lambda m: join_indent + m.group(0).lstrip(),
        s,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    s = re.sub(r"^\s*ON\b", on_indent + "ON", s, flags=re.IGNORECASE | re.MULTILINE)

    s = _align_select_equals(s)
    s = _align_where_on_ops(s)
    s = _compact_case_when(s)

    return s.rstrip() + "
"


def _align_select_equals(s: str) -> str:
    lines = s.split("
")
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
            out.append(f"{m.group('pref')}{m.group('left').strip().ljust(max_left)} = {m.group('right').strip()}")

        if i < len(lines) and rx_from.search(lines[i]):
            out.append(lines[i])
            i += 1

    return "
".join(out)


def _align_where_on_ops(s: str) -> str:
    lines = s.split("
")
    out = []
    i = 0

    rx_head = re.compile(r"^\s*(WHERE\s{4}|ON\b)", re.IGNORECASE)
    rx_andor = re.compile(r"^\s*(AND|OR)\b", re.IGNORECASE)

    rx_prefix = re.compile(r"^(?P<prefix>\s*(WHERE\s{4}|ON\s*|AND\s+|OR\s+))(?P<rest>.*)$", re.IGNORECASE)
    rx_op = re.compile(
        r"^(?P<lhs>.+?)\s+(?P<op>NOT\s+IN|IN|IS\s+NOT\s+NULL|IS\s+NULL|NOT\s+LIKE|LIKE|BETWEEN|>=|<=|<>|!=|=|>|<)\s+(?P<rhs>.+)$",
        re.IGNORECASE,
    )

    while i < len(lines):
        if not rx_head.search(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        block = [lines[i]]
        i += 1
        while i < len(lines) and rx_andor.match(lines[i]):
            block.append(lines[i])
            i += 1

        parsed = []
        max_lhs = 0
        for ln in block:
            m = rx_prefix.match(ln)
            if not m:
                parsed.append((ln, None))
                continue
            prefix = m.group("prefix")
            rest = m.group("rest").strip()
            mo = rx_op.match(rest)
            if not mo:
                parsed.append((ln, None))
                continue
            lhs = mo.group("lhs").rstrip()
            op = re.sub(r"\s+", " ", mo.group("op").upper())
            rhs = mo.group("rhs").strip()
            max_lhs = max(max_lhs, len(lhs))
            parsed.append((prefix, (lhs, op, rhs)))

        for raw, parts in parsed:
            if parts is None:
                out.append(raw)
            else:
                prefix, (lhs, op, rhs) = raw, parts
                out.append(f"{prefix}{lhs.ljust(max_lhs)} {op} {rhs}")

    return "
".join(out)


def _compact_case_when(s: str) -> str:
    lines = s.split("
")
    out = []
    i = 0
    rx_case = re.compile(r"\bCASE\s*$", re.IGNORECASE)
    rx_when = re.compile(r"^\s*WHEN\b", re.IGNORECASE)
    rx_then = re.compile(r"^\s*THEN\b", re.IGNORECASE)
    rx_else = re.compile(r"^\s*ELSE\b", re.IGNORECASE)
    rx_end = re.compile(r"^\s*END\b", re.IGNORECASE)

    while i < len(lines):
        if not rx_case.search(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        if i + 1 >= len(lines) or not rx_when.search(lines[i + 1]):
            out.append(lines[i])
            i += 1
            continue

        indent = re.match(r"^(\s*)", lines[i]).group(1)
        out.append(f"{indent}CASE {lines[i + 1].lstrip()}")
        i += 2

        when_indent = indent + " " * 4
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

    return "
".join(out)
