# -*- coding: utf-8 -*-
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from docflow import latest as latestmod


class ExtrasDialog(tk.Toplevel):
    """Лишнее в !LATEST — отметить и удалить."""

    def __init__(self, parent, extras, on_done=None):
        super().__init__(parent)
        self.title("Лишнее в !LATEST")
        self.extras = extras
        self.on_done = on_done
        self.row_x = {}
        self.geometry("760x460")
        self.transient(parent)
        self.grab_set()
        ttk.Label(self, padding=8, font=("Segoe UI", 10, "bold"),
                  text=f"Найдено лишнего: {len(extras)}. Отметьте, что удалить.").pack(fill="x")
        mid = ttk.Frame(self, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=("chk", "what"), show="headings")
        self.tree.heading("chk", text="✓")
        self.tree.heading("what", text="Что удалить")
        self.tree.column("chk", width=36, anchor="center", stretch=False)
        self.tree.column("what", width=680, anchor="w")
        vsb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        for x in extras:
            iid = self.tree.insert("", "end", values=("☑", x["reason"]))
            self.row_x[iid] = {"x": x, "sel": True}
        self.tree.bind("<Button-1>", self._click)
        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        ttk.Button(bot, text="Снять все",
                   command=lambda: self._all(False)).pack(side="left")
        ttk.Button(bot, text="Отметить все",
                   command=lambda: self._all(True)).pack(side="left", padx=4)
        ttk.Button(bot, text="🗑 Удалить отмеченные",
                   command=self._delete).pack(side="right")

    def _click(self, e):
        if self.tree.identify_column(e.x) != "#1":
            return
        iid = self.tree.identify_row(e.y)
        if iid in self.row_x:
            d = self.row_x[iid]
            d["sel"] = not d["sel"]
            v = list(self.tree.item(iid, "values"))
            v[0] = "☑" if d["sel"] else "☐"
            self.tree.item(iid, values=v)

    def _all(self, val):
        for iid, d in self.row_x.items():
            d["sel"] = val
            v = list(self.tree.item(iid, "values"))
            v[0] = "☑" if val else "☐"
            self.tree.item(iid, values=v)

    def _delete(self):
        paths = [d["x"]["path"] for d in self.row_x.values() if d["sel"]]
        if not paths:
            return
        if not messagebox.askyesno("Удаление",
                                   f"Удалить из !LATEST объектов: {len(paths)}?"):
            return
        n = latestmod.remove_paths(paths)
        messagebox.showinfo("!LATEST", f"Удалено: {n}.")
        if self.on_done:
            self.on_done()
        self.destroy()
