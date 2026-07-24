# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from docflow import latest as latestmod
from docflow import planner
from docflow import transfer as transfermod

from .extras import ExtrasDialog


class LatestDialog(tk.Toplevel):
    """Сравнение 4300_ПД и !LATEST + обновление комплекта и версий в Excel."""

    def __init__(self, parent, cfg, entries, registry_path):
        super().__init__(parent)
        self.title("Комплект !LATEST — сравнение с 4300_ПД")
        self.cfg = cfg
        self.entries = entries
        self.registry_path = registry_path
        self.rows = []
        self.row_data = {}
        self.geometry("920x620")
        self.transient(parent)
        self.grab_set()
        self._build()
        self.after(120, self._compare)

    def _build(self):
        # уведомление вверху: совпадает ли !LATEST с 4300_ПД
        self.banner = ttk.Label(self, padding=(8, 6), font=("Segoe UI", 10, "bold"),
                                text="Сравнение…")
        self.banner.pack(fill="x")

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="🔄 Сравнить", command=self._compare).pack(side="left")
        ttk.Button(top, text="Отметить все к обновлению",
                   command=lambda: self._check_all(True)).pack(side="left", padx=4)
        ttk.Button(top, text="Снять все",
                   command=lambda: self._check_all(False)).pack(side="left")
        ttk.Button(top, text="🧹 Лишнее в !LATEST",
                   command=self._show_extras).pack(side="left", padx=8)
        ttk.Button(top, text="🔒 Зафиксировать CRC",
                   command=self._fix_crc).pack(side="left", padx=2)
        ttk.Button(top, text="🔍 Проверить целостность",
                   command=self._verify_crc).pack(side="left", padx=2)
        self.var_excel = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Записать версии в Excel (с бэкапом)",
                        variable=self.var_excel).pack(side="left", padx=12)

        ttk.Label(self, padding=(8, 0),
                  text="🔴 нет в !LATEST или есть новее в 4300_ПД   🟢 актуально   "
                       "🟣 та же версия, но файлы отличаются (CRC32)   "
                       "⚪ нет версии в 4300_ПД").pack(anchor="w")

        mid = ttk.Frame(self, padding=(8, 4))
        mid.pack(fill="both", expand=True)
        cols = ("chk", "pd", "lat", "st")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings")
        self.tree.heading("#0", text="Раздел / документ")
        self.tree.column("#0", width=420, stretch=True)
        for c, t, w in [("chk", "✓", 36), ("pd", "4300_ПД", 90),
                        ("lat", "!LATEST", 90), ("st", "Статус", 60)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center", stretch=False)
        vsb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("need", foreground="#c0392b")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double)

        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        self.lbl = ttk.Label(bot, text="Сравнение…")
        self.lbl.pack(side="left")
        ttk.Button(bot, text="📦 Обновить отмеченные в !LATEST",
                   command=self._update).pack(side="right")

    def _show_extras(self):
        extras = latestmod.latest_extras(self.cfg, self.entries)
        if not extras:
            messagebox.showinfo("!LATEST", "Лишнего в !LATEST не найдено.")
            return
        ExtrasDialog(self, extras, on_done=self._compare)

    def _fix_crc(self):
        n = transfermod.fix_crc(self.cfg, self.cfg.latest_abs)
        messagebox.showinfo("Контроль целостности",
                            f"Зафиксированы контрольные суммы (CRC32) файлов в "
                            f"!LATEST: {n}.\n\nТеперь любая перезапись файла будет "
                            f"видна по кнопке «Проверить целостность».")

    def _verify_crc(self):
        res = transfermod.verify_crc(self.cfg, self.cfg.latest_abs)
        if res is None:
            messagebox.showinfo("Контроль целостности",
                                "Контрольные суммы ещё не зафиксированы. "
                                "Нажмите «🔒 Зафиксировать CRC».")
            return
        ch, rm, ad = res["changed"], res["removed"], res["added"]
        if not (ch or rm or ad):
            messagebox.showinfo("Контроль целостности",
                                "✓ !LATEST не менялся — все файлы совпадают с "
                                "зафиксированными CRC32.")
            return

        def _lst(t, lst):
            return f"\n{t} ({len(lst)}):\n" + "\n".join("  • " + x for x in lst[:15]) + \
                   (f"\n  …и ещё {len(lst) - 15}" if len(lst) > 15 else "") if lst else ""
        messagebox.showwarning(
            "⚠ Целостность нарушена",
            "В !LATEST изменились файлы относительно зафиксированных CRC32:"
            + _lst("ПЕРЕЗАПИСАНЫ (другое содержимое)", ch)
            + _lst("УДАЛЕНЫ", rm) + _lst("ДОБАВЛЕНЫ", ad))

    def _compare(self):
        self.lbl.config(text="Сравнение…")
        threading.Thread(target=self._compare_worker, daemon=True).start()

    def _compare_worker(self):
        try:
            rows = latestmod.compare(self.cfg, self.entries)
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
            # ИИ показываем отдельным блоком, после разделов ПД
            is_ii = getattr(r["entry"], "area", "ПД") == "ИИ"
            by_sec[(1 if is_ii else 0, sec)].append(r)
        need = 0
        for area_sec in sorted(by_sec):
            is_ii, sec = area_sec
            title = f"📁 ИИ · {sec}" if is_ii else f"📁 {sec}"
            gid = self.tree.insert("", "end", text=title, open=True,
                                   values=("", "", "", ""))
            for r in by_sec[area_sec]:
                st = r["status"]
                needs = st in (latestmod.NEWER, latestmod.ABSENT, latestmod.DIFF)
                r["_sel"] = False          # по умолчанию НИЧЕГО не отмечено — точечно
                need += 1 if needs else 0
                chk = "☐" if needs else ""   # отмечать можно только то, что требует обновления
                iid = self.tree.insert(gid, "end", text=r["entry"].oboznachenie,
                                       values=(chk, r["pd"] or "—", r["latest"] or "—", st),
                                       tags=("need",) if needs else ())
                self.row_data[iid] = r
        self.lbl.config(text=f"Документов: {len(rows)} · отметьте нужные и нажмите «Обновить»")
        if need:
            self.banner.config(
                text=f"⚠ Структура !LATEST отличается от 4300_ПД — "
                     f"документов к обновлению: {need}", foreground="#c0392b")
        else:
            self.banner.config(text="✓ !LATEST совпадает с 4300_ПД — всё актуально",
                               foreground="#1a7f1a")

    def _on_click(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        r = self.row_data.get(iid)
        if not r or r["status"] in (latestmod.OK, latestmod.NO_PD):
            return
        r["_sel"] = not r.get("_sel")
        vals = list(self.tree.item(iid, "values"))
        vals[0] = "☑" if r["_sel"] else "☐"
        self.tree.item(iid, values=vals)

    def _on_double(self, event):
        iid = self.tree.identify_row(event.y)
        r = self.row_data.get(iid)
        if not r or r["status"] != latestmod.DIFF or not r.get("diff"):
            return
        d = r["diff"]

        def _lst(t, lst):
            return f"\n{t} ({len(lst)}):\n" + "\n".join("  • " + x for x in lst[:15]) + \
                   (f"\n  …и ещё {len(lst) - 15}" if len(lst) > 15 else "") if lst else ""
        messagebox.showwarning(
            f"🟣 {r['entry'].oboznachenie} — та же версия ({r['latest']}), но файлы отличаются",
            "Содержимое в 4300_ПД отличается от того, что уже лежит в !LATEST:"
            + _lst("ИЗМЕНЕНЫ (другой CRC32)", d.get("changed") or [])
            + _lst("ЕСТЬ ТОЛЬКО В 4300_ПД", d.get("added") or [])
            + _lst("ЕСТЬ ТОЛЬКО В !LATEST", d.get("removed") or [])
            + "\n\nЧтобы исправить — отметьте документ галочкой и нажмите «Обновить».")

    def _check_all(self, value):
        for iid, r in self.row_data.items():
            if r["status"] in (latestmod.OK, latestmod.NO_PD):
                continue
            r["_sel"] = value
            vals = list(self.tree.item(iid, "values"))
            vals[0] = "☑" if value else "☐"
            self.tree.item(iid, values=vals)

    def _update(self):
        sel = [r for r in self.rows if r.get("_sel")]
        if not sel:
            messagebox.showinfo("!LATEST", "Отметьте документы для обновления.")
            return
        extra = "\nИ записать версии в Excel (будет создан .bak)." if self.var_excel.get() else ""
        if not messagebox.askyesno("!LATEST",
                                   f"Скопировать в !LATEST документов: {len(sel)}?{extra}"):
            return
        try:
            upd = latestmod.update_latest(self.cfg, sel)
            msg = f"В !LATEST обновлено документов: {len(upd)}."
            if self.var_excel.get():
                # пишем версии ВСЕХ документов, что лежат в !LATEST (по манифесту),
                # а не только перенесённых сейчас — чтобы в Excel были и старые
                man = latestmod.load_manifest(self.cfg)
                by_key = {e.key: e for e in self.entries}
                full = {by_key[k].oboznachenie: d for k, d in man.items()
                        if k in by_key and d}
                bak = os.path.join(self.cfg.project_root or ".",
                                   ".docflow_excel_backup.xlsx") if self.cfg.project_root else None
                n = latestmod.write_excel_versions(
                    self.registry_path, self.cfg.registry_sheet, full,
                    smeta_sub=self.cfg.smeta_sub_codes, smeta_razdel=self.cfg.smeta_razdel,
                    backup_path=bak)
                msg += (f"\nВ Excel записаны версии всех документов из !LATEST: {n} "
                        f"(резервная копия в корне проекта).")
            # перефиксируем CRC32 !LATEST (новое утверждённое состояние)
            cnt = transfermod.fix_crc(self.cfg, self.cfg.latest_abs)
            msg += f"\nЗафиксированы CRC32 файлов в !LATEST: {cnt}."
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка обновления", str(e))
            return
        messagebox.showinfo("!LATEST", msg)
        self._compare()
