# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import ttk, filedialog

from docflow import config as cfgmod
from docflow import registry as regmod

from .checks import ChecksDialog
from .source_editor import SourceEditor


class ProjectDialog(tk.Toplevel):
    """Окно настроек одного проекта (объекта): пути, источники, правила, состав."""
    def __init__(self, parent, app, cfg: cfgmod.AppConfig, on_save):
        super().__init__(parent)
        name = os.path.basename((cfg.project_root or "").rstrip("/\\")) or "новый проект"
        self.title(f"Настройки проекта — {name}")
        self.app = app
        self.cfg = cfg
        self.on_save = on_save
        self.geometry("820x680")
        self.transient(parent)
        self.grab_set()
        self._build()

    def _build(self):
        pad = {"padx": 6, "pady": 4}
        # кнопки сохранения — закреплены внизу окна (всегда видны)
        savebar = ttk.Frame(self, padding=8)
        savebar.pack(side="bottom", fill="x")
        ttk.Button(savebar, text="Сохранить", command=self._save).pack(side="right")
        ttk.Button(savebar, text="Закрыть", command=self.destroy).pack(side="right", padx=6)
        # остальное — в прокручиваемой области (чтобы ничего не обрезалось)
        canvas = tk.Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        frm = ttk.Frame(canvas, padding=10)
        win = canvas.create_window((0, 0), window=frm, anchor="nw")
        frm.bind("<Configure>",
                 lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind("<Enter>", lambda e: canvas.bind_all(
            "<MouseWheel>", lambda ev: canvas.yview_scroll(int(-ev.delta / 120), "units")))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # пути
        self.v_root = tk.StringVar(value=self.cfg.project_root)
        self.v_reg = tk.StringVar(value=self.cfg.registry_path)
        self.v_sheet = tk.StringVar(value=self.cfg.registry_sheet)
        self.v_latest = tk.StringVar(value=self.cfg.latest_dir)
        self.v_pd = tk.StringVar(value=self.cfg.target_pd)
        self.v_ii = tk.StringVar(value=self.cfg.ii_root)
        self.v_edit = tk.StringVar(value=self.cfg.edit_dir)
        self.v_pub = tk.StringVar(value=self.cfg.published_dir)
        self.v_latest_layout = tk.StringVar(
            value=getattr(self.cfg, "latest_layout", "{area}/{section}"))

        self._row_path(frm, 0, "Корень проекта:", self.v_root, self._pick_dir)
        self._row_path(frm, 1, "Файл реестра (xlsx):", self.v_reg, self._pick_file)
        ttk.Label(frm, text="Лист реестра:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.v_sheet, width=20).grid(row=2, column=1, sticky="w", **pad)

        # папки документации — у каждого проекта свои (можно относительные или полные)
        ff = ttk.LabelFrame(frm, text="Папки документации (относительно корня проекта "
                                      "или полный путь)", padding=6)
        ff.grid(row=3, column=0, columnspan=3, sticky="we", **pad)
        ff.columnconfigure(1, weight=1)
        ttk.Label(ff, text="Проектная документация (ПД):").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.v_pd, width=46).grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(ff, text="Изыскания — папка отчётов (ИИ):").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.v_ii, width=46).grid(row=1, column=1, sticky="we", **pad)
        ttk.Label(ff, text="Комплект !LATEST:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.v_latest, width=46).grid(row=2, column=1, sticky="we", **pad)
        ttk.Label(ff, foreground="#666", text="Напр.: ПД = 4300_ПД · ИИ = 1100_ИИ\\04_Отчеты "
                  "(там лежат 01_ИГДИ, 02_ИГИ…)").grid(row=3, column=0, columnspan=2,
                                                       sticky="w", padx=6)
        ttk.Label(ff, text="Вложенность в !LATEST:").grid(row=7, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.v_latest_layout, width=30).grid(
            row=7, column=1, sticky="w", **pad)
        ttk.Label(ff, foreground="#666", wraplength=520, justify="left",
                  text="Как раскладывать комплект. Плейсхолдеры: {area} (02_ПД/01_ИИ), "
                       "{section} (01_ПЗ…), {razdel}, {short}, {oboznachenie}. Внутри "
                       "всегда каталог версии {обозначение}_{дата}. "
                       "Примеры: {area}/{section}  ·  {section}  ·  {area}  ·  пусто "
                       "(всё в один уровень).").grid(
                  row=8, column=0, columnspan=2, sticky="w", padx=6)
        # имена обязательных подпапок каталога версии (по умолчанию для всех томов)
        ttk.Label(ff, text="Папка ред. форматов:").grid(row=4, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.v_edit, width=20).grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(ff, text="Папка публикуемых (→ !LATEST):").grid(row=5, column=0, sticky="w", **pad)
        ttk.Entry(ff, textvariable=self.v_pub, width=20).grid(row=5, column=1, sticky="w", **pad)
        ttk.Label(ff, foreground="#666", wraplength=520, justify="left",
                  text="По умолчанию !EDIT / !PUBLISHED. Можно задать свои (напр. РЕД / "
                       "НЕРЕД). Имя для отдельного тома меняется в project_structure.json "
                       "(поля edit_dir, pub_dir, split).").grid(
                  row=6, column=0, columnspan=2, sticky="w", padx=6)

        # источники (субподряд) — сервер ПД сканируется автоматически по «Папке ПД»
        srcf = ttk.LabelFrame(frm, text="Папки субподрядчиков (сервер ПД "
                                       "сканируется автоматически — добавлять не нужно)",
                              padding=6)
        srcf.grid(row=4, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(4, weight=1)
        frm.columnconfigure(1, weight=1)

        self.lst = tk.Listbox(srcf, height=8)
        self.lst.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(srcf, command=self.lst.yview)
        sb.pack(side="left", fill="y")
        self.lst.config(yscrollcommand=sb.set)
        self._reload_sources()

        btns = ttk.Frame(srcf)
        btns.pack(side="left", fill="y", padx=6)
        ttk.Button(btns, text="Добавить…", command=self._add_src).pack(fill="x", pady=2)
        ttk.Button(btns, text="Изменить…", command=self._edit_src).pack(fill="x", pady=2)
        ttk.Button(btns, text="Вкл/выкл", command=self._toggle_src).pack(fill="x", pady=2)
        ttk.Button(btns, text="Удалить", command=self._del_src).pack(fill="x", pady=2)

        # опции
        self.v_hash = tk.BooleanVar(value=self.cfg.use_hash)
        self.v_mtime = tk.BooleanVar(value=self.cfg.date_from_mtime_if_absent)
        self.v_exclude = tk.StringVar(value="; ".join(self.cfg.exclude_folders))
        opt = ttk.Frame(frm)
        opt.grid(row=5, column=0, columnspan=3, sticky="we", **pad)
        ttk.Checkbutton(opt, text="Сверять содержимое по хэшу (точнее, но медленнее)",
                        variable=self.v_hash).pack(anchor="w")
        ttk.Checkbutton(opt, text="Если в имени нет даты — брать дату изменения файла",
                        variable=self.v_mtime).pack(anchor="w")
        exf = ttk.Frame(opt)
        exf.pack(anchor="w", fill="x", pady=(4, 0))
        ttk.Label(exf, text="Исключить из проверки папки (через «;»):").pack(side="left")
        ttk.Entry(exf, textvariable=self.v_exclude, width=44).pack(side="left", padx=4)

        # форма шифра объекта и рабочие папки — настраиваемые правила
        self.v_shifr = tk.StringVar(value=self.cfg.shifr_pattern)
        self.v_work = tk.StringVar(value="; ".join(self.cfg.work_prefixes))
        shf = ttk.Frame(opt)
        shf.pack(anchor="w", fill="x", pady=(4, 0))
        ttk.Label(shf, text="Шаблон шифра объекта (рег. выражение):").pack(side="left")
        ttk.Entry(shf, textvariable=self.v_shifr, width=34).pack(side="left", padx=4)
        wkf = ttk.Frame(opt)
        wkf.pack(anchor="w", fill="x", pady=(2, 0))
        ttk.Label(wkf, text="Нестандартные (рабочие) папки, префиксы (через «;»):").pack(side="left")
        ttk.Entry(wkf, textvariable=self.v_work, width=30).pack(side="left", padx=4)

        # типы проверок — в отдельном окне (их много, чтобы не растягивать настройки)
        chk = ttk.Frame(frm)
        chk.grid(row=7, column=0, columnspan=3, sticky="we", **pad)
        ttk.Button(chk, text="⚙ Настройка типов проверок…",
                   command=self._open_checks).pack(side="left")
        ttk.Label(chk, foreground="#666",
                  text="— что прогонять и что считать ошибкой / замечанием"
                  ).pack(side="left", padx=8)

        # состав проекта (разовая операция)
        comp = ttk.LabelFrame(frm, text="Состав проекта (формируется редко)", padding=6)
        comp.grid(row=6, column=0, columnspan=3, sticky="we", **pad)
        ttk.Label(comp, wraplength=720, justify="left",
                  text="1) «Сформировать состав из Excel» — прочитать "
                       "…Наименование файлов.xlsx и создать редактируемый "
                       "project_structure.json.\n"
                       "2) «Создать папки» — построить структуру разделов и "
                       "документов с !ARCHIVE по составу.").pack(anchor="w", pady=(0, 4))
        cbtns = ttk.Frame(comp)
        cbtns.pack(anchor="w")
        ttk.Button(cbtns, text="📋 Сформировать состав из Excel",
                   command=self._do_composition).pack(side="left")
        ttk.Button(cbtns, text="📁 Создать папки",
                   command=self._do_folders).pack(side="left", padx=6)

    def _do_composition(self):
        self._apply_fields()
        cfgmod.save(self.cfg)
        self.app._save_project_settings()
        self.app.do_build_composition()

    def _do_folders(self):
        self._apply_fields()
        cfgmod.save(self.cfg)
        self.app._save_project_settings()
        self.app.do_create_folders()

    def _apply_fields(self):
        root = self.v_root.get().strip()
        self.cfg.project_root = os.path.normpath(root) if root else ""
        self.cfg.registry_path = self.v_reg.get().strip()
        self.cfg.registry_sheet = self.v_sheet.get().strip() or "ПИР"
        self.cfg.latest_dir = self.v_latest.get().strip()
        self.cfg.latest_layout = self.v_latest_layout.get().strip()
        self.cfg.target_pd = self.v_pd.get().strip() or "4300_ПД"
        self.cfg.ii_root = self.v_ii.get().strip() or "1100_ИИ/04_Отчеты"
        self.cfg.edit_dir = self.v_edit.get().strip() or "!EDIT"
        self.cfg.published_dir = self.v_pub.get().strip() or "!PUBLISHED"
        self.cfg.use_hash = self.v_hash.get()
        self.cfg.date_from_mtime_if_absent = self.v_mtime.get()
        self.cfg.exclude_folders = [x.strip() for x in re.split(r"[;,]", self.v_exclude.get())
                                    if x.strip()]
        self.cfg.shifr_pattern = self.v_shifr.get().strip() or cfgmod.AppConfig().shifr_pattern
        self.cfg.work_prefixes = [x.strip() for x in re.split(r"[;,]", self.v_work.get())
                                  if x.strip()]
        regmod.configure_shifr(self.cfg.shifr_pattern)   # применить форму шифра сразу
        # checks / check_levels настраиваются в отдельном окне (пишутся прямо в cfg)

    def _open_checks(self):
        ChecksDialog(self, self.cfg)

    def _row_path(self, frm, r, label, var, cmd):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(frm, textvariable=var, width=60).grid(row=r, column=1, sticky="we", padx=6, pady=4)
        ttk.Button(frm, text="Обзор…", command=lambda: cmd(var)).grid(row=r, column=2, padx=6)

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or os.getcwd())
        if d:
            var.set(d)

    def _pick_file(self, var):
        f = filedialog.askopenfilename(initialdir=os.path.dirname(var.get()) or os.getcwd(),
                                       filetypes=[("Excel", "*.xlsx;*.xlsm"), ("Все", "*.*")])
        if f:
            var.set(f)

    def _reload_sources(self):
        self.lst.delete(0, "end")
        for s in self.cfg.sources:
            mark = "✓" if s.enabled else "✗"
            self.lst.insert("end", f"[{mark}] {s.category}  {s.name}  —  {s.path}")

    def _add_src(self):
        d = filedialog.askdirectory(title="Выберите папку субподрядчика")
        if not d:
            return
        name = os.path.basename(d.rstrip("/\\")) or d
        cat = "ИИ" if "ИИ" in d or "1100" in d else "ПД"
        src = cfgmod.Source(name=name, path=d, category=cat)
        self.cfg.sources.append(src)
        self._reload_sources()
        # сразу даём назвать организацию (имя показывается в дереве: «Субподряд: …»)
        SourceEditor(self, src, on_ok=self._reload_sources,
                     title="Субподрядчик — укажите название")

    def _edit_src(self):
        i = self._sel_index()
        if i is None:
            return
        SourceEditor(self, self.cfg.sources[i], on_ok=self._reload_sources)

    def _toggle_src(self):
        i = self._sel_index()
        if i is None:
            return
        self.cfg.sources[i].enabled = not self.cfg.sources[i].enabled
        self._reload_sources()

    def _del_src(self):
        i = self._sel_index()
        if i is None:
            return
        del self.cfg.sources[i]
        self._reload_sources()

    def _sel_index(self):
        sel = self.lst.curselection()
        return sel[0] if sel else None

    def _save(self):
        self._apply_fields()
        self.on_save()
        self.destroy()
