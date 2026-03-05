import sys
import tkinter as tk
from tkinter import ttk, messagebox

from formatter import format_sql

APP_TITLE = "EBH SQL Formatter"


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

    def do_format():
        try:
            src = txt.get("1.0", tk.END)
            res = format_sql(src)
            show_output(res)
        except Exception as ex:
            messagebox.showerror("Hiba", str(ex))

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

    ttk.Button(btns, text="Formázás", command=do_format).pack(side=tk.LEFT)

    ttk.Label(btns, text="Tipp: Ctrl+V / Ctrl+A a szövegmezőben.", foreground="#666").pack(side=tk.RIGHT)

    root.mainloop()


if __name__ == "__main__":
    main()
