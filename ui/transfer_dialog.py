# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from docflow import latest as latestmod
from docflow import planner
from docflow import transfer as transfermod

from .crc_detail import CrcDetailDialog


class TransferDialog(tk.Toplevel):
    """Перенос новых версий из субподрядных папок в 4300_ПД (копируется вся
    папка версии). Сравнение — как в !LATEST."""

    def __init__(self, parent, cfg, entries, registry_path=""):
        super().__init__(parent)
        self.title("Перенос на сервер — сравнение с 4300_ПД")
        self.cfg = cfg
        self.entries = entries
        self.registry_path = registry_path
        self.rows = []
        self.row_data = {}
        self.geometry("960x620")
        self.transient(parent)
        self.grab_set()
        self._build()
        self.after(120, self._compare)

    def _build(self):
        self.banner = ttk.Label(self, padding=(8, 6), font=("Segoe UI", 10, "bold"),
                                text="Сравнение…")
        self.banner.pack(fill="x")
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="🔄 Сравнить", command=self._compare).pack(side="left")
        ttk.Button(top, text="Отметить все новые",
                   command=lambda: self._check_all(True)).pack(side="left", padx=4)
        ttk.Button(top, text="Снять все",
                   command=lambda: self._check_all(False)).pack(side="left")
        ttk.Button(top, text="🔒 Зафиксировать CRC",
                   command=self._fix_crc).pack(side="left", padx=(8, 2))
        ttk.Button(top, text="🔍 Проверить",
                   command=self._verify_crc).pack(side="left", padx=2)
        self.var_excel = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Записать версии в Excel (с бэкапом)",
                        variable=self.var_excel).pack(side="left", padx=12)
        ttk.Label(self, padding=(8, 0),
                  text="🔵 на сервере нет   🟠 у субподрядчика новее   "
                       "🟣 та же дата, но файлы отличаются (CRC32)   "
                       "🟢 не старее   ⚪ нет").pack(anchor="w")

        mid = ttk.Frame(self, padding=(8, 4))
        mid.pack(fill="both", expand=True)
        cols = ("chk", "srv", "sub", "src", "val", "st")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings")
        self.tree.heading("#0", text="Раздел / документ")
        self.tree.column("#0", width=360, stretch=True)
        for c, t, w in [("chk", "✓", 36), ("srv", "На сервере", 84),
                        ("sub", "Субподряд", 84), ("src", "Источник", 140),
                        ("val", "Проверка", 100), ("st", "Статус", 56)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center", stretch=(c == "src"))
        vsb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("need", foreground="#b9770e")
        self.tree.tag_configure("bad", foreground="#c0392b")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_double)

        self.lbl_issues = ttk.Label(self, padding=(8, 0), foreground="#c0392b",
                                    wraplength=920, justify="left", text="")
        self.lbl_issues.pack(fill="x")
        ttk.Label(self, padding=(8, 0), foreground="#555",
                  text="Двойной клик по документу — пофайловое сравнение папок "
                       "(CRC32)").pack(anchor="w")
        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        self.lbl = ttk.Label(bot, text="Сравнение…")
        self.lbl.pack(side="left")
        ttk.Button(bot, text="📥 Перенести отмеченные на сервер",
                   command=self._do).pack(side="right")

    def _on_select(self, event=None):
        sel = self.tree.selection()
        r = self.row_data.get(sel[0]) if sel else None
        if not r:
            self.lbl_issues.config(text="")
            return
        parts = []
        if r.get("issues"):
            parts.append("; ".join(r["issues"]))
        diff = r.get("diff")
        if diff:
            def _names(lst):
                return ", ".join(lst[:6]) + (f" …(+{len(lst) - 6})" if len(lst) > 6 else "")
            if diff["added"]:
                parts.append("новые: " + _names(diff["added"]))
            if diff["removed"]:
                parts.append("нет на субподряде: " + _names(diff["removed"]))
            if diff["changed"]:
                parts.append("изменены: " + _names(diff["changed"]))
        self.lbl_issues.config(
            text=("⚠ " + r["entry"].oboznachenie + ": " + " | ".join(parts)) if parts else "")

    def _on_double(self, event=None):
        sel = self.tree.selection()
        r = self.row_data.get(sel[0]) if sel else None
        if not r:
            return
        CrcDetailDialog(self, self.cfg, r["entry"], r.get("folder"), r.get("sub"))

    def _fix_crc(self):
        srcs = transfermod.external_sources(self.cfg)
        if not srcs:
            messagebox.showinfo("CRC", "Нет подключённых субподрядных папок.")
            return
        total = 0
        for s in srcs:
            total += transfermod.fix_crc(self.cfg, self.cfg.abspath(s.path))
        messagebox.showinfo("Контроль целостности",
                            f"Зафиксированы CRC32 файлов в субподрядных папках: {total}.\n"
                            f"Перезапись/подмена файлов будет видна по «Проверить».")

    def _verify_crc(self):
        srcs = transfermod.external_sources(self.cfg)
        problems = []
        for s in srcs:
            res = transfermod.verify_crc(self.cfg, self.cfg.abspath(s.path))
            if res and (res["changed"] or res["removed"] or res["added"]):
                problems.append((s.name, res))
        if not srcs:
            messagebox.showinfo("CRC", "Нет подключённых субподрядных папок.")
            return
        if not problems:
            messagebox.showinfo("Контроль целостности",
                                "✓ Субподрядные папки не менялись (CRC32 совпадают).")
            return
        txt = []
        for name, res in problems:
            txt.append(f"\n[{name}]  перезаписано {len(res['changed'])}, "
                       f"удалено {len(res['removed'])}, добавлено {len(res['added'])}")
            for x in res["changed"][:8]:
                txt.append("  ⚠ " + x)
        messagebox.showwarning("⚠ Целостность нарушена",
                               "Изменения относительно зафиксированных CRC32:\n" + "\n".join(txt))

    def _compare(self):
        self.lbl.config(text="Сравнение…")
        threading.Thread(target=self._compare_worker, daemon=True).start()

    def _compare_worker(self):
        try:
            rows = transfermod.compare(self.cfg, self.entries)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            self.after(0, lambda m=msg: messagebox.showerror("Ошибка сравнения", m))
            return
        self.after(0, lambda: self._fill(rows))

    def _fill(self, rows):
        from collections import defaultdict
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        self.row_data = {}
        by_sec = defaultdict(list)
        for r in rows:
            sec = planner._section_folder(r["entry"], self.cfg)
            is_ii = getattr(r["entry"], "area", "ПД") == "ИИ"
            by_sec[(1 if is_ii else 0, sec)].append(r)
        shown = (transfermod.NEW, transfermod.NEWER, transfermod.DIFF)
        need = 0
        ndiff = 0
        for area_sec in sorted(by_sec):
            is_ii, sec = area_sec
            if not any(r["status"] in shown for r in by_sec[area_sec]):
                continue
            title = f"📁 ИИ · {sec}" if is_ii else f"📁 {sec}"
            gid = self.tree.insert("", "end", text=title, open=True, values=("",) * 6)
            for r in by_sec[area_sec]:
                st = r["status"]
                if st not in shown:
                    continue
                r["_sel"] = False
                need += 1
                if st == transfermod.DIFF:
                    ndiff += 1
                issues = r.get("issues") or []
                val = "✓ ок" if not issues else f"⚠ {len(issues)}"
                tags = ("bad",) if (issues or st == transfermod.DIFF) else ("need",)
                iid = self.tree.insert(
                    gid, "end", text=r["entry"].oboznachenie,
                    values=("☐", r["server"] or "—", r["sub"] or "—",
                            r["source"] or "—", val, st), tags=tags)
                self.row_data[iid] = r
        nbad = sum(1 for r in self.rows if r["status"] in shown and r.get("issues"))
        self.lbl.config(text=f"Строк: {need}"
                             + (f" · с замечаниями: {nbad}" if nbad else "")
                             + " · отметьте нужные")
        if ndiff:
            self.banner.config(
                text=f"🟣 ВНИМАНИЕ: версий с той же датой, но изменённым составом "
                     f"файлов (CRC32): {ndiff} — проверьте, не подменили ли файлы!",
                foreground="#8e44ad")
        elif need:
            self.banner.config(
                text=f"⚠ Новые версии у субподрядчиков: {need} — отметьте и перенесите",
                foreground="#b9770e")
        else:
            self.banner.config(text="✓ Новых версий у субподрядчиков нет",
                               foreground="#1a7f1a")

    def _on_click(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        r = self.row_data.get(iid)
        if not r:
            return
        r["_sel"] = not r.get("_sel")
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "☑" if r["_sel"] else "☐"
        self.tree.item(iid, values=vals)

    def _check_all(self, value):
        for iid, r in self.row_data.items():
            r["_sel"] = value
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "☑" if value else "☐"
            self.tree.item(iid, values=vals)

    def _do(self):
        sel = [r for r in self.rows if r.get("_sel")]
        if not sel:
            messagebox.showinfo("Перенос", "Отметьте документы для переноса.")
            return
        bad = [r for r in sel if r.get("issues")]
        if bad:
            lst = "\n".join(f"• {r['entry'].oboznachenie}: {'; '.join(r['issues'])}"
                            for r in bad[:8])
            more = f"\n…и ещё {len(bad) - 8}" if len(bad) > 8 else ""
            if not messagebox.askyesno(
                    "Внимание: есть замечания",
                    f"У {len(bad)} из выбранных версий есть замечания по структуре/"
                    f"именам — лучше сначала исправить у субподрядчика:\n\n{lst}{more}\n\n"
                    "Всё равно перенести (включая версии с замечаниями)?",
                    icon="warning"):
                return
        if not messagebox.askyesno(
                "Перенос на сервер",
                f"Скопировать на сервер (в 4300_ПД) документов: {len(sel)}?\n"
                "Копируется вся папка версии целиком (через временный каталог).\n"
                "Если версия на сервере уже есть — она будет атомарно заменена "
                "(без слияния файлов). Другие даты на сервере не трогаем."):
            return
        try:
            done, skipped = transfermod.transfer(self.cfg, sel)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка переноса", str(e))
            return
        n_new = sum(1 for *_, how in done if how == "new")
        n_repl = sum(1 for *_, how in done if how == "replaced")
        msg = f"Перенесено версий: {len(done)}"
        parts = []
        if n_new:
            parts.append(f"новых {n_new}")
        if n_repl:
            parts.append(f"заменено {n_repl}")
        if parts:
            msg += " (" + ", ".join(parts) + ")"
        msg += "."
        if skipped:
            lines = "\n".join(f"• {oboz}_{date}: {why}" for oboz, date, why in skipped[:8])
            more = f"\n…и ещё {len(skipped) - 8}" if len(skipped) > 8 else ""
            msg += f"\n\nПропущено (ошибка переноса): {len(skipped)}\n{lines}{more}"
        # записать версии в Excel («Актуальная версия на дату»)
        if self.var_excel.get() and done and self.registry_path:
            updates = {oboz: date for oboz, date, _how in done}
            try:
                bak = (os.path.join(self.cfg.project_root, ".docflow_excel_backup.xlsx")
                       if self.cfg.project_root else None)
                n = latestmod.write_excel_versions(
                    self.registry_path, self.cfg.registry_sheet, updates,
                    smeta_sub=self.cfg.smeta_sub_codes, smeta_razdel=self.cfg.smeta_razdel,
                    backup_path=bak)
                msg += f"\nВ Excel записано версий: {n} (резервная копия в корне проекта)."
            except Exception as e:  # noqa: BLE001
                msg += f"\n⚠ Версии в Excel записать не удалось: {e}"
        messagebox.showinfo("Перенос на сервер",
                            msg + "\n\nЗапустите сканирование, чтобы навести порядок "
                            "(старые версии уйдут в !ARCHIVE).")
        self._compare()
