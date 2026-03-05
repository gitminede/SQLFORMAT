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
    # CRLF normalizálás: a \r eltávolítása elég (CRLF -> \n marad)
    s = sql.replace("\r", "")

    # HTML entity visszaalakítás
    for rx, repl in _RX_HTML:
        s = rx.sub(repl, s)

    # szeparátor normalizálás
    s = _RX_SEP.sub("-------------------------------------------------------------------------------", s)

    # NOLOCK normalizálás (WITH nélkül, nagybetűvel)
    s = _RX_WITH_NOLOCK.sub("( NOLOCK )", s)
    s = _RX_BARE_NOLOCK.sub("( NOLOCK )", s)

    # Kulcsszó prefixek (kulcsszó után nincs külön sor)
    s = re.sub(r"\bSELECT\s+", "SELECT   ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bFROM\s+", "FROM     ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bWHERE\s+", "WHERE    ", s, flags=re.IGNORECASE)

    # JOIN / ON indent a megbeszélt mintára
    s = _normalize_join_on_indent(s)
    s = _normalize_on_spacing(s)      # <-- EZ 
    # SELECT-listában '=' oszlop igazítás (vessző bal oldalon feltételezve)
    s = _align_select_equals(s)

    # WHERE/ON blokkokban operátor-oszlop igazítás (minden operátorra),
    # és AND/OR a *WHERE/ON utáni első feltétel* alatt legyen
    s = _align_where_on_ops(s)

    # CASE WHEN kompakt (CASE WHEN egy sorban, THEN/ELSE a WHEN alatt)
    s = _compact_case_when(s)

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


# ------------------------------
# GUI
# ------------------------------
def main():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("1000x650")

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frm, text="Ide másold be az SQL-t:").pack(anchor=tk.W)

    txt = tk.Text(frm, wrap=tk.NONE, undo=True)
    txt.pack(fill=tk.BOTH, expand=True, pady=(6, 10))

    btns = ttk.Frame(frm)
    btns.pack(fill=tk.X)

    def show_output(formatted: str):
        win = tk.Toplevel(root)
        win.title("Formázott eredmény")
        win.geometry("1000x650")

        out = tk.Text(win, wrap=tk.NONE)
        out.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        out.insert(tk.END, formatted)

        bar = ttk.Frame(win, padding=10)
        bar.pack(fill=tk.X)

        def copy_all():
            win.clipboard_clear()
            win.clipboard_append(out.get("1.0", tk.END))

        ttk.Button(bar, text="Másolás", command=copy_all).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Bezárás", command=win.destroy).pack(side=tk.RIGHT, padx=(0, 8))

    def do_format():
        try:
            src = txt.get("1.0", tk.END)
            res = format_sql(src)
            show_output(res)
        except Exception as ex:
            messagebox.showerror("Hiba", str(ex))

    ttk.Button(btns, text="Formázás", command=do_format).pack(side=tk.LEFT)
    ttk.Label(btns, text="Tipp: Ctrl+V / Ctrl+A", foreground="#666").pack(side=tk.RIGHT)

    root.mainloop()


if __name__ == "__main__":
    main()
