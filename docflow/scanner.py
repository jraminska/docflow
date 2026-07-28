"""Инвентаризация папок и отслеживание изменений.

Снимок (snapshot) хранится в state.json рядом с программой. При каждом
сканировании мы строим новый снимок и сравниваем с предыдущим, получая
списки добавленных / изменённых / удалённых / перемещённых файлов.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
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


def scan_source_incremental(name: str, root: str, category: str,
                            ignore: List[str], previous: Dict[str, dict],
                            previous_dirs: Dict[str, int],
                            use_hash: bool = False,
                            skip_dirs: Optional[List[str]] = None):
    """Быстрый обход: файлы неизменившихся каталогов берутся из общего индекса.

    Каталоги всё равно перечисляются, чтобы заметить добавление/удаление папок.
    Полное чтение метаданных файлов выполняется только в каталогах, чей mtime
    изменился. Возвращает (records, directory_mtimes, reused_count).
    """
    skip_dirs = skip_dirs or []
    if not root or not os.path.isdir(root):
        return [], {}, 0
    by_parent: Dict[str, List[dict]] = {}
    root_norm = os.path.normcase(os.path.normpath(root))
    for old in previous.values():
        path = os.path.normcase(os.path.normpath(old.get("path", "")))
        try:
            if os.path.commonpath((root_norm, path)) != root_norm:
                continue
        except ValueError:
            continue
        by_parent.setdefault(os.path.dirname(path), []).append(old)

    out: List[FileRec] = []
    dir_mtimes: Dict[str, int] = {}
    reused = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in skip_dirs and not _ignored(d, ignore)]
        key = os.path.normcase(os.path.normpath(dirpath))
        try:
            mtime_ns = os.stat(dirpath).st_mtime_ns
        except OSError:
            continue
        dir_mtimes[key] = mtime_ns
        if previous_dirs.get(key) == mtime_ns:
            current_names = {n.casefold() for n in filenames if not _ignored(n, ignore)}
            cached = [r for r in by_parent.get(key, [])
                      if str(r.get("name", "")).casefold() in current_names]
            out.extend(FileRec(**{k: r.get(k) for k in FileRec.__dataclass_fields__})
                       for r in cached)
            reused += len(cached)
            continue
        for fn in filenames:
            if _ignored(fn, ignore):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out.append(FileRec(
                path=full, rel=os.path.relpath(full, root), source=name,
                category=category, size=st.st_size, mtime=round(st.st_mtime, 2),
                name=fn, sha1=sha1_of(full) if use_hash else None))
    return out, dir_mtimes, reused


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
    files, _directories = load_snapshot_index(path)
    return files


def load_snapshot_index(path: str):
    """Полный общий индекс: ({path: file}, {directory: mtime_ns})."""
    if not os.path.exists(path):
        return {}, {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        files = {r["path"]: r for r in data.get("files", [])}
        dirs = {str(k): int(v) for k, v in data.get("directories", {}).items()}
        return files, dirs
    except (OSError, ValueError, TypeError):
        return {}, {}


def save_snapshot(path: str, files: List[FileRec],
                  directories: Optional[Dict[str, int]] = None) -> None:
    import datetime
    from .config import hide_file, unhide_file
    data = {"version": 2,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "files": [asdict(r) for r in files],
            "directories": directories or {}}
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".docflow_state_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        unhide_file(path)
        os.replace(tmp, path)
        hide_file(path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
