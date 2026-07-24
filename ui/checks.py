# -*- coding: utf-8 -*-
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from docflow import config as cfgmod


class ChecksDialog(tk.Toplevel):
    """Отдельное окно «Типы проверок»: этапы, галочки, значимость (Ошибка/Замечание).
    Пишет результат прямо в cfg.checks / cfg.check_levels по кнопке OK."""
    def __init__(self, parent, cfg: cfgmod.AppConfig):
        super().__init__(parent)
        self.title("Типы проверок")
        self.cfg = cfg
        self.geometry("580x600")
        self.transient(parent)
        self.grab_set()
        self._build()

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Отметьте проверки и задайте значимость. "
                  "Выключенная проверка не создаёт замечаний.",
                  wraplength=540, foreground="#555", justify="left").pack(anchor="w")
        bar = ttk.Frame(top)
        bar.pack(anchor="w", pady=(6, 0))
        ttk.Button(bar, text="Отметить все", command=lambda: self._all(True)).pack(side="left")
        ttk.Button(bar, text="Снять все", command=lambda: self._all(False)).pack(side="left", padx=4)

        # прокручиваемая область со списком проверок
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=8)
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # колесо мыши работает, пока курсор над окном; снимаем при уходе/закрытии
        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        self.v_checks, self.v_levels, self._grp_vars = {}, {}, []
        cur = self.cfg.checks or {}
        lvls = self.cfg.check_levels or {}
        for title, ids in cfgmod.CHECK_GROUPS:
            gvar = tk.BooleanVar(value=all(cur.get(i, True) for i in ids))
            self._grp_vars.append((gvar, ids))
            ttk.Checkbutton(body, text=title, variable=gvar,
                            command=lambda i=ids, g=gvar: self._toggle_group(i, g)
                            ).pack(anchor="w", pady=(8, 2))
            for cid in ids:
                rf = ttk.Frame(body)
                rf.pack(anchor="w", fill="x", padx=20)
                v = tk.BooleanVar(value=cur.get(cid, True))
                self.v_checks[cid] = v
                ttk.Checkbutton(rf, text=cfgmod.CHECKS[cid], variable=v, width=46
                                ).pack(side="left", anchor="w")
                lv = tk.StringVar(value="Ошибка" if lvls.get(
                    cid, cfgmod.DEFAULT_CHECK_LEVELS.get(cid, "warn")) == "error"
                    else "Замечание")
                self.v_levels[cid] = lv
                ttk.Combobox(rf, textvariable=lv, values=["Ошибка", "Замечание"],
                             width=11, state="readonly").pack(side="left", padx=4)

        btns = ttk.Frame(self, padding=8)
        btns.pack(fill="x")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")
        ttk.Button(btns, text="Отмена", command=self.destroy).pack(side="right", padx=6)

    def _toggle_group(self, ids, gvar):
        for cid in ids:
            if cid in self.v_checks:
                self.v_checks[cid].set(gvar.get())

    def _all(self, value):
        for v in self.v_checks.values():
            v.set(value)
        for gv, _ids in self._grp_vars:
            gv.set(value)

    def _ok(self):
        self.cfg.checks = {cid: var.get() for cid, var in self.v_checks.items()}
        self.cfg.check_levels = {
            cid: ("error" if self.v_levels[cid].get() == "Ошибка" else "warn")
            for cid in self.v_checks}
        self.destroy()
