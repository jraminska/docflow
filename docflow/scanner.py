"""Инвентаризация папок и отслеживание изменений.

Снимок (snapshot) хранится в state.json рядом с программой. При каждом
сканировании мы строим новый снимок и сравниваем с предыдущим, получая
списки добавленных / изменённых / удалённых / перемещённых файлов.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from .config import is_ignored


@dataclass
class FileRec:
    path: str          # полный путь
    rel: str           # путь относительно источника
    source: str        # имя источника
    category: str      # ПД / ИИ
    size: int
    mtime: float
    name: str
    sha1: Optional[str] = None


def _ignored(name: str, patterns: List[str]) -> bool:
    return is_ignored(name, patterns)


def sha1_of(path: str, limit_mb: int = 200) -> Optional[str]:
    try:
        if os.path.getsize(path) > limit_mb * 1024 * 1024:
            return None
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def scan_source(name: str, root: str, category: str,
                ignore: List[str], use_hash: bool = False,
                skip_dirs: Optional[List[str]] = None) -> List[FileRec]:
    """Обходит одну папку-источник. skip_dirs — служебные подпапки не для версий."""
    skip_dirs = skip_dirs or []
    out: List[FileRec] = []
    if not root or not os.path.isdir(root):
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        # не заходим в служебные подпапки и в папки по игнор-маскам (*_DRAFT* и т.п.)
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and not _ignored(d, ignore)]
        for fn in filenames:
            if _ignored(fn, ignore):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rec = FileRec(
                path=full,
                rel=os.path.relpath(full, root),
                source=name,
                category=category,
                size=st.st_size,
                mtime=round(st.st_mtime, 2),
                name=fn,
                sha1=sha1_of(full) if use_hash else None,
            )
            out.append(rec)
    return out


@dataclass
class Changes:
    added: List[FileRec] = field(default_factory=list)
    modified: List[FileRec] = field(default_factory=list)
    removed: List[dict] = field(default_factory=list)
    moved: List[tuple] = field(default_factory=list)   # (старый rel/путь, новый FileRec)
    unchanged: int = 0

    def total(self) -> int:
        return len(self.added) + len(self.modified) + len(self.removed) + len(self.moved)


def diff(prev: Dict[str, dict], current: List[FileRec], use_hash: bool) -> Changes:
    """prev — словарь {path: rec} из прошлого снимка."""
    ch = Changes()
    cur_by_path = {r.path: r for r in current}

    for r in current:
        old = prev.get(r.path)
        if old is None:
            ch.added.append(r)
        elif r.size != old.get("size") or r.mtime != old.get("mtime") or \
                (use_hash and r.sha1 and old.get("sha1") and r.sha1 != old["sha1"]):
            ch.modified.append(r)
        else:
            ch.unchanged += 1

    for path, old in prev.items():
        if path not in cur_by_path:
            ch.removed.append(old)

    # переименование/перемещение: совпадение по хэшу (если включён) или по имени+размеру
    if ch.added and ch.removed:
        rem_index = {}
        for old in ch.removed:
            k = old.get("sha1") if use_hash and old.get("sha1") else f"{old.get('name')}|{old.get('size')}"
            rem_index.setdefault(k, []).append(old)
        still_added, moved = [], []
        for r in ch.added:
            k = r.sha1 if use_hash and r.sha1 else f"{r.name}|{r.size}"
            if rem_index.get(k):
                old = rem_index[k].pop(0)
                moved.append((old.get("path"), r))
            else:
                still_added.append(r)
        ch.added = still_added
        ch.moved = moved
        ch.removed = [o for lst in rem_index.values() for o in lst]
    return ch


# ---------- снимок ----------

def load_snapshot(path: str) -> Dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {r["path"]: r for r in data.get("files", [])}
    except (OSError, ValueError):
        return {}


def save_snapshot(path: str, files: List[FileRec]) -> None:
    import datetime
    from .config import unhide_file
    data = {"saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": [asdict(r) for r in files]}
    unhide_file(path)                    # на случай, если файл был скрыт прошлой версией
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
