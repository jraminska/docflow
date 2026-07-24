# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from docflow import dedup as dedupmod
from docflow import executor

from .constants import CHECK_ON, CHECK_OFF


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
