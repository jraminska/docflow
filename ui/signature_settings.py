from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from docflow import structure as structmod


class SignatureSettingsDialog(tk.Toplevel):
    """Редактор обязательных подписантов в составе проекта."""

    LEVELS = {
        "none": "Не проверять",
        "warn": "Предупреждение",
        "error": "Ошибка",
    }

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.structure = app.structure
        self._current = None
        self.title("Подписанты ЭЦП по документам")
        self.geometry("960x620")
        self.minsize(760, 480)
        self.transient(parent)
        self.grab_set()
        self._build()
        self._fill()

    def _build(self):
        ttk.Label(
            self,
            text=("Выберите документ, укажите обязательных подписантов — по одному "
                  "в строке — и важность отсутствующей подписи."),
            wraplength=900, justify="left",
        ).pack(fill="x", padx=10, pady=(10, 6))
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10)

        left = ttk.Frame(body)
        self.tree = ttk.Treeview(
            left, columns=("signers", "level"), show="tree headings",
            selectmode="browse")
        self.tree.heading("#0", text="Раздел / документ")
        self.tree.heading("signers", text="Подписанты")
        self.tree.heading("level", text="Режим")
        self.tree.column("#0", width=390)
        self.tree.column("signers", width=230)
        self.tree.column("level", width=120, anchor="center")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        body.add(left, weight=2)

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        self.lbl_doc = ttk.Label(
            right, text="Выберите документ", font=("Segoe UI", 10, "bold"),
            wraplength=310, justify="left")
        self.lbl_doc.pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text="Обязательные подписанты:").pack(anchor="w")
        self.txt = tk.Text(right, height=12, width=36, wrap="word")
        self.txt.pack(fill="both", expand=True, pady=(3, 8))
        ttk.Label(
            right, foreground="#666", wraplength=310, justify="left",
            text=("По одному человеку в строке. Можно указать фамилию или полное "
                  "ФИО. Пустой список отключает проверку для документа."),
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(right, text="Если подписи нет:").pack(anchor="w")
        self.v_level = tk.StringVar(value="warn")
        for value, title in self.LEVELS.items():
            ttk.Radiobutton(
                right, text=title, value=value, variable=self.v_level
            ).pack(anchor="w")
        ttk.Button(
            right, text="Применить к выбранному документу",
            command=self._apply_current,
        ).pack(fill="x", pady=(12, 0))
        body.add(right, weight=1)

        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Сохранить состав", command=self._save).pack(side="right")
        ttk.Button(bar, text="Закрыть", command=self.destroy).pack(side="right", padx=6)

    @staticmethod
    def _signer_name(value):
        if isinstance(value, dict):
            return value.get("name") or value.get("full_name") or value.get("surname") or ""
        return str(value or "")

    def _fill(self):
        for area_key in ("sections", "ii_sections"):
            for section in self.structure.get(area_key, []):
                area = "ПД" if area_key == "sections" else "ИИ"
                title = section.get("code") or section.get("razdel") or area
                sec_id = self.tree.insert("", "end", text=f"{area}: {title}", open=True)
                for doc in section.get("documents", []):
                    signers = ", ".join(
                        self._signer_name(x) for x in doc.get("signers", []))
                    level = str(doc.get("signature_level", "warn") or "warn").lower()
                    item = self.tree.insert(
                        sec_id, "end",
                        text=doc.get("oboznachenie") or doc.get("name") or "Документ",
                        values=(signers, self.LEVELS.get(level, self.LEVELS["warn"])))
                    setattr(self, f"_doc_{item}", doc)

    def _select(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        doc = getattr(self, f"_doc_{item}", None)
        if doc is None:
            return
        self._store_current()
        self._current = (item, doc)
        self.lbl_doc.config(
            text=doc.get("oboznachenie") or doc.get("name") or "Документ")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "\n".join(
            self._signer_name(x) for x in doc.get("signers", [])))
        level = str(doc.get("signature_level", "warn") or "warn").lower()
        self.v_level.set(level if level in self.LEVELS else "warn")

    def _store_current(self):
        if self._current is None:
            return
        item, doc = self._current
        signers = [x.strip() for x in self.txt.get("1.0", "end").splitlines()
                   if x.strip()]
        level = self.v_level.get()
        doc["signers"] = signers
        doc["signature_level"] = level
        self.tree.item(
            item, values=(", ".join(signers), self.LEVELS.get(level, level)))

    def _apply_current(self):
        if self._current is None:
            messagebox.showinfo("Подписанты", "Сначала выберите документ.")
            return
        self._store_current()

    def _save(self):
        self._store_current()
        structmod.save(self.structure, self.app._structure_path())
        self.app.entries = structmod.to_entries(self.structure)
        self.app._populate()
        messagebox.showinfo(
            "Подписанты",
            "Подписанты и режимы проверки сохранены в составе проекта.")
