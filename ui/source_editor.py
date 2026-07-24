# -*- coding: utf-8 -*-
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from docflow import config as cfgmod


class SourceEditor(tk.Toplevel):
    def __init__(self, parent, src: cfgmod.Source, on_ok, title="Папка субподрядчика"):
        super().__init__(parent)
        self.title(title)
        self.src = src
        self.on_ok = on_ok
        self.transient(parent)
        self.grab_set()
        self.v_name = tk.StringVar(value=src.name)
        self.v_path = tk.StringVar(value=src.path)
        self.v_cat = tk.StringVar(value=src.category)
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Название организации:").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.v_name, width=40).grid(row=0, column=1, sticky="we", pady=3)
        ttk.Label(frm, text="Путь:").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.v_path, width=40).grid(row=1, column=1, sticky="we", pady=3)
        ttk.Label(frm, text="Категория:").grid(row=2, column=0, sticky="w")
        ttk.Combobox(frm, textvariable=self.v_cat, values=["ПД", "ИИ"], width=8,
                     state="readonly").grid(row=2, column=1, sticky="w", pady=3)
        ttk.Button(frm, text="OK", command=self._ok).grid(row=3, column=1, sticky="e", pady=6)

    def _ok(self):
        self.src.name = self.v_name.get().strip()
        self.src.path = self.v_path.get().strip()
        self.src.category = self.v_cat.get()
        self.on_ok()
        self.destroy()
