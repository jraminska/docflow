# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox

from docflow import transfer as transfermod


class CrcDetailDialog(tk.Toplevel):
    """Пофайловое сравнение папки субподрядчика с версией на сервере по CRC32.
    Показывает: какие файлы в каждой папке, их CRC32 и вердикт (совпадает /
    переписан / переименован / только у одной стороны)."""

    def __init__(self, parent, cfg, entry, sub_folder, sub_date):
        super().__init__(parent)
        self.cfg = cfg
        self.entry = entry
        self.title(f"Сравнение файлов — {entry.oboznachenie}")
        self.geometry("900x520")
        self.transient(parent)
        self.grab_set()
        try:
            self.detail = transfermod.doc_detail(cfg, entry, sub_folder, sub_date)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", str(e))
            self.destroy()
            return
        self._build()

    def _build(self):
        d = self.detail
        head = ttk.Frame(self, padding=8)
        head.pack(fill="x")
        vers = d["versions"]
        vtxt = ", ".join(f"{dt}{' (архив)' if arch else ''}" for dt, _f, arch in vers) \
            or "нет версий на сервере"
        ttk.Label(head, font=("Segoe UI", 10, "bold"),
                  text=d["oboznachenie"]).pack(anchor="w")
        ttk.Label(head, foreground="#333",
                  text=f"Версии на сервере: {vtxt}").pack(anchor="w")
        ttk.Label(head, foreground="#333",
                  text=f"Сравнение: субподряд {d['sub_date'] or '—'}  ↔  "
                       f"сервер {d['srv_date'] or '—'}").pack(anchor="w")

        mid = ttk.Frame(self, padding=(8, 4))
        mid.pack(fill="both", expand=True)
        cols = ("crc_sub", "crc_srv", "verdict")
        tree = ttk.Treeview(mid, columns=cols, show="tree headings")
        tree.heading("#0", text="Файл")
        tree.column("#0", width=300, stretch=True)
        for c, t, w in [("crc_sub", "CRC32 субподряд", 130),
                        ("crc_srv", "CRC32 сервер", 130),
                        ("verdict", "Вердикт", 300)]:
            tree.heading(c, text=t)
            tree.column(c, width=w, anchor="w", stretch=(c == "verdict"))
        vsb = ttk.Scrollbar(mid, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        tree.tag_configure("ok", foreground="#1e7a34")
        tree.tag_configure("warn", foreground="#b9770e")
        tree.tag_configure("bad", foreground="#c0392b")

        n_ok = n_bad = 0
        for row in d["rows"]:
            v = row["verdict"]
            if v == "совпадает":
                tag = "ok"; n_ok += 1
            elif v.startswith("переписан") or "только" in v:
                tag = "bad"; n_bad += 1
            else:
                tag = "warn"
            name = row["name"] or row.get("srv_name") or "—"
            tree.insert("", "end", text=name,
                        values=(transfermod._crc_hex(row["crc_sub"]),
                                transfermod._crc_hex(row["crc_srv"]), v), tags=(tag,))

        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        ttk.Label(bot, text=f"Совпадает: {n_ok}   Расхождений: {n_bad}").pack(side="left")
        ttk.Button(bot, text="Открыть папку субподряда",
                   command=lambda: self._open(d["sub_folder"])).pack(side="right", padx=4)
        ttk.Button(bot, text="Открыть папку сервера",
                   command=lambda: self._open(d["srv_folder"])).pack(side="right")

    def _open(self, path):
        if path and os.path.isdir(path):
            try:
                os.startfile(path)  # noqa: SLF001  (Windows)
            except Exception:  # noqa: BLE001
                messagebox.showinfo("Папка", path)
        else:
            messagebox.showinfo("Папка", "Папка недоступна.")
