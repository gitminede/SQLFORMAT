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

    # SELECT-listában '=' oszlop igazítás (vessző bal oldalon feltételezve)
    s = _align_select_equals(s)

    # WHERE/ON blokkokban operátor-oszlop igazítás (minden operátorra),
    # és AND/OR a *WHERE/ON utáni első feltétel* alatt legyen
    s = _align_where_on_ops(s)

    # CASE WHEN kompakt (CASE WHEN egy sorban, THEN/ELSE a WHEN alatt)
    s = _compact_case_when(s)

    return s.rstrip() + "\n"


def _normalize_join_on_indent(s: str) -> str:
    join_indent = " " * 9
    on_indent = " " * 11

    # JOIN sorok elejére fix indent
    s = re.sub(
        r"^\s*(INNER\s+JOIN|LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|FULL\s+(?:OUTER\s+)?JOIN|CROSS\s+APPLY|OUTER\s+APPLY|JOIN)\b",
        lambda m: join_indent + m.group(0).lstrip(),
        s,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # ON sorok elejére fix indent + 'ON ' (szóköz az ON után!)
    s = re.sub(r"^\s*ON\b", on_indent + "ON ", s, flags=re.IGNORECASE | re.MULTILINE)

    return s


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
    WHERE / ON blokk igazítás:
    - Az első sor: WHERE    <cond>  / ON <cond>
    - A további AND/OR sorok: a WHERE/ON utáni első feltétel oszlopa alá kerülnek
      (tehát nem a WHERE/ON alá)
    - Operátor-oszlop igazítás (minden operátorra, IN/NOT IN is).
    """
    lines = s.split("\n")
    out = []
    i = 0

    rx_where = re.compile(r"^(?P<ws>\s*)WHERE\s{4}(?P<rest>.*)$", re.IGNORECASE)
    rx_on = re.compile(r"^(?P<ws>\s*)ON\s+(?P<rest>.*)$", re.IGNORECASE)

    rx_andor = re.compile(r"^(?P<ws>\s*)(?P<kw>AND|OR)\b(?P<rest>.*)$", re.IGNORECASE)

    # operátor felismerés (minden operátor)
    rx_op = re.compile(
        r"^(?P<lhs>.+?)\s+(?P<op>NOT\s+IN|IN|IS\s+NOT\s+NULL|IS\s+NULL|NOT\s+LIKE|LIKE|BETWEEN|>=|<=|<>|!=|=|>|<)\s+(?P<rhs>.+)$",
        re.IGNORECASE,
    )

    while i < len(lines):
        m_where = rx_where.match(lines[i])
        m_on = rx_on.match(lines[i])

        if not (m_where or m_on):
            out.append(lines[i])
            i += 1
            continue

        # Blokk típus és prefix hossza (mivel már normalizáltuk WHERE    és ON )
        if m_where:
            head_ws = m_where.group("ws")
            head_prefix = "WHERE    "  # 9 chars
            head_rest = m_where.group("rest").lstrip()
            base_prefix_len = len(head_prefix)
            head_line_prefix = head_ws + head_prefix
        else:
            head_ws = m_on.group("ws")
            head_prefix = "ON "  # ON után legyen szóköz
            head_rest = m_on.group("rest").lstrip()
            base_prefix_len = len(head_prefix)
            head_line_prefix = head_ws + head_prefix

        # Gyűjtjük a blokk sorait: head + egymást követő AND/OR sorok
        block = []
        block.append(("HEAD", head_line_prefix, head_rest))
        i += 1

        while i < len(lines):
            m = rx_andor.match(lines[i])
            if not m:
                break
            kw = m.group("kw").upper()
            rest = m.group("rest").strip()
            block.append((kw, None, rest))
            i += 1

        # Meghatározzuk, hova igazodjon az AND/OR: a feltétel kezdőoszlopa alá
        cond_start_prefix = head_ws + (" " * base_prefix_len)

        # Operátor-oszlop igazításhoz: max LHS hossz a blokkban (mind HEAD, mind AND/OR)
        parsed = []
        max_lhs = 0

        for kind, prefix, rest in block:
            mo = rx_op.match(rest)
            if not mo:
                parsed.append((kind, prefix, None))
                continue

            lhs = mo.group("lhs").rstrip()
            op = re.sub(r"\s+", " ", mo.group("op").upper())
            rhs = mo.group("rhs").strip()
            max_lhs = max(max_lhs, len(lhs))
            parsed.append((kind, prefix, (lhs, op, rhs)))

        # Újraépítés
        for kind, prefix, parts in parsed:
            if parts is None:
                # nem tudtuk operátorra bontani, hagyjuk
                if kind == "HEAD":
                    out.append(head_line_prefix + block[0][2])
                else:
                    out.append(cond_start_prefix + kind + " " + (block[[b[0] for b in block].index(kind)][2]))
                continue

            lhs, op, rhs = parts

            if kind == "HEAD":
                out.append(f"{head_line_prefix}{lhs.ljust(max_lhs)} {op} {rhs}")
            else:
                out.append(f"{cond_start_prefix}{kind} {lhs.ljust(max_lhs)} {op} {rhs}")

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
