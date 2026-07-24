# -*- coding: utf-8 -*-
"""DocFlow — приложение для приведения в порядок и комплектования
проектной документации.

Запуск:  python app.py   (или собранный DocFlow.exe)

Окно на русском, рассчитано на помощников без навыков программирования.
Никаких сетевых отправок — программа работает только с локальными
и сетевыми папками, указанными в настройках.
"""
from __future__ import annotations

import os
import re
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from docflow import config as cfgmod
from docflow import registry as regmod
from docflow import scanner, planner, executor, structure as structmod
from docflow import latest as latestmod
from docflow import transfer as transfermod
from docflow import signing as signmod
from docflow import dedup as dedupmod
from docflow.naming import Matcher
from docflow import __version__, __build_date__

APP_TITLE = "DocFlow — комплектование проектной документации"
CHECK_ON, CHECK_OFF = "☑", "☐"

KIND_ORDER = planner.KIND_ORDER

# статусы разделов
DOT_OK = "🟢"        # всё в порядке
DOT_TODO = "🟠"      # есть действия для наведения порядка
DOT_ATTENTION = "🟡" # требует внимания (не распознано)
DOT_UNKNOWN = "⚪"   # ещё не сканировали

# тег цвета текста узла по статусу (эмодзи в ttk не цветные, красим текст)
STATUS_TAG = {DOT_OK: "st_ok", DOT_TODO: "st_todo",
              DOT_ATTENTION: "st_att", DOT_UNKNOWN: "st_unk"}

# индикатор у строки действия
KIND_DOT = {
    planner.MKSTRUCT: "🔴", planner.TRANSFER: "🔴", planner.MKCATALOG: "🟠",
    planner.ARCHIVE_FOLDER: "🟠", planner.RENAME: "🟠", planner.MOVE: "🟠",
    planner.ARCHIVE: "🟠", planner.COPY_LATEST: "🔵", planner.FLAG: "🟡",
}


def _app_dir() -> str:
    return os.path.dirname(os.path.abspath(__import__("sys").argv[0])) or os.getcwd()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE}  —  v{__version__}")
        self.geometry("1180x720")
        self.minsize(900, 560)

        self.cfg = cfgmod.load()
        regmod.configure_shifr(self.cfg.shifr_pattern)   # форма шифра объекта
        self.actions: list[planner.Action] = []
        self.inventory: list = []                     # просканированные файлы (FileRec)
        self.structure: dict | None = None            # состав проекта (из json)
        self.entries: list = []                       # документы состава (RegEntry)
        self.row_action: dict[str, planner.Action] = {}
        self.node_actions: dict[str, list] = {}       # узел структуры → его СОБСТВЕННЫЕ действия
        self.node_sub: dict[str, list] = {}           # узел → действия всего поддерева
        self.node_path: dict[str, str] = {}           # узел → путь папки (для «Открыть»)
        self.cur_actions: list = []                   # действия выбранного узла
        self.all_groups: set = set()
        self._scanned = False                          # был ли скан (иначе статус «не проверено»)
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self._busy = False

        self._proj_paths: list = []
        self._build_ui()
        self._refresh_projects()
        self._load_project()
        self.after(120, self._drain_log)

    def _load_project(self):
        """Загрузить проект: состав + настройки из project_structure.json."""
        self.structure = None
        self.entries = []
        sp = self._structure_path()
        applied = False
        if os.path.exists(sp):
            try:
                self.structure = structmod.load(sp)
                if isinstance(self.structure.get("settings"), dict):
                    cfgmod.apply_settings(self.cfg, self.structure["settings"])
                    applied = True
                self.entries = structmod.to_entries(self.structure)
                self.log(f"Проект загружен: {structmod.count_documents(self.structure)} "
                         f"документов (project_structure.json).")
            except Exception as e:  # noqa: BLE001
                self.log(f"Не удалось прочитать project_structure.json: {e}")
        if not applied and cfgmod.load_project_settings(self.cfg):
            self.log("Настройки взяты из .docflow_settings.json (будут перенесены в состав).")
        # источники по умолчанию НЕ нужны: сервер ПД сканируется автоматически
        # (по полю «Папка ПД»), изыскания — с диска. Источники — только субподряд.
        self._drop_whole_ii_source()       # миграция: убрать источник «весь 1100_ИИ»
        regmod.configure_shifr(self.cfg.shifr_pattern)   # форма шифра для этого проекта
        self._refresh_header()

    def _drop_whole_ii_source(self):
        """Источник, указывающий на ВЕСЬ корень 1100_ИИ, мешает: туда попадают
        00_Переписка/02_Задания и т.п. как «сторонние». Изыскания и так проверяются
        с диска (папка отчётов), поэтому такой источник убираем."""
        ii_root = os.path.normpath(self.cfg.target_ii_abs) if self.cfg.target_ii_abs else None
        if not ii_root:
            return
        kept = []
        for s in self.cfg.sources:
            p = os.path.normpath(self.cfg.abspath(s.path))
            if p == ii_root:                       # ровно весь 1100_ИИ — выкидываем
                self.log(f"Источник «{s.name}» (весь {s.path}) убран: "
                         f"изыскания проверяются по папке отчётов автоматически.")
                continue
            kept.append(s)
        self.cfg.sources = kept

    def _save_project_settings(self):
        """Встроить настройки в project_structure.json (один файл на проект)."""
        st = self.structure if isinstance(self.structure, dict) else {"sections": []}
        st["settings"] = cfgmod.settings_dict(self.cfg)
        self.structure = st
        try:
            structmod.save(st, self._structure_path())
        except OSError as e:
            self.log(f"Не удалось сохранить настройки в состав: {e}")

    # ---------------- проекты (объекты) ----------------
    def _refresh_projects(self):
        projs = [p for p in self.cfg.projects if p]
        if self.cfg.project_root and self.cfg.project_root not in projs:
            projs.insert(0, self.cfg.project_root)
        self.cfg.projects = projs
        self._proj_paths = projs
        names = [os.path.basename(p.rstrip("/\\")) or p for p in projs]
        self.cmb_project["values"] = names
        if self.cfg.project_root in projs:
            self.var_project.set(names[projs.index(self.cfg.project_root)])
        elif names:
            self.var_project.set(names[0])
            self.cfg.project_root = projs[0]

    def _on_project_change(self, event=None):
        idx = self.cmb_project.current()
        if idx < 0 or idx >= len(self._proj_paths):
            return
        self.cfg.project_root = self._proj_paths[idx]
        cfgmod.save(self.cfg)
        self.actions, self.inventory = [], []
        self._scanned = False
        self._load_project()
        self._populate()
        self.log(f"Активный проект: {self.cfg.project_root}")

    def _add_project(self):
        d = filedialog.askdirectory(title="Выберите корень проекта (объекта)")
        if not d:
            return
        d = os.path.normpath(d)
        if d not in self.cfg.projects:
            self.cfg.projects.append(d)
        self.cfg.project_root = d
        cfgmod.save(self.cfg)
        self._refresh_projects()
        self.actions, self.inventory = [], []
        self._scanned = False
        self._load_project()
        self._populate()

    # ОБЩИЕ файлы (для всех помощников) — в корне проекта на сервере
    def _proj_dir(self) -> str:
        return self.cfg.project_root or _app_dir()

    def _structure_path(self) -> str:
        return os.path.join(self._proj_dir(), "project_structure.json")

    # ЛИЧНЫЕ файлы (снимок изменений, журнал отмены) — в %APPDATA%\DocFlow,
    # по подпапке на проект. Не зависят от того, где лежит .exe.
    def _local_dir(self) -> str:
        import hashlib
        root = os.path.normpath(self.cfg.project_root or "default")
        h = hashlib.sha1(root.encode("utf-8")).hexdigest()[:8]
        name = re.sub(r"[^\w.-]+", "_", os.path.basename(root.rstrip("/\\")) or "project")
        d = os.path.join(cfgmod.app_data_dir(), f"{name}_{h}")
        os.makedirs(d, exist_ok=True)
        return d

    def _state_path(self) -> str:
        return os.path.join(self._local_dir(), "state.json")

    def _journal_path(self) -> str:
        return os.path.join(self._local_dir(), "journal.jsonl")

    # ---------------- UI ----------------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Проект:").pack(side="left")
        self.var_project = tk.StringVar()
        self.cmb_project = ttk.Combobox(top, textvariable=self.var_project,
                                        state="readonly", width=34)
        self.cmb_project.pack(side="left", padx=4)
        self.cmb_project.bind("<<ComboboxSelected>>", self._on_project_change)
        ttk.Button(top, text="➕ Создать проект",
                   command=self._create_project).pack(side="left", padx=2)
        ttk.Button(top, text="✏ Редактировать проект",
                   command=self._edit_project).pack(side="left", padx=2)

        self.lbl_proj = ttk.Label(top, text="", font=("Segoe UI", 9))
        self.lbl_proj.pack(side="left", padx=10)
        self.lbl_summary = ttk.Label(top, text="", font=("Segoe UI", 9, "bold"))
        self.lbl_summary.pack(side="left", padx=6)

        ver = ttk.Label(top, text=f"v{__version__}", foreground="#888",
                        cursor="hand2")
        ver.pack(side="right", padx=(6, 0))
        ver.bind("<Button-1>", lambda e: self._about())

        bar = ttk.Frame(self, padding=(8, 0, 8, 6))
        bar.pack(fill="x")
        self.btn_scan = ttk.Button(bar, text="🔍 Сканировать", command=self.do_scan)
        self.btn_scan.pack(side="left")
        self.btn_latest = ttk.Button(bar, text="📦 Сформировать !LATEST", command=self.do_latest)
        self.btn_latest.pack(side="left", padx=4)
        self.btn_transfer = ttk.Button(bar, text="📥 Перенести на сервер", command=self.do_transfer)
        self.btn_transfer.pack(side="left", padx=4)
        self.btn_sign = ttk.Button(bar, text="✍ Подписание", command=self.do_signing)
        self.btn_sign.pack(side="left", padx=4)
        self.btn_dedup = ttk.Button(bar, text="♊ Дубликаты", command=self.do_dedup)
        self.btn_dedup.pack(side="left", padx=4)
        ttk.Button(bar, text="↩ Откатить последнее",
                   command=self.undo_last).pack(side="right")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Отметить узел", command=lambda: self._check_all(True)).pack(side="left")
        ttk.Button(bar, text="Снять узел", command=lambda: self._check_all(False)).pack(side="left", padx=4)
        self.var_problems = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Только проблемные",
                        variable=self.var_problems,
                        command=self._populate).pack(side="left", padx=8)
        self.var_hideflags = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Скрыть «внимание»",
                        variable=self.var_hideflags,
                        command=self._on_struct_select).pack(side="left", padx=4)
        self.var_filter = tk.StringVar(value="all")
        ttk.Label(bar, text="  Показать:").pack(side="left")
        self.cmb = ttk.Combobox(bar, width=22, state="readonly", textvariable=self.var_filter,
                                values=["all"] + list(planner.KIND_TITLE.values()))
        self.cmb.pack(side="left")
        self.cmb.bind("<<ComboboxSelected>>", lambda e: self._on_struct_select())
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=110)
        self.progress.pack(side="right")
        ttk.Button(bar, text="📄 Отчёт", command=self.do_report).pack(side="right", padx=6)

        # две панели: слева структура проекта, справа действия
        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # --- ЛЕВО: дерево структуры проекта ---
        left = ttk.Frame(pw)
        lefthdr = ttk.Frame(left)
        lefthdr.pack(fill="x")
        ttk.Label(lefthdr, text="Структура проекта",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(lefthdr, text="📂 Открыть папку",
                   command=self._open_node_folder).pack(side="right")
        legend = ttk.Label(
            left, font=("Segoe UI", 8), foreground="#666",
            text="●зелёный — в порядке   ●красный — структура нарушена   "
                 "●жёлтый — посторонние/внимание   ●серый — не проверено")
        legend.pack(side="bottom", fill="x", pady=(2, 0))
        tf = ttk.Frame(left)
        tf.pack(side="top", fill="both", expand=True)
        self.stree = ttk.Treeview(tf, show="tree", selectmode="browse")
        lsb = ttk.Scrollbar(tf, orient="vertical", command=self.stree.yview)
        self.stree.configure(yscrollcommand=lsb.set)
        self.stree.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.stree.tag_configure("SEC", font=("Segoe UI", 9, "bold"))
        # статус — цветной кружок-картинка (текст оставляем чёрным)
        self._dot_imgs = {
            DOT_OK: self._make_dot("#1a9a1a"),
            DOT_TODO: self._make_dot("#d23b2e"),
            DOT_ATTENTION: self._make_dot("#e0a000"),
            DOT_UNKNOWN: self._make_dot("#b8b8b8"),
        }
        self.stree.bind("<<TreeviewSelect>>", self._on_struct_select)
        self.stree.bind("<Double-1>", lambda e: self._open_node_folder())
        self.ctx = tk.Menu(self, tearoff=0)
        self.ctx.add_command(label="📂 Открыть папку", command=self._open_node_folder)
        self.ctx.add_command(label="✔ Применить этот узел",
                             command=lambda: self.apply(current_only=True))
        self.ctx.add_command(label="🚫 Добавить папку в исключения",
                             command=self._exclude_node)
        self.stree.bind("<Button-3>", self._ctx_menu)
        pw.add(left, weight=1)

        # --- ПРАВО: действия для выбранного узла ---
        right = ttk.Frame(pw)
        self.lbl_detail = ttk.Label(right, text="Выберите раздел или документ слева",
                                    font=("Segoe UI", 9, "bold"))
        self.lbl_detail.pack(anchor="w")
        # имя файла документа (его смотрят проектировщики)
        self.lbl_file = ttk.Label(right, text="", foreground="#1F4E79",
                                  font=("Segoe UI", 9), cursor="hand2")
        self.lbl_file.pack(anchor="w")
        self.lbl_file.bind("<Button-1>", lambda e: self._copy_file_name())
        cols = ("chk", "kind", "comment")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("chk", text="✓")
        self.tree.heading("kind", text="Действие")
        self.tree.heading("comment", text="Что будет выполнено")
        self.tree.column("chk", width=34, anchor="center", stretch=False)
        self.tree.column("kind", width=210, anchor="w", stretch=False)
        self.tree.column("comment", width=520, anchor="w", stretch=True)
        rsb = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=rsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        rsb.pack(side="right", fill="y")
        self.tree.tag_configure("FLAG", foreground="#a05a00")
        self.tree.tag_configure("MKSTRUCT", foreground="#c0392b")
        self.tree.tag_configure("TRANSFER", foreground="#c0392b")
        self.tree.tag_configure("MKCATALOG", foreground="#0a7")
        self.tree.tag_configure("MOVE", foreground="#0a7")
        self.tree.tag_configure("RENAME", foreground="#0b6")
        self.tree.tag_configure("ARCHIVE", foreground="#36c")
        self.tree.tag_configure("ARCHIVE_FOLDER", foreground="#36c")
        self.tree.tag_configure("COPY_LATEST", foreground="#609")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", self._open_location)
        pw.add(right, weight=2)

        # нижняя панель: применить + лог
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        self.btn_apply_sel = ttk.Button(bottom, text="✔ Применить в этом узле",
                                        command=lambda: self.apply(current_only=True))
        self.btn_apply_sel.pack(side="left")
        self.btn_apply_all = ttk.Button(bottom, text="✔✔ Применить всё отмеченное",
                                        command=lambda: self.apply(current_only=False))
        self.btn_apply_all.pack(side="left", padx=4)
        self.lbl_stat = ttk.Label(bottom, text="")
        self.lbl_stat.pack(side="right")

        logf = ttk.LabelFrame(self, text="Журнал работы", padding=4)
        logf.pack(fill="both", expand=False, padx=8, pady=(0, 8))
        self.txt = tk.Text(logf, height=8, wrap="word", state="disabled",
                           font=("Consolas", 9))
        self.txt.pack(fill="both", expand=True)

    def _refresh_header(self):
        root = self.cfg.project_root or "(не задан)"
        n = len([s for s in self.cfg.sources if s.enabled])
        obj = self.cfg.object_name
        obj_part = f"    |    объект: {obj}" if obj else ""
        self.lbl_proj.config(text=f"Проект: {root}{obj_part}    |    источников: {n}")

    # ---------------- логирование ----------------
    def log(self, msg: str):
        self.log_q.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt.config(state="normal")
                self.txt.insert("end", msg + "\n")
                self.txt.see("end")
                self.txt.config(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    # ---------------- состав проекта ----------------
    def _registry_abs(self) -> str:
        return self.cfg.abspath(self.cfg.registry_path)

    def do_build_composition(self):
        """Шаг 1: прочитать Excel и сформировать project_structure.json."""
        if self._busy:
            return
        if not self.cfg.registry_path or not os.path.exists(self._registry_abs()):
            messagebox.showwarning("Нет файла Excel",
                                   "Укажите файл «…Наименование файлов.xlsx» в Настройках.")
            return
        self._set_busy(True)
        self.log("── Формирование состава проекта из Excel ──")
        threading.Thread(target=self._comp_worker, daemon=True).start()

    def _comp_worker(self):
        try:
            self.log(f"Файл Excel: {self._registry_abs()}")
            entries, diag = regmod.load_registry_diag(
                self._registry_abs(), self.cfg.registry_sheet,
                smeta_sub=self.cfg.smeta_sub_codes, smeta_razdel=self.cfg.smeta_razdel)
            self.log(f"Excel: {diag}")
            obj = regmod.read_object_name(self._registry_abs(), self.cfg.registry_sheet)
            if obj:
                self.log(f"Наименование объекта: {obj}")
            st = structmod.build_from_entries(
                entries, smeta_codes=self.cfg.smeta_short_codes,
                smeta_razdel=self.cfg.smeta_razdel,
                razdel_codes=self.cfg.razdel_codes,
                pub_ext=self.cfg.published_extensions,
                smeta_ext=self.cfg.smeta_extensions,
                edit_dir=self.cfg.edit_dir, pub_dir=self.cfg.published_dir)
            # инженерные изыскания (ИИ): отдельный состав по видам изысканий,
            # обозначения уточняются по реальным папкам 1100_ИИ/04_Отчеты
            proj_shifr = planner._project_shifr(structmod.to_entries(st))
            st["ii_sections"] = structmod.build_ii_sections(
                self.cfg.ii_abs, self.cfg.ii_types, proj_shifr,
                pub_ext=self.cfg.published_extensions,
                edit_dir=self.cfg.edit_dir, pub_dir=self.cfg.published_dir)
            st["settings"] = cfgmod.settings_dict(self.cfg)   # настройки — в тот же файл
            structmod.save(st, self._structure_path())
            entries_out = structmod.to_entries(st)
            pd_secs = len(st.get("sections", []))
            ii_secs = len(st.get("ii_sections", []))
            pd_docs = sum(len(s.get("documents", [])) for s in st.get("sections", []))
            ii_docs = sum(len(s.get("documents", [])) for s in st.get("ii_sections", []))
            self.log(f"Состав сохранён → project_structure.json: "
                     f"проектная документация — {pd_secs} разделов / {pd_docs} док., "
                     f"изыскания — {ii_secs} видов / {ii_docs} отчётов.")

            def _apply():
                if obj:
                    self.cfg.object_name = obj
                self.structure = st
                self.entries = entries_out
                messagebox.showinfo(
                    "Состав проекта готов",
                    f"Сформирован состав:\n\n"
                    f"• Проектная документация (ПД): {pd_secs} разделов, {pd_docs} документов\n"
                    f"• Инженерные изыскания (ИИ): {ii_secs} видов, {ii_docs} отчётов\n\n"
                    "Файл project_structure.json лежит рядом с программой — его можно "
                    "открыть и поправить.\n\nДалее: «Создать папки», затем «Сканировать».")

            self.after(0, _apply)
        except Exception as e:  # noqa: BLE001
            import traceback
            msg = str(e)
            self.log("ОШИБКА чтения Excel:\n" + traceback.format_exc())
            self.after(0, lambda m=msg: messagebox.showerror("Ошибка чтения Excel", m))
        finally:
            self.after(0, lambda: self._set_busy(False))

    def do_create_folders(self):
        """Шаг 2: создать папки разделов/документов с !ARCHIVE по составу."""
        if self._busy:
            return
        if not self.entries:
            messagebox.showwarning("Нет состава",
                                   "Сначала нажмите «Состав из Excel» (или положите "
                                   "project_structure.json рядом с программой).")
            return
        if not self.cfg.project_root:
            messagebox.showwarning("Нет корня проекта",
                                   "Укажите «Корень проекта» в Настройках.")
            return
        acts = []
        for e in self.entries:
            section = planner._section_folder(e, self.cfg)
            arch = os.path.join(planner._doc_folder(e, self.cfg), self.cfg.archive_name)
            if not os.path.isdir(arch):
                acts.append(planner.Action(
                    planner.MKSTRUCT,
                    os.path.join(planner._area_root(e, self.cfg), section),
                    "", reason=f"создать {e.oboznachenie}/{self.cfg.archive_name}",
                    mkdirs=[arch], group=section))
        if not acts:
            messagebox.showinfo("Готово", "Все папки по составу уже существуют.")
            return
        if not messagebox.askyesno(
                "Создать папки",
                f"Будет создано папок документов: {len(acts)}\n"
                f"(каждая с подпапкой {self.cfg.archive_name}).\n\nПродолжить?"):
            return
        self._set_busy(True)
        self.log(f"── Создание структуры папок ({len(acts)}) ──")
        threading.Thread(target=self._apply_worker, args=(acts,), daemon=True).start()

    # ---------------- сканирование ----------------
    def _scan_sources(self):
        """Папки для сканирования: сервер ПД сканируется автоматически (по полю
        «Папка ПД»), плюс подключённые источники (субподряд). Сервер добавлять
        источником вручную НЕ нужно. Несуществующую папку скан просто пропустит."""
        srcs = [s for s in self.cfg.sources if s.enabled]
        if self.cfg.target_pd:                       # сервер ПД — всегда авто
            pd = os.path.normpath(self.cfg.target_pd_abs)
            covered = any(os.path.normpath(self.cfg.abspath(s.path)) == pd for s in srcs)
            if not covered:
                srcs = [cfgmod.Source("Сервер — ПД (авто)", self.cfg.target_pd, "ПД")] + srcs
        return srcs

    def do_scan(self):
        if self._busy:
            return
        if not self.cfg.project_root:
            messagebox.showwarning("Нет корня проекта",
                                   "Укажите «Корень проекта» в настройках проекта.")
            return
        if not self.entries and (not self.cfg.registry_path
                                 or not os.path.exists(self._registry_abs())):
            messagebox.showwarning(
                "Нет состава проекта",
                "Сначала нажмите «Состав из Excel» (или укажите файл Excel в Настройках).")
            return
        self._set_busy(True)
        self.progress.start(12)
        self.log("── Сканирование начато ──")
        # сброс UI-состояния только в главном потоке
        self._scanned = False
        self.actions = []
        self._populate()
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _apply_scan_results(self, entries, inventory, groups_seen, plan):
        """Применить результаты скана в главном потоке UI."""
        self.entries = entries
        self.inventory = inventory
        self.all_groups = groups_seen
        self.actions = plan
        self._scanned = True
        self._populate()
        self._summary(plan)

    def _scan_worker(self):
        try:
            if self.entries:
                entries = list(self.entries)
                self.log(f"Состав проекта: {len(entries)} документов.")
            else:
                self.log(f"Файл Excel: {self._registry_abs()}")
                entries, diag = regmod.load_registry_diag(
                    self._registry_abs(), self.cfg.registry_sheet,
                    smeta_sub=self.cfg.smeta_sub_codes, smeta_razdel=self.cfg.smeta_razdel)
                self.log(f"Excel: {diag}")

            matcher = Matcher(entries)

            inventory = []
            groups_seen = set()
            for s in self._scan_sources():
                path = self.cfg.abspath(s.path)
                recs = scanner.scan_source(s.name, path, s.category,
                                           self.cfg.ignore_patterns,
                                           use_hash=self.cfg.use_hash,
                                           skip_dirs=self.cfg.scan_skip_dirs + self.cfg.exclude_folders)
                self.log(f"  • {s.name}: {len(recs)} файлов ({path})")
                inventory.extend(recs)
                for r in recs:
                    groups_seen.add(planner._first_component(r.rel))

            prev = scanner.load_snapshot(self._state_path())
            changes = scanner.diff(prev, inventory, self.cfg.use_hash)
            changed = {r.path for r in changes.added + changes.modified}
            changed |= {nr.path for _, nr in changes.moved}
            if prev:
                self.log(f"Изменения: +{len(changes.added)} новых, "
                         f"~{len(changes.modified)} изменённых, "
                         f"⇄{len(changes.moved)} перемещённых, "
                         f"−{len(changes.removed)} удалённых.")
            else:
                self.log("Первое сканирование — снимок сохранён для отслеживания изменений.")

            plan = planner.build_plan(inventory, matcher, self.cfg, changed)
            scanner.save_snapshot(self._state_path(), inventory)

            plan.sort(key=lambda a: (not a.is_change, KIND_ORDER.get(a.kind, 9),
                                     os.path.basename(a.src).lower()))
            self.after(0, lambda e=entries, inv=inventory, g=groups_seen, p=plan:
                       self._apply_scan_results(e, inv, g, p))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            self.log(f"ОШИБКА: {msg}")
            self.after(0, lambda m=msg: messagebox.showerror("Ошибка сканирования", m))
        finally:
            self.after(0, lambda: (self.progress.stop(), self._set_busy(False)))

    def _summary(self, plan):
        from collections import Counter
        c = Counter(a.kind for a in plan)
        # TRANSFER в плане скана больше не создаётся (перенос — отдельный диалог)
        n_transfer = c.get(planner.TRANSFER, 0)
        extra = f"на сервер {n_transfer}, " if n_transfer else ""
        self.log(
            f"Готово. Базовая структура {c.get(planner.MKSTRUCT,0)}, "
            f"{extra}"
            f"каталоги {c.get(planner.MKCATALOG,0)}, "
            f"в архив {c.get(planner.ARCHIVE_FOLDER,0)+c.get(planner.ARCHIVE,0)}, "
            f"переименовать {c.get(planner.RENAME,0)}, "
            f"переместить {c.get(planner.MOVE,0)}, "
            f"внимание {c.get(planner.FLAG,0)}.")

    def do_latest(self):
        if self._busy:
            return
        if not self.entries:
            messagebox.showwarning("Нет состава",
                                   "Сначала сформируйте состав проекта (Настройки).")
            return
        LatestDialog(self, self.cfg, self.entries, self._registry_abs())

    def do_transfer(self):
        if self._busy:
            return
        if not self.entries:
            messagebox.showwarning("Нет состава",
                                   "Сначала сформируйте состав проекта (Настройки).")
            return
        if not transfermod.external_sources(self.cfg):
            messagebox.showinfo(
                "Перенос на сервер",
                "Не подключено ни одной субподрядной папки.\n\n"
                "Добавьте её: «✏ Редактировать проект» → «Папки-источники» → "
                "«Добавить…» (путь к папке субподрядчика). Серверные папки "
                "(4300_ПД, 1100_ИИ) субподрядом не считаются.")
            return
        TransferDialog(self, self.cfg, self.entries, self._registry_abs())

    def do_signing(self):
        if self._busy:
            return
        if not self.entries:
            messagebox.showwarning("Нет состава",
                                   "Сначала сформируйте состав проекта (Настройки).")
            return
        SigningDialog(self, self.cfg, self.entries)

    def do_dedup(self):
        if self._busy:
            return
        DuplicatesDialog(self, self.cfg, self._journal_path())

    # ---------------- структура (лево) и детали (право) ----------------
    def _status(self, acts):
        from collections import Counter
        if not getattr(self, "_scanned", False):
            return DOT_UNKNOWN, "не проверено"
        worst = max((self._severity(a) for a in acts), default=0)
        if worst >= 2:
            return DOT_TODO, "структура нарушена"
        if worst == 1:
            return DOT_ATTENTION, "требует внимания"
        return DOT_OK, "в порядке"

    def _severity(self, a) -> int:
        """0 — ок, 1 — замечание (жёлтый), 2 — ошибка (красный).
        Берётся из настроек значимости проверки; иначе — по типу действия."""
        cid = getattr(a, "check", "")
        if cid:
            lvl = (self.cfg.check_levels or {}).get(cid)
            if lvl == "error":
                return 2
            if lvl == "warn":
                return 1
        if a.kind in planner.STRUCTURE_KINDS:
            return 2
        if a.kind in (planner.FLAG, planner.NORMALIZE):
            return 1
        return 0

    def _loc(self, a) -> str:
        """Бакет действия: ii / pd / ext:<i> (конкретная субподрядная папка) / ext."""
        p = os.path.normpath(a.src)

        def _under(base):
            return base and (p == base or p.startswith(base + os.sep))
        ii = os.path.normpath(self.cfg.ii_abs) if self.cfg.ii_abs else None
        pd = os.path.normpath(self.cfg.target_pd_abs) if self.cfg.target_pd_abs else None
        if _under(ii):
            return "ii"        # сначала ИИ: его папка может лежать внутри ПД-дерева
        if _under(pd):
            return "pd"
        for key, base, _name in getattr(self, "_ext_bases", []):
            if _under(base):
                return key
        return "ext"

    # --- произвольная вложенность: дерево строится из путей папок документов ---
    @staticmethod
    def _new_node():
        return {"children": {}, "actions": [], "entry": None}

    def _rel_parts(self, folder: str, base: str = None) -> list:
        """Путь папки относительно корня области (по умолчанию 4300_ПД)."""
        if base is None:
            base = self.cfg.target_pd_abs or ""
        rel = os.path.relpath(folder, base) if base else folder
        return [p for p in re.split(r"[\\/]+", rel) if p and p != "."]

    def _is_ignored_name(self, name: str) -> bool:
        import fnmatch
        low = name.lower()
        return any(fnmatch.fnmatch(low, p.lower()) for p in self.cfg.ignore_patterns)

    def _service_names(self) -> set:
        c = self.cfg
        names = {c.edit_dir, c.published_dir, c.archive_name, "!LATEST", "!WORK",
                 "!SUPPORT", "!REF", "!LINKS", "!INITIAL", "DWG", "DOC", "PDF"}
        # имена ред./публ. папок, заданные у отдельных томов (РЕД/НЕРЕД и т.п.)
        for e in self.entries:
            if getattr(e, "edit_dir", ""):
                names.add(e.edit_dir)
            if getattr(e, "pub_dir", ""):
                names.add(e.pub_dir)
        return names

    def _node_parts(self, folder: str, base: str = None) -> list:
        """Путь до уровня каталога версии (служебные папки !EDIT/!PUBLISHED/
        !ARCHIVE и т.п. в дереве не показываем — обрезаем путь до них)."""
        service = self._service_names()
        out = []
        for p in self._rel_parts(folder, base):
            if p in service:
                break
            out.append(p)
        return out

    def _doc_parts(self, entry) -> list:
        return self._rel_parts(planner._doc_folder(entry, self.cfg),
                               planner._area_root(entry, self.cfg))

    def _insert_path(self, root, parts, entry=None, action=None):
        node = root
        for p in parts:
            node = node["children"].setdefault(p, self._new_node())
        if entry is not None:
            node["entry"] = entry
        if action is not None:
            node["actions"].append(action)

    def _gather(self, node) -> list:
        acts = list(node["actions"])
        for ch in node["children"].values():
            acts += self._gather(ch)
        return acts

    def _make_dot(self, color: str, size: int = 9, gap: int = 6):
        """Маленький цветной кружок + отступ справа (белое поле) до текста."""
        w = size + gap
        img = tk.PhotoImage(width=w, height=size)
        c = (size - 1) / 2.0
        rad = size / 2.0 - 1.0
        rows = []
        for y in range(size):
            px = [color if (x < size and (x - c) ** 2 + (y - c) ** 2 <= rad * rad)
                  else "#ffffff" for x in range(w)]
            rows.append("{" + " ".join(px) + "}")
        img.put(" ".join(rows))
        return img

    def _render_children(self, node, parent_iid, top=False, prefix=""):
        for name, child in sorted(node["children"].items(),
                                  key=lambda kv: self._group_sort_key(kv[0])):
            sub = self._gather(child)
            dot, verd = self._status(sub)
            if self.var_problems.get() and dot == DOT_OK:
                continue                     # «только проблемные» — прячем зелёные
            label = f"{name}" + (f" — {verd}" if top else "")
            iid = self.stree.insert(parent_iid, "end", text=label,
                                    image=self._dot_imgs.get(dot, ""),
                                    open=(dot not in (DOT_OK, DOT_UNKNOWN)))
            self.node_actions[iid] = list(child["actions"])   # замечания ЭТОГО узла
            self.node_sub[iid] = sub                          # всё поддерево (для подсказки)
            path = os.path.join(prefix, name) if prefix else ""
            self.node_path[iid] = path
            self._render_children(child, iid, top=False, prefix=path)

    def _iter_tree_items(self, parent=""):
        out = []
        for i in self.stree.get_children(parent):
            out.append(i)
            out += self._iter_tree_items(i)
        return out

    def _capture_tree_state(self):
        """Запомнить, какие узлы раскрыты, выделение и прокрутку — чтобы после
        повторного сканирования не пролистывать всё заново."""
        items = self._iter_tree_items()
        if not items:
            return                      # нечего запоминать (первый показ)
        self._tree_open = {self.node_path.get(i) for i in items
                           if self.stree.item(i, "open") and self.node_path.get(i)}
        sel = self.stree.selection()
        self._tree_sel = self.node_path.get(sel[0]) if sel else None
        try:
            self._tree_scroll = self.stree.yview()[0]
        except tk.TclError:
            pass

    def _restore_tree_state(self):
        if not getattr(self, "_tree_open", None) and not getattr(self, "_tree_sel", None):
            return
        for i in self._iter_tree_items():
            p = self.node_path.get(i)
            if p and p in getattr(self, "_tree_open", set()):
                self.stree.item(i, open=True)
            if p and p == getattr(self, "_tree_sel", None):
                self.stree.selection_set(i)
                self.stree.see(i)
        try:
            self.stree.yview_moveto(getattr(self, "_tree_scroll", 0.0))
        except tk.TclError:
            pass

    def _populate(self):
        """Строит дерево структуры слева (произвольной вложенности)."""
        self._capture_tree_state()      # запомнить раскрытые узлы/прокрутку
        self.stree.delete(*self.stree.get_children())
        self.node_actions = {}
        self.node_sub = {}
        self.node_path = {}
        self.cur_actions = []
        self.tree.delete(*self.tree.get_children())
        self.row_action.clear()
        self.lbl_detail.config(text="Выберите раздел или документ слева")
        if hasattr(self, "lbl_file"):
            self.lbl_file.config(text="")

        # субподрядные папки (источники, не серверные) — каждая отдельной веткой,
        # чтобы её можно было раскрыть и «Открыть папку» (реальные пути)
        self._ext_bases = [
            (f"ext:{i}", os.path.normpath(self.cfg.abspath(s.path)), s.name)
            for i, s in enumerate(transfermod.external_sources(self.cfg))]

        from collections import defaultdict
        buckets = defaultdict(list)
        for a in self.actions:
            buckets[self._loc(a)].append(a)
        entry_by_key = {e.key: e for e in self.entries}

        pd_name = (self.cfg.target_pd or "ПД").replace("\\", "/").split("/")[-1]
        ii_name = (self.cfg.ii_root or "ИИ").replace("\\", "/").split("/")[0]
        pd_base = os.path.normpath(self.cfg.target_pd_abs) if self.cfg.target_pd_abs else ""
        ii_base = os.path.normpath(self.cfg.ii_abs) if self.cfg.ii_abs else ""
        base_of = {"pd": pd_base, "ii": ii_base}
        order = [("pd", f"📂 {pd_name} — проектная документация"),
                 ("ii", f"📂 {ii_name} — изыскания")]
        for key, base, name in self._ext_bases:
            base_of[key] = base
            order.append((key, f"📂 Субподряд: {name}"))
        order.append(("ext", "📂 Сторонние папки"))
        # посторонние папки — их содержимое НЕ показываем как узлы структуры
        foreign_dirs = [os.path.normpath(a.src) for a in self.actions
                        if a.kind == planner.FLAG and a.reason.startswith("посторонняя папка")]

        def _under_foreign(path):
            return any(path == d or path.startswith(d + os.sep) for d in foreign_dirs)

        for bkey, blabel in order:
            root = self._new_node()
            base = base_of.get(bkey)            # для pd/ii есть корень области
            structured = base is not None and bkey != "ext"
            # эта область отсканирована как источник? (тогда узлы версий дадут файлы)
            scanned_area = any(
                base and os.path.normpath(fr.path).startswith(base + os.sep)
                for fr in self.inventory)
            # состав проекта + реально существующие папки (зелёные узлы) этой области
            if structured:
                for e in self.entries:
                    er = planner._area_root(e, self.cfg)
                    if not (er and os.path.normpath(er) == base):
                        continue
                    self._insert_path(root, self._doc_parts(e), entry=e)
                    # если область не сканировалась источником (актуально для ИИ) —
                    # покажем существующие каталоги версий, прочитав папку документа
                    if not scanned_area:
                        doc = planner._doc_folder(e, self.cfg)
                        try:
                            names = os.listdir(doc) if os.path.isdir(doc) else []
                        except OSError:
                            names = []
                        for nm in names:
                            full = os.path.join(doc, nm)
                            if os.path.isdir(full) and planner.cat_match(nm)[0]:
                                self._insert_path(root, self._rel_parts(full, base))
                for fr in self.inventory:
                    p = os.path.normpath(fr.path)
                    if base and (p == base or p.startswith(base + os.sep)) \
                            and not _under_foreign(p):
                        self._insert_path(root, self._node_parts(os.path.dirname(fr.path), base))
                # субподряд: всегда показываем реальную структуру папки с диска
                # (даже если ничего не распозналось) — чтобы подключённый источник был виден
                if str(bkey).startswith("ext:") and base and os.path.isdir(base):
                    service = self._service_names()
                    for dp, dns, _fns in os.walk(base):
                        keep = []
                        for d in dns:
                            if d in service or self._is_ignored_name(d):
                                continue
                            self._insert_path(root, self._node_parts(
                                os.path.join(dp, d), base))
                            keep.append(d)
                        dns[:] = keep
            is_server = bkey in ("pd", "ii")
            # действия — на узел нужного уровня
            for a in buckets[bkey]:
                # выбираем путь, реально лежащий в этой ветке: узел или папка файла
                placed = None
                for cand in (a.node, os.path.dirname(a.src) if a.src else ""):
                    cn = os.path.normpath(cand) if cand else ""
                    if structured and cn and (cn == base or cn.startswith(base + os.sep)):
                        placed = cand
                        break
                if placed:
                    if _under_foreign(os.path.normpath(placed)):
                        continue            # содержимое посторонней папки не показываем
                    self._insert_path(root, self._node_parts(placed, base), action=a)
                    continue
                e = entry_by_key.get(a.key)
                if e is not None and is_server:
                    self._insert_path(root, self._doc_parts(e), action=a)
                else:
                    sec = a.group or "Прочее"
                    self._insert_path(root, [sec, "⚠ посторонние / структура"], action=a)
            # субподрядные ветки показываем всегда (даже пустые — видно, что подключено)
            if not root["children"] and not str(bkey).startswith("ext:"):
                continue
            allacts = self._gather(root)
            bdot, _ = self._status(allacts)
            bnode = self.stree.insert("", "end", text=blabel,
                                      image=self._dot_imgs.get(bdot, ""),
                                      open=True, tags=("SEC",))
            self.node_actions[bnode] = list(root["actions"])
            self.node_sub[bnode] = allacts
            self.node_path[bnode] = base or ""
            self._render_children(root, bnode, top=True, prefix=base or "")
        self._update_stat()
        self._restore_tree_state()      # вернуть раскрытые узлы/выделение/прокрутку

    @staticmethod
    def _group_sort_key(name: str):
        m = re.match(r"\s*(\d+)", name)
        return (int(m.group(1)) if m else 999, name.lower())

    def _on_struct_select(self, event=None):
        sel = self.stree.selection()
        if not sel:
            return
        acts = self.node_actions.get(sel[0], [])
        sub = self.node_sub.get(sel[0], acts)
        label = self.stree.item(sel[0], "text")
        self._show_file_name(label)
        self._render_detail(acts, label, sub)

    def _entry_by_name(self, name: str):
        """Документ состава по имени узла (обозначение или {обозн}_{дата})."""
        name = (name or "").split(" — ")[0].strip()
        for e in self.entries:
            if e.oboznachenie == name:
                return e
        pfx = planner.cat_match(name)[0]      # узел каталога версии
        if pfx:
            for e in self.entries:
                if e.oboznachenie == pfx:
                    return e
        return None

    def _show_file_name(self, label: str):
        e = self._entry_by_name(label)
        if e and e.doc_template:
            tom = f"   ·   том {e.tom}" if getattr(e, "tom", "") else ""
            self.lbl_file.config(text=f"📄 файл: {e.doc_template}{tom}   (клик — копировать)")
            self._cur_file_name = e.doc_template
        else:
            self.lbl_file.config(text="")
            self._cur_file_name = ""

    def _copy_file_name(self):
        if getattr(self, "_cur_file_name", ""):
            self.clipboard_clear()
            self.clipboard_append(self._cur_file_name)
            self.log(f"Имя файла скопировано: {self._cur_file_name}")

    def _comment(self, a) -> str:
        """Понятное описание, что будет выполнено."""
        if a.kind in (planner.MKSTRUCT, planner.FLAG, planner.NORMALIZE):
            return a.reason
        src = os.path.basename(a.src)
        if a.dst:
            d = os.path.relpath(a.dst, self.cfg.project_root) \
                if self.cfg.project_root else a.dst
            verb = "копировать" if a.kind in (planner.TRANSFER, planner.COPY_LATEST) else "переместить"
            return f"{verb}: «{src}»  →  {d}"
        return a.reason or src

    def _render_detail(self, acts, label, sub=None):
        self.tree.delete(*self.tree.get_children())
        self.row_action.clear()
        self.cur_actions = list(acts)
        self.lbl_detail.config(text=f"Действия: {label}")
        flt = self.var_filter.get()
        hide = self.var_hideflags.get()
        shown = [a for a in acts
                 if not (hide and a.kind == planner.FLAG)
                 and (flt == "all" or a.title == flt)]
        if not shown:
            # у самого узла замечаний нет — но, может, есть во вложенных
            sub = sub if sub is not None else acts
            nested = [a for a in sub if a not in acts]
            if nested:
                self.tree.insert("", "end", values=(
                    "", "— здесь чисто —",
                    "замечания во вложенных папках — разверните узел и выберите их"))
            else:
                self.tree.insert("", "end",
                                 values=("", "— в порядке —", "замечаний нет"))
            self._update_stat()
            return
        for a in sorted(shown, key=lambda x: (KIND_ORDER.get(x.kind, 9),
                                              os.path.basename(x.src).lower())):
            chk = CHECK_ON if (a.selected and a.kind != planner.FLAG) else (
                "" if a.kind == planner.FLAG else CHECK_OFF)
            mark = "● " if a.is_change else ""
            disp = a.title
            if a.kind == planner.FLAG:
                r = a.reason.lower()
                if "папк" in r and "посторон" in r:
                    disp = "Посторонняя папка"
                elif "посторон" in r:
                    disp = "Посторонний файл"
                elif "не на месте" in r:
                    disp = "Папка не на месте"
            title = f"● {mark}{disp}"
            iid = self.tree.insert("", "end",
                                   values=(chk, title, self._comment(a)), tags=(a.kind,))
            self.row_action[iid] = a
        self._update_stat()

    def _on_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _on_space(self, event):
        for iid in self.tree.selection():
            self._toggle(iid)

    def _toggle(self, iid):
        a = self.row_action.get(iid)
        if not a or a.kind == planner.FLAG:
            return
        a.selected = not a.selected
        vals = list(self.tree.item(iid, "values"))
        vals[0] = CHECK_ON if a.selected else CHECK_OFF
        self.tree.item(iid, values=vals)
        self._update_stat()

    def _check_all(self, value: bool):
        # отмечаем все действия текущего узла (правая панель)
        for a in self.cur_actions:
            if a.kind != planner.FLAG:
                a.selected = value
        if self.cur_actions:
            self._render_detail(self.cur_actions, self.lbl_detail.cget("text").replace("Действия: ", ""))

    def _update_stat(self):
        sel = sum(1 for a in self.actions if a.selected and a.kind != planner.FLAG)
        total = sum(1 for a in self.actions if a.kind != planner.FLAG)
        flags = sum(1 for a in self.actions if a.kind == planner.FLAG)
        self.lbl_stat.config(
            text=f"Отмечено: {sel} из {total} · требует внимания: {flags}")
        # сводка вверху
        if not self._scanned:
            self.lbl_summary.config(text="⚪ не проверено — нажмите «Сканировать»",
                                    foreground="#8a8a8a")
        else:
            struct = sum(1 for a in self.actions if a.kind in planner.STRUCTURE_KINDS)
            if struct or flags:
                self.lbl_summary.config(
                    text=f"🔴 нарушений: {struct}   🟡 посторонних: {flags}",
                    foreground="#c0392b")
            else:
                self.lbl_summary.config(text="🟢 всё в порядке", foreground="#1a7f1a")

    def _open_location(self, event):
        iid = self.tree.identify_row(event.y)
        a = self.row_action.get(iid)
        if a and os.path.exists(a.src):
            self._open_folder(os.path.dirname(a.src))

    def _ctx_menu(self, event):
        iid = self.stree.identify_row(event.y)
        if iid:
            self.stree.selection_set(iid)
            self._on_struct_select()
            self.ctx.tk_popup(event.x_root, event.y_root)

    def _exclude_node(self):
        sel = self.stree.selection()
        if not sel:
            return
        path = self.node_path.get(sel[0], "")
        name = os.path.basename(path.rstrip("/\\")) if path else ""
        if name and name not in self.cfg.exclude_folders:
            self.cfg.exclude_folders.append(name)
            self._save_project_settings()
            cfgmod.save(self.cfg)
            self.log(f"Папка добавлена в исключения: {name}")
            if self.entries and self.cfg.sources:
                self.do_scan()

    def do_report(self):
        if not self._scanned:
            messagebox.showinfo("Отчёт", "Сначала отсканируйте проект.")
            return
        from collections import Counter
        c = Counter(a.kind for a in self.actions)
        struct = sum(v for k, v in c.items() if k in planner.STRUCTURE_KINDS)
        lines = ["DocFlow — отчёт по проекту",
                 f"Проект: {self.cfg.project_root}",
                 f"Нарушений структуры: {struct} · посторонних/внимание: {c.get(planner.FLAG, 0)}",
                 ""]
        by_grp = {}
        for a in self.actions:
            by_grp.setdefault(a.group or "Прочее", []).append(a)
        for grp in sorted(by_grp, key=self._group_sort_key):
            lines.append(f"=== {grp} ===")
            for a in sorted(by_grp[grp], key=lambda x: KIND_ORDER.get(x.kind, 9)):
                lines.append(f"  [{a.title}] {a.reason}")
            lines.append("")
        f = filedialog.asksaveasfilename(
            defaultextension=".txt", initialfile="DocFlow_отчёт.txt",
            filetypes=[("Текст", "*.txt")])
        if not f:
            return
        with open(f, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines))
        messagebox.showinfo("Отчёт", f"Сохранён: {f}")
        self._open_folder(os.path.dirname(f))

    def _open_node_folder(self):
        sel = self.stree.selection()
        if not sel:
            return
        path = self.node_path.get(sel[0], "")
        if not path:
            return
        # если папки ещё нет (запланирована) — открываем ближайшую существующую
        while path and not os.path.isdir(path):
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        if path:
            self._open_folder(path)

    @staticmethod
    def _open_folder(path: str):
        try:
            os.startfile(path)            # Windows
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        except Exception:  # noqa: BLE001
            pass

    # ---------------- применение ----------------
    def apply(self, current_only: bool):
        if current_only:
            todo = [a for a in getattr(self, "cur_actions", [])
                    if a.kind != planner.FLAG and a.selected]
        else:
            todo = [a for a in self.actions if a.kind != planner.FLAG and a.selected]
        if not todo:
            messagebox.showinfo("Нечего выполнять",
                                "Отметьте действия галочками (столбец ✓) и повторите.")
            return
        if not messagebox.askyesno("Подтверждение",
                                   f"Выполнить действий: {len(todo)}?\n"
                                   "Файлы будут переименованы/перемещены. "
                                   "Операцию можно откатить кнопкой «Откатить последнее»."):
            return
        self._set_busy(True)
        threading.Thread(target=self._apply_worker, args=(todo,), daemon=True).start()

    def _apply_worker(self, todo):
        try:
            results = executor.execute(todo, self._journal_path(), log=self.log)
            ok = sum(1 for r in results if r.ok)
            self.log(f"── Выполнено: {ok} из {len(results)} ──")
            done_src = {r.action.src for r in results if r.ok}

            def _apply_ui():
                self.actions = [a for a in self.actions if a.src not in done_src]
                self._populate()

            self.after(0, _apply_ui)
        except Exception as e:  # noqa: BLE001
            self.log(f"ОШИБКА: {e}")
        finally:
            self.after(0, lambda: self._set_busy(False))

    def undo_last(self):
        if self._busy:
            return
        if not os.path.exists(self._journal_path()) or not executor.read_journal(self._journal_path()):
            messagebox.showinfo("Откат", "Нет выполненных операций для отката.")
            return
        if not messagebox.askyesno("Откат", "Откатить последнюю выполненную операцию?"):
            return
        n = executor.undo_last(self._journal_path(), log=self.log)
        if n == 0:
            messagebox.showinfo("Откат", "Откат не потребовался "
                                "(файлы уже на месте или были изменены).")
            return
        messagebox.showinfo("Откат", f"Возвращено объектов: {n}.\n"
                                     "Обновляю вид…")
        # обновляем дерево по факту на диске
        if self.entries and self.cfg.sources:
            self.do_scan()
        else:
            self.actions = []
            self._populate()

    def _about(self):
        messagebox.showinfo(
            "О программе",
            f"DocFlow — комплектование проектной документации\n\n"
            f"Версия: {__version__}\n"
            f"Сборка: {__build_date__}\n\n"
            "Наведение порядка в 4300_ПД и 1100_ИИ, сборка !LATEST, "
            "перенос с субподряда (сверка по CRC32).\n\n"
            "История изменений — в файле CHANGELOG.txt рядом с программой.")

    # ---------------- проекты и их настройки ----------------
    def _create_project(self):
        """Создать новый проект (объект): выбрать корень, открыть его настройки."""
        d = filedialog.askdirectory(title="Выберите корень нового проекта (объекта)")
        if not d:
            return
        d = os.path.normpath(d)
        if d not in self.cfg.projects:
            self.cfg.projects.append(d)
        self.cfg.project_root = d
        # для нового проекта (нет project_structure.json) — настройки по умолчанию
        if not os.path.exists(self._structure_path()):
            defaults = cfgmod.AppConfig()
            for f in cfgmod.PROJECT_FIELDS:
                setattr(self.cfg, f, getattr(defaults, f))
            self.cfg.sources = []        # сервер ПД сканируется автоматически
        cfgmod.save(self.cfg)
        self.actions, self.inventory, self._scanned = [], [], False
        self._load_project()
        self._refresh_projects()
        self._populate()
        ProjectDialog(self, self, self.cfg, on_save=self._on_settings_saved)

    def _edit_project(self):
        """Открыть окно настроек активного проекта."""
        if not self.cfg.project_root:
            messagebox.showinfo("Проект",
                                "Сначала создайте проект или выберите его в списке.")
            return
        ProjectDialog(self, self, self.cfg, on_save=self._on_settings_saved)

    def _on_settings_saved(self):
        cfgmod.save(self.cfg)              # %APPDATA%: список проектов
        self._save_project_settings()     # в project_structure.json на сервере
        self._refresh_projects()
        self._refresh_header()
        self.log("Настройки сохранены (в составе проекта).")

    def _set_busy(self, b: bool):
        self._busy = b
        state = "disabled" if b else "normal"
        for w in (self.btn_scan, self.btn_latest, self.btn_apply_sel, self.btn_apply_all):
            w.config(state=state)


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


class DuplicatesDialog(tk.Toplevel):
    """Поиск дубликатов по CRC32 в выбранной папке. Оставляем по одной копии,
    остальные (по подтверждению) переносим в !ДУБЛИКАТЫ (с откатом)."""

    def __init__(self, parent, cfg, journal_path):
        super().__init__(parent)
        self.cfg = cfg
        self.journal_path = journal_path
        self.groups = []
        self.node_map = {}          # iid -> (group_index, path)  для файлов
        self.group_node = {}        # group_index -> iid
        self.node_group = {}        # iid -> group_index (узел группы)
        self.selected = set()       # индексы выбранных групп
        self.title("Поиск дубликатов по CRC32")
        self.geometry("940x620")
        self.transient(parent)
        self.grab_set()
        base = cfg.project_root or os.path.expanduser("~")
        self.v_folder = tk.StringVar(value=base)
        self.v_skip_archive = tk.BooleanVar(value=False)   # False = учитывать !ARCHIVE (уведомлять)
        self._build()

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Папка:").pack(side="left")
        ttk.Entry(top, textvariable=self.v_folder, width=64).pack(side="left", padx=6)
        ttk.Button(top, text="…", width=3, command=self._pick).pack(side="left")
        ttk.Button(top, text="🔍 Найти дубликаты",
                   command=self._scan).pack(side="left", padx=8)
        ttk.Button(top, text="Выбрать все",
                   command=lambda: self._select_all(True)).pack(side="left")
        ttk.Button(top, text="Снять все",
                   command=lambda: self._select_all(False)).pack(side="left", padx=4)
        ttk.Checkbutton(top, text="Пропускать !ARCHIVE",
                        variable=self.v_skip_archive).pack(side="left", padx=8)
        self.banner = ttk.Label(self, padding=(8, 2), font=("Segoe UI", 10, "bold"),
                                text="Выберите папку и нажмите «Найти дубликаты».")
        self.banner.pack(fill="x")
        ttk.Label(self, padding=(8, 0), foreground="#555",
                  text="Галочка на группе (☑) — обрабатывать её. Клик по файлу — "
                       "сделать его тем, который ОСТАВИТЬ (★, по умолчанию ранний). "
                       "Остальные копии уедут в !УДАЛЕННЫЕ; вне каталога версии — "
                       "встанет в каталог (⇄).").pack(anchor="w")

        mid = ttk.Frame(self, padding=(8, 4))
        mid.pack(fill="both", expand=True)
        cols = ("mark", "date", "size", "path")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings")
        self.tree.heading("#0", text="Группа / файл")
        self.tree.column("#0", width=300, stretch=True)
        for c, t, w in [("mark", "Действие", 150), ("date", "Дата", 84),
                        ("size", "Размер", 80), ("path", "Расположение", 320)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "path"))
        vsb = ttk.Scrollbar(mid, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("keep", foreground="#1e7a34")
        self.tree.tag_configure("version", foreground="#1d6fa5")
        self.tree.tag_configure("replaced", foreground="#c0392b")
        self.tree.tag_configure("delete", foreground="#b9770e")
        self.tree.tag_configure("archive", foreground="#888")
        self.tree.tag_configure("move", foreground="#b9770e")
        self.tree.tag_configure("grp", font=("Segoe UI", 9, "bold"))
        self.tree.tag_configure("grp_note", font=("Segoe UI", 9), foreground="#888")
        self.tree.bind("<Button-1>", self._on_click)

        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        self.lbl = ttk.Label(bot, text="")
        self.lbl.pack(side="left")
        ttk.Button(bot, text="Закрыть", command=self.destroy).pack(side="right")
        self.btn_apply = ttk.Button(bot, text="🧹 Применить (удалить / заменить)",
                                    command=self._apply, state="disabled")
        self.btn_apply.pack(side="right", padx=6)

    def _pick(self):
        d = filedialog.askdirectory(initialdir=self.v_folder.get() or self.cfg.project_root)
        if d:
            self.v_folder.set(d)

    def _scan(self, preselect=True):
        folder = self.v_folder.get().strip()
        if not os.path.isdir(folder):
            messagebox.showwarning("Папка", "Папка не найдена.")
            return
        self._preselect = preselect          # выделять ли все группы после сканирования
        self.banner.config(text="Сканирую…")
        self.btn_apply.config(state="disabled")
        threading.Thread(target=self._scan_worker, args=(folder,), daemon=True).start()

    def _scan_worker(self, folder):
        try:
            groups = dedupmod.find_duplicates(
                self.cfg, folder,
                progress=lambda m: self.after(0, lambda: self.banner.config(text=m)),
                skip_archive=self.v_skip_archive.get())
        except Exception as e:  # noqa: BLE001
            self.after(0, lambda: messagebox.showerror("Ошибка", str(e)))
            return
        self.after(0, lambda: self._fill(groups))

    def _fill(self, groups):
        self.groups = groups
        self.tree.delete(*self.tree.get_children())
        self.node_map = {}
        self.group_node = {}
        self.node_group = {}
        preselect = getattr(self, "_preselect", True)
        # уведомления (только копия в !ARCHIVE) по умолчанию НЕ выбираем — там нет действий
        actionable = {gi for gi, g in enumerate(groups) if not g.get("notify_only")}
        self.selected = set(actionable) if preselect else set()
        root = self.v_folder.get().strip()
        for gi, g in enumerate(groups):
            note = g.get("notify_only")
            n_active = len(g["files"]) - len(g["archive"])
            if note:
                head = f"есть в !ARCHIVE · {os.path.basename(g['keep'])}"
            else:
                head = f"{n_active} копии · CRC {g['crc']:08X}"
                if g.get("has_archive"):
                    head += " · есть в !ARCHIVE"
            mark = (CHECK_ON if gi in self.selected else CHECK_OFF) if not note else "ℹ"
            gid = self.tree.insert(
                "", "end", open=(gi < 20), text=f"{mark} {head}",
                values=("", "", dedupmod.human(g["size"]),
                        ("уведомление" if note else
                         f"лишнего: {dedupmod.human((n_active-1)*g['size'])}")),
                tags=("grp_note" if note else "grp",))
            self.group_node[gi] = gid
            self.node_group[gid] = gi
            for p in g["files"]:
                self._add_file_row(gid, gi, p, root)
        if not groups:
            self.banner.config(text="✓ Дубликатов не найдено.")
            self.lbl.config(text="")
            self.btn_apply.config(state="disabled")
            return
        self.btn_apply.config(state="normal")
        self._update_summary()

    def _update_summary(self):
        sel = [self.groups[i] for i in self.selected]
        extra = sum(len(g["files"]) - len(g["archive"]) - 1 for g in sel)
        wasted = dedupmod.human(dedupmod.wasted_bytes(sel))
        n_note = sum(1 for g in self.groups if g.get("notify_only"))
        n_arch = sum(1 for g in self.groups if g.get("has_archive"))
        tail = (f" · есть в !ARCHIVE: {n_arch}" if n_arch else "")
        self.banner.config(text=f"Групп: {len(self.groups)} (уведомлений: {n_note}) · "
                                f"выбрано: {len(sel)} · лишних копий: {extra} · "
                                f"освободится ~{wasted}{tail}")
        self.lbl.config(text="Отметьте группы галочкой, выберите какую копию оставить. "
                             "Файлы в !ARCHIVE не изменяются.")

    def _select_all(self, on):
        actionable = {gi for gi, g in enumerate(self.groups) if not g.get("notify_only")}
        self.selected = set(actionable) if on else set()
        for gi, gid in self.group_node.items():
            self._set_group_mark(gid, gi)
        self._update_summary()

    def _set_group_mark(self, gid, gi):
        if self.groups[gi].get("notify_only"):
            return                                     # уведомление — без галочки
        txt = self.tree.item(gid, "text")
        mark = CHECK_ON if gi in self.selected else CHECK_OFF
        # заменить первый символ-галочку
        rest = txt[1:].lstrip() if txt[:1] in (CHECK_ON, CHECK_OFF) else txt
        self.tree.item(gid, text=f"{mark} {rest}")

    _ACT_LABEL = {"keep": "★ оставить", "version": "⇄ в каталог версии",
                  "replaced": "🗑 заменяется", "delete": "🗑 удалить",
                  "archive": "🔒 в !ARCHIVE (не трогаем)"}

    def _add_file_row(self, gid, gi, path, root):
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            rel = path
        act = dedupmod.file_action(self.groups[gi], path)
        iid = self.tree.insert(
            gid, "end", text=os.path.basename(path),
            values=(self._ACT_LABEL[act], dedupmod.file_date(path),
                    dedupmod.human(self.groups[gi]["size"]), os.path.dirname(rel) or "."),
            tags=(act,))
        self.node_map[iid] = (gi, path)

    def _refresh_group(self, gi):
        gid = self.group_node[gi]
        for child in self.tree.get_children(gid):
            _cgi, cpath = self.node_map[child]
            act = dedupmod.file_action(self.groups[gi], cpath)
            self.tree.item(child,
                           values=(self._ACT_LABEL[act], self.tree.set(child, "date"),
                                   self.tree.set(child, "size"),
                                   self.tree.set(child, "path")),
                           tags=(act,))

    def _on_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid in self.node_group:                    # клик по группе — переключить галочку
            gi = self.node_group[iid]
            if self.groups[gi].get("notify_only"):
                return                                 # уведомление — переключать нечего
            if gi in self.selected:
                self.selected.discard(gi)
            else:
                self.selected.add(gi)
            self._set_group_mark(iid, gi)
            self._update_summary()
            return
        if iid not in self.node_map:
            return
        gi, path = self.node_map[iid]
        if self.groups[gi]["keep"] == path:
            return
        self.groups[gi]["keep"] = path                # сделать этот файл «оставить»
        self._refresh_group(gi)

    def _apply(self):
        sel_groups = [self.groups[i] for i in sorted(self.selected)]
        if not sel_groups:
            messagebox.showinfo("Дубликаты", "Не отмечено ни одной группы.")
            return
        actions = dedupmod.build_actions(self.cfg, self.v_folder.get().strip(),
                                         sel_groups)
        if not actions:
            messagebox.showinfo("Дубликаты", "Нет копий для обработки.")
            return
        n_repl = sum(1 for g in sel_groups
                     if g.get("slot") and g.get("keep") and g["keep"] != g["slot"])
        extra = (f"\nИз них замен в каталоге версии: {n_repl}." if n_repl else "")
        if not messagebox.askyesno(
                "Подтверждение",
                f"Обработать дубликаты? Действий: {len(actions)}.{extra}\n\n"
                f"Лишние копии переносятся в папку !УДАЛЕННЫЕ (не удаляются "
                f"безвозвратно) — вернуть можно кнопкой «Откатить последнее»."):
            return
        res = executor.execute(actions, self.journal_path, log=lambda m: None)
        ok = sum(1 for r in res if r.ok)
        messagebox.showinfo("Готово",
                            f"Выполнено действий: {ok} из {len(actions)}.\n"
                            f"Лишние копии — в !УДАЛЕННЫЕ. Откат — кнопкой "
                            f"«↩ Откатить последнее» в главном окне.")
        self._scan(preselect=False)                   # пересканировать без автовыделения


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
                "Копируется вся папка версии целиком. Старые версии на сервере "
                "не удаляются."):
            return
        try:
            done, skipped = transfermod.transfer(self.cfg, sel)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Ошибка переноса", str(e))
            return
        msg = f"Перенесено версий: {len(done)}."
        if skipped:
            lines = "\n".join(f"• {oboz}_{date}: {why}" for oboz, date, why in skipped[:8])
            more = f"\n…и ещё {len(skipped) - 8}" if len(skipped) > 8 else ""
            msg += f"\n\nПропущено (уже есть на сервере): {len(skipped)}\n{lines}{more}"
        # записать версии в Excel («Актуальная версия на дату»)
        if self.var_excel.get() and done and self.registry_path:
            updates = {oboz: date for oboz, date in done}
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


if __name__ == "__main__":
    App().mainloop()
