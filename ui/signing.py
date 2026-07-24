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
        self.geometry("760x560")
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
        self._build()

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
        self._row(f1, 0, "Источник (комплект/сервер):", self.v_src,
                  lambda: self._pick(self.v_src))
        self._row(f1, 1, "Куда собрать (папка на подпись):", self.v_signfolder,
                  lambda: self._pick(self.v_signfolder))
        ttk.Label(f1, text="Форматы:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(f1, textvariable=self.v_exts, width=24).grid(row=2, column=1, sticky="w",
                                                               padx=6, pady=3)
        ttk.Button(f1, text="📦 Собрать актуальные файлы",
                   command=self._do_collect).grid(row=3, column=1, sticky="w", padx=6, pady=4)

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

    # ---- действия ----
    def _do_collect(self):
        src = self.v_src.get().strip()
        dst = self.v_signfolder.get().strip()
        if not os.path.isdir(src):
            messagebox.showwarning("Источник", "Папка-источник не найдена.")
            return
        exts = [x.strip() for x in re.split(r"[;,\s]+", self.v_exts.get()) if x.strip()]
        try:
            copied, skipped = signmod.collect_for_signing(self.cfg, src, dst, exts)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", str(e))
            return
        self.v_sigfolder.set(dst)
        self._log(f"[Сбор] в «{dst}» скопировано файлов: {len(copied)}"
                  + (f", пропущено дублей: {skipped}" if skipped else ""))
        for n in copied[:40]:
            self._log("   • " + n)
        if len(copied) > 40:
            self._log(f"   … и ещё {len(copied) - 40}")
        if not copied:
            self._log("   (ничего не найдено — проверьте источник и форматы)")

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
