# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from docflow import customer_package as package
from docflow import signing


class CustomerPackageDialog(tk.Toplevel):
    def __init__(self, parent, cfg, entries=None):
        super().__init__(parent)
        self.cfg = cfg
        self.entries = entries or []
        self.title("Комплект для передачи заказчику")
        self.geometry("760x650")
        self.minsize(620, 480)
        self.transient(parent)
        self.grab_set()
        self.v_target = tk.StringVar(value=os.path.join(
            cfg.project_root or os.path.expanduser("~"),
            "8300_ВыпускПД", "Комплект_заказчику"))
        self._states = {}
        self._children = {}
        self._items = {}
        self._paths = {}
        self._build()
        self._load_tree()

    def _build(self):
        ttk.Label(
            self, padding=10, wraplength=720, justify="left",
            text=("Выберите разделы из !LATEST. Программа сохранит их структуру, "
                  "скопирует все файлы и создаст Excel-реестр с CRC32 и ссылками."),
        ).pack(fill="x")
        target = ttk.LabelFrame(self, text="1. Папка комплекта", padding=8)
        target.pack(fill="x", padx=10, pady=4)
        ttk.Entry(target, textvariable=self.v_target).pack(
            side="left", fill="x", expand=True)
        ttk.Button(target, text="Обзор…", command=self._pick).pack(
            side="left", padx=(6, 0))

        choose = ttk.LabelFrame(self, text="2. Разделы и тома", padding=8)
        choose.pack(fill="both", expand=True, padx=10, pady=4)
        tools = ttk.Frame(choose)
        tools.pack(fill="x", pady=(0, 5))
        ttk.Button(tools, text="Выбрать все",
                   command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(tools, text="Снять все",
                   command=lambda: self._set_all(False)).pack(side="left", padx=5)
        self.lbl = ttk.Label(tools, foreground="#666")
        self.lbl.pack(side="right")
        outer = ttk.Frame(choose)
        outer.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            outer, show="tree", selectmode="browse", padding=4)
        yscroll = ttk.Scrollbar(
            outer, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(
            outer, orient="horizontal", command=self.tree.xview)
        self.tree.configure(
            yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.column("#0", width=690, minwidth=380, stretch=True)
        self.tree.tag_configure("mixed", foreground="#666666")
        self.tree.bind("<Button-1>", self._tree_click, add=True)
        self.tree.bind("<space>", self._tree_space, add=True)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        action = ttk.Frame(self, padding=10)
        action.pack(fill="x")
        self.progress = ttk.Progressbar(action, mode="indeterminate", length=180)
        self.progress.pack(side="left")
        self.status = ttk.Label(action, text="")
        self.status.pack(side="left", padx=8)
        self.btn = ttk.Button(
            action, text="📦 Сформировать комплект", command=self._start)
        self.btn.pack(side="right")
        ttk.Button(action, text="Закрыть", command=self.destroy).pack(
            side="right", padx=6)

    def _pick(self):
        value = filedialog.askdirectory(
            initialdir=self.v_target.get() or self.cfg.project_root)
        if value:
            self.v_target.set(value)

    def _load_tree(self):
        nodes = signing.list_signing_folder_tree(self.cfg.latest_abs, self.cfg)
        paths = {path for path, _depth in nodes}
        for path, _depth in nodes:
            parent = os.path.dirname(path)
            if parent in paths:
                self._children.setdefault(parent, []).append(path)
            self._children.setdefault(path, [])
            self._states[path] = True
        for path, depth in nodes:
            parent = os.path.dirname(path)
            parent_item = self._items.get(parent, "")
            item = self.tree.insert(
                parent_item, "end", text="", open=False)
            self._items[path] = item
            self._paths[item] = path
            self._refresh_item(path)
        self._summary()

    def _selected(self):
        return [path for path, state in self._states.items()
                if state is True and not self._children.get(path)]

    def _set_all(self, value):
        for path in self._states:
            self._states[path] = value
            self._refresh_item(path)
        self._summary()

    def _set_branch(self, path, value):
        self._states[path] = value
        self._refresh_item(path)
        def descendants(parent):
            for child in self._children.get(parent, []):
                self._states[child] = value
                self._refresh_item(child)
                descendants(child)
        descendants(path)
        parent = os.path.dirname(path)
        while parent in self._states:
            child_states = [self._states[c] for c in self._children[parent]]
            if all(state is True for state in child_states):
                self._states[parent] = True
            elif all(state is False for state in child_states):
                self._states[parent] = False
            else:
                self._states[parent] = None
            self._refresh_item(parent)
            parent = os.path.dirname(parent)
        self._summary()

    def _refresh_item(self, path):
        state = self._states[path]
        mark = "☑" if state is True else ("☐" if state is False else "▣")
        tags = ("mixed",) if state is None else ()
        self.tree.item(
            self._items[path],
            text=f"{mark}  {os.path.basename(path)}",
            tags=tags)

    def _toggle_item(self, item):
        path = self._paths.get(item)
        if path:
            self._set_branch(path, self._states[path] is not True)

    def _tree_click(self, event):
        item = self.tree.identify_row(event.y)
        element = self.tree.identify_element(event.x, event.y)
        if item and "indicator" not in element:
            self.tree.after_idle(lambda: self._toggle_item(item))
            return "break"
        return None

    def _tree_space(self, _event):
        item = self.tree.focus()
        if item:
            self._toggle_item(item)
        return "break"

    def _summary(self):
        leaves = sum(not children for children in self._children.values())
        self.lbl.config(text=f"выбрано: {len(self._selected())} из {leaves}")

    def _start(self):
        target = self.v_target.get().strip()
        selected = self._selected()
        if not os.path.isdir(self.cfg.latest_abs):
            messagebox.showwarning("!LATEST", "Папка !LATEST не найдена.")
            return
        if not selected:
            messagebox.showwarning("Разделы", "Не выбран ни один раздел или том.")
            return
        if os.path.isdir(target) and os.listdir(target):
            if not messagebox.askyesno(
                    "Папка не пустая",
                    "В папке назначения уже есть файлы. Обновить их и продолжить?"):
                return
        self.btn.config(state="disabled")
        self.progress.start(12)
        self.status.config(text="Формирование и расчёт CRC32…")
        threading.Thread(
            target=self._worker, args=(target, selected), daemon=True).start()

    def _worker(self, target, selected):
        try:
            result = package.build_package(
                self.cfg, self.cfg.latest_abs, target, selected,
                entries=self.entries)
            self.after(0, lambda r=result: self._done(r))
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda m=str(exc): self._failed(m))

    def _done(self, result):
        self.progress.stop()
        self.btn.config(state="normal")
        self.status.config(text=f"Готово: {result['files']} файлов")
        messagebox.showinfo(
            "Комплект сформирован",
            f"Скопировано файлов: {result['files']}\n"
            f"Реестр CRC32:\n{result['registry']}")

    def _failed(self, message):
        self.progress.stop()
        self.btn.config(state="normal")
        self.status.config(text="Ошибка")
        messagebox.showerror("Комплект заказчику", message)
