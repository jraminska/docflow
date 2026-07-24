# -*- coding: utf-8 -*-
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
from docflow import signing as signmod
from docflow import __version__, __build_date__
from docflow.naming import Matcher

from .constants import (
    APP_TITLE, CHECK_ON, CHECK_OFF, KIND_ORDER,
    DOT_OK, DOT_TODO, DOT_ATTENTION, DOT_UNKNOWN,
    STATUS_TAG, KIND_DOT, app_dir,
)
from .duplicates import DuplicatesDialog
from .latest import LatestDialog
from .project import ProjectDialog
from .signing import SigningDialog
from .transfer_dialog import TransferDialog


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
        return self.cfg.project_root or app_dir()

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
