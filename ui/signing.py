# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from docflow import signing as signmod


class SigningDialog(tk.Toplevel):
    """Конвейер подписания: 1) собрать комплект актуальных файлов в плоскую
    папку → 2) после подписи добавить фамилию к .sig → 3) разложить .sig
    обратно в 4300_ПД/1100_ИИ."""

    def __init__(self, parent, cfg, entries):
        super().__init__(parent)
        self.cfg = cfg
        self.entries = entries
        self.title("Подписание комплекта")
        self.geometry("760x680")
        self.transient(parent)
        self.grab_set()
        # значения по умолчанию
        default_src = cfg.latest_abs if os.path.isdir(cfg.latest_abs) else cfg.target_pd_abs
        base = cfg.project_root or os.path.expanduser("~")
        default_sign = os.path.join(base, "8300_ВыпускПД", "!НА_ПОДПИСЬ")
        self.v_src = tk.StringVar(value=default_src)
        self.v_signfolder = tk.StringVar(value=default_sign)
        self.v_exts = tk.StringVar(value=", ".join(signmod.DEFAULT_SIGN_EXTS))
        self.v_surname = tk.StringVar(value="")
        self.v_sigfolder = tk.StringVar(value=default_sign)
        self.v_sig_target = tk.StringVar(value=cfg.target_pd_abs)
        self._folder_vars: dict[str, tk.BooleanVar] = {}
        self._folder_children: dict[str, list[str]] = {}
        self._build()
        self.v_src.trace_add("write", lambda *_: self._refresh_folders())
        self._refresh_folders()

    # ---- вспомогательное ----
    def _row(self, parent, r, label, var, cmd):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(parent, textvariable=var, width=54).grid(row=r, column=1, sticky="we",
                                                           padx=6, pady=3)
        ttk.Button(parent, text="…", width=3, command=cmd).grid(row=r, column=2, padx=2)
        parent.columnconfigure(1, weight=1)

    def _pick(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or self.cfg.project_root)
        if d:
            var.set(d)

    def _pick_src(self):
        self._pick(self.v_src)
        self._refresh_folders()

    def _log(self, text):
        self.out.configure(state="normal")
        self.out.insert("end", text + "\n")
        self.out.see("end")
        self.out.configure(state="disabled")

    def _build(self):
        pad = dict(padx=8, pady=4)
        # Шаг 1
        f1 = ttk.LabelFrame(self, text="Шаг 1. Собрать комплект на подпись (плоская папка)",
                            padding=6)
        f1.pack(fill="x", **pad)
        self._row(f1, 0, "Источник (комплект/сервер):", self.v_src, self._pick_src)
        self._row(f1, 1, "Куда собрать (папка на подпись):", self.v_signfolder,
                  lambda: self._pick(self.v_signfolder))
        ttk.Label(f1, text="Форматы:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(f1, textvariable=self.v_exts, width=24).grid(row=2, column=1, sticky="w",
                                                               padx=6, pady=3)

        # Выбор разделов и томов в отдельном окне
        ttk.Label(f1, text="Разделы и тома:").grid(row=3, column=0, sticky="w",
                                                    padx=6, pady=3)
        fold = ttk.Frame(f1)
        fold.grid(row=3, column=1, columnspan=2, sticky="w", padx=6, pady=3)
        ttk.Button(fold, text="Выбрать разделы и тома…",
                   command=self._open_folder_picker).pack(side="left")
        self.lbl_folders = ttk.Label(fold, foreground="#666", text="")
        self.lbl_folders.pack(side="left", padx=8)

        ttk.Label(f1, foreground="#666", wraplength=680, justify="left",
                  text="Можно выбрать весь комплект, отдельный раздел или конкретные тома. "
                       "Флажок родительской папки переключает всю вложенную ветку. "
                       "По умолчанию выбраны все; служебные папки и версии не показываются.").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=6)

        ttk.Button(f1, text="📦 Собрать актуальные файлы",
                   command=self._do_collect).grid(row=5, column=1, sticky="w", padx=6, pady=4)
        f1.columnconfigure(1, weight=1)

        # Шаг 2
        f2 = ttk.LabelFrame(self, text="Шаг 2. После подписания — добавить фамилию к .sig",
                            padding=6)
        f2.pack(fill="x", **pad)
        self._row(f2, 0, "Папка с подписями (.sig):", self.v_sigfolder,
                  lambda: self._pick(self.v_sigfolder))
        ttk.Label(f2, text="Фамилия подписавшего:").grid(row=1, column=0, sticky="w",
                                                         padx=6, pady=3)
        ttk.Entry(f2, textvariable=self.v_surname, width=24).grid(row=1, column=1, sticky="w",
                                                                  padx=6, pady=3)
        ttk.Button(f2, text="✍ Добавить фамилию к .sig",
                   command=self._do_surname).grid(row=2, column=1, sticky="w", padx=6, pady=4)
        ttk.Label(f2, foreground="#666", wraplength=680, justify="left",
                  text="Пример: 523-ПИР-24-КР1_Раздел ПД №4 Часть №1.pdf.sig  →  "
                       "…№1.pdf_Сидоров.sig. Несколько подписантов — запускайте по "
                       "очереди для каждой партии .sig.").grid(
                  row=3, column=0, columnspan=3, sticky="w", padx=6)

        # Шаг 3
        f3 = ttk.LabelFrame(self, text="Шаг 3. Разложить .sig (папка из шага 2)", padding=6)
        f3.pack(fill="x", **pad)
        ttk.Label(f3, foreground="#333",
                  text="Вариант А — наши подписи, автоматически по серверу:").grid(
                  row=0, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 0))
        ttk.Button(f3, text="📤 Разложить по документам (4300_ПД/1100_ИИ)",
                   command=self._do_distribute).grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(f3, foreground="#666",
                  text="Каждый .sig — в !PUBLISHED свежей версии своего документа.").grid(
                  row=2, column=0, columnspan=3, sticky="w", padx=6)
        ttk.Separator(f3, orient="horizontal").grid(row=3, column=0, columnspan=3,
                                                    sticky="we", pady=6)
        ttk.Label(f3, foreground="#333",
                  text="Вариант Б — подписи от субподрядчика, в указанную папку "
                       "рядом с файлом:").grid(row=4, column=0, columnspan=3,
                                               sticky="w", padx=6)
        self._row(f3, 5, "Папка назначения (субподряд):", self.v_sig_target,
                  lambda: self._pick(self.v_sig_target))
        ttk.Button(f3, text="📁 Разложить рядом с файлами (по имени)",
                   command=self._do_distribute_folder).grid(row=6, column=1, sticky="w",
                                                            padx=6, pady=2)
        ttk.Label(f3, foreground="#666",
                  text="Каждый .sig кладётся рядом с файлом того же имени (X.pdf.sig → "
                       "рядом с X.pdf), найденным в этой папке и подпапках.").grid(
                  row=7, column=0, columnspan=3, sticky="w", padx=6)

        # журнал
        lf = ttk.LabelFrame(self, text="Результат", padding=4)
        lf.pack(fill="both", expand=True, **pad)
        self.out = tk.Text(lf, height=8, wrap="word", state="disabled")
        self.out.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        ttk.Button(self, text="Закрыть", command=self.destroy).pack(side="right", padx=10, pady=6)

    def _set_all_folders(self, value: bool):
        for v in self._folder_vars.values():
            v.set(value)
        self._update_folder_summary()

    def _selected_folders(self) -> list[str]:
        # Родители служат для группового выбора. Сканируем только конечные
        # выбранные узлы, чтобы одна ветка не обходилась несколько раз.
        return [path for path, var in self._folder_vars.items()
                if var.get() and not self._folder_children.get(path)]

    def _folder_changed(self, path: str):
        value = self._folder_vars[path].get()

        def set_descendants(parent: str):
            for child in self._folder_children.get(parent, []):
                self._folder_vars[child].set(value)
                set_descendants(child)

        set_descendants(path)
        parent = os.path.dirname(path)
        while parent and parent in self._folder_vars:
            children = self._folder_children.get(parent, [])
            self._folder_vars[parent].set(
                any(self._folder_vars[child].get() for child in children))
            parent = os.path.dirname(parent)
        self._update_folder_summary()

    def _refresh_folders(self):
        self._folder_vars.clear()
        self._folder_children.clear()
        src = self.v_src.get().strip()
        nodes = signmod.list_signing_folder_tree(src, self.cfg) if src else []
        if not nodes:
            self.lbl_folders.configure(
                text=("будет собран весь корень"
                      if src and os.path.isdir(src)
                      else "укажите существующий источник"))
            return
        paths = {path for path, _depth in nodes}
        for path, _depth in nodes:
            parent = os.path.dirname(path)
            if parent in paths:
                self._folder_children.setdefault(parent, []).append(path)
            self._folder_children.setdefault(path, [])
        for path, _depth in nodes:
            var = tk.BooleanVar(value=True)  # по умолчанию все выбраны
            self._folder_vars[path] = var
        self._update_folder_summary()

    def _update_folder_summary(self):
        leaves = sum(not children for children in self._folder_children.values())
        selected = len(self._selected_folders())
        self.lbl_folders.configure(
            text=(f"выбрано конечных папок: {selected} из {leaves}"
                  if leaves else "нет доступных разделов и томов"))

    def _open_folder_picker(self):
        if not self._folder_vars:
            messagebox.showinfo(
                "Разделы и тома",
                "В выбранном источнике нет доступных вложенных папок.")
            return
        snapshot = {path: var.get() for path, var in self._folder_vars.items()}
        win = tk.Toplevel(self)
        win.title("Выбор разделов и томов для подписания")
        win.geometry("620x560")
        win.minsize(460, 360)
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win, text="Отметьте весь комплект, отдельные разделы или конкретные тома.",
            padding=(10, 10, 10, 4)).pack(anchor="w")
        toolbar = ttk.Frame(win, padding=(10, 4))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Выбрать все",
                   command=lambda: self._set_all_folders(True)).pack(side="left")
        ttk.Button(toolbar, text="Снять все",
                   command=lambda: self._set_all_folders(False)).pack(side="left", padx=4)

        outer = ttk.Frame(win, padding=(10, 4))
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>",
                  lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(body_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        depths: dict[str, int] = {}
        for path in self._folder_vars:
            parent = os.path.dirname(path)
            depths[path] = depths.get(parent, -1) + 1
            ttk.Checkbutton(
                body, text=("    " * depths[path]) + os.path.basename(path),
                variable=self._folder_vars[path],
                command=lambda p=path: self._folder_changed(p)).pack(anchor="w")

        def close(apply: bool):
            if not apply:
                for path, value in snapshot.items():
                    self._folder_vars[path].set(value)
                self._update_folder_summary()
            win.destroy()

        buttons = ttk.Frame(win, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Отмена",
                   command=lambda: close(False)).pack(side="right")
        ttk.Button(buttons, text="Применить",
                   command=lambda: close(True)).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", lambda: close(False))

    # ---- действия ----
    def _do_collect(self):
        src = self.v_src.get().strip()
        dst = self.v_signfolder.get().strip()
        if not os.path.isdir(src):
            messagebox.showwarning("Источник", "Папка-источник не найдена.")
            return
        exts = [x.strip() for x in re.split(r"[;,\s]+", self.v_exts.get()) if x.strip()]
        # Если список разделов есть — фильтруем по отмеченным; иначе весь корень.
        selected = None
        if self._folder_vars:
            selected = self._selected_folders()
            if not selected:
                messagebox.showwarning(
                    "Разделы",
                    "Не выбран ни один раздел. Отметьте нужные или нажмите «Выбрать все».")
                return
        try:
            copied, skipped = signmod.collect_for_signing(
                self.cfg, src, dst, exts, selected_folders=selected)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", str(e))
            return
        self.v_sigfolder.set(dst)
        sel_note = ""
        if selected is not None:
            sel_note = f", выбрано томов/веток: {len(selected)}"
        self._log(f"[Сбор] в «{dst}» скопировано файлов: {len(copied)}"
                  + (f", пропущено дублей: {skipped}" if skipped else "")
                  + sel_note)
        for n in copied[:40]:
            self._log("   • " + n)
        if len(copied) > 40:
            self._log(f"   … и ещё {len(copied) - 40}")
        if not copied:
            self._log("   (ничего не найдено — проверьте источник, разделы и форматы)")

    def _do_surname(self):
        folder = self.v_sigfolder.get().strip()
        surname = self.v_surname.get().strip()
        if not surname:
            messagebox.showwarning("Фамилия", "Укажите фамилию подписавшего.")
            return
        if not os.path.isdir(folder):
            messagebox.showwarning("Папка", "Папка с подписями не найдена.")
            return
        done, skipped = signmod.add_surname(folder, surname)
        self._log(f"[Фамилия «{surname}»] переименовано .sig: {len(done)}"
                  + (f", пропущено: {len(skipped)}" if skipped else ""))
        for old, new in done[:40]:
            self._log(f"   • {old}  →  {new}")
        if len(done) > 40:
            self._log(f"   … и ещё {len(done) - 40}")
        if not done:
            self._log("   (нет подходящих .sig — возможно, уже с фамилией)")

    def _do_distribute(self):
        folder = self.v_sigfolder.get().strip()
        if not os.path.isdir(folder):
            messagebox.showwarning("Папка", "Папка с подписями не найдена.")
            return
        res = signmod.distribute_sig(self.cfg, self.entries, folder)
        ok = [r for r in res if r["status"] == "ок"]
        bad = [r for r in res if r["status"] != "ок"]
        self._log(f"[Разложено] на сервер: {len(ok)}"
                  + (f", не удалось: {len(bad)}" if bad else ""))
        for r in ok[:40]:
            self._log(f"   ✓ {r['sig']}")
        for r in bad:
            self._log(f"   ⚠ {r['sig']} — {r['status']}")
        if not res:
            self._log("   (в папке нет .sig)")

    def _do_distribute_folder(self):
        folder = self.v_sigfolder.get().strip()
        target = self.v_sig_target.get().strip()
        if not os.path.isdir(folder):
            messagebox.showwarning("Папка", "Папка с подписями (шаг 2) не найдена.")
            return
        if not os.path.isdir(target):
            messagebox.showwarning("Папка назначения", "Папка назначения не найдена.")
            return
        res = signmod.distribute_by_name(self.cfg, folder, target)
        ok = [r for r in res if r["status"] in ("ок", "уже на месте")]
        bad = [r for r in res if r["status"] not in ("ок", "уже на месте")]
        self._log(f"[В папку «{os.path.basename(target.rstrip(os.sep))}»] разложено: "
                  f"{len(ok)}" + (f", не найдено/ошибок: {len(bad)}" if bad else ""))
        for r in ok[:40]:
            self._log(f"   ✓ {r['sig']}")
        for r in bad:
            self._log(f"   ⚠ {r['sig']} — {r['status']}")
        if not res:
            self._log("   (в папке нет .sig)")
