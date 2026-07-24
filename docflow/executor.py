"""Выполнение действий с журналом и возможностью отката.

Каждая операция записывается в журнал (journal.jsonl). Откат последней
операции выполняет обратные действия в обратном порядке.

Рабочие тома (4300_ПД / 1100_ИИ): программа не удаляет файлы безвозвратно —
устаревшие версии только переносятся в !ARCHIVE.
Исключение: обновление комплекта !LATEST и очистка «лишнего» в !LATEST
могут удалять старые версии/копии после подтверждения пользователем
(см. latest.update_latest / remove_paths). Никаких сетевых отправок:
только локальная файловая система.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import datetime as _dt
from dataclasses import dataclass
from typing import Callable, List, Optional

from .planner import (Action, MKSTRUCT, TRANSFER, MKCATALOG, ARCHIVE_FOLDER,
                      MOVE, NORMALIZE, RENAME, ARCHIVE, COPY_LATEST, FLAG)

MOVE_KINDS = (MKCATALOG, ARCHIVE_FOLDER, MOVE, RENAME, ARCHIVE)
COPY_KINDS = (TRANSFER, COPY_LATEST)   # копируем, оригинал не трогаем


@dataclass
class OpResult:
    action: Action
    ok: bool
    message: str = ""
    undo_kind: str = ""   # как откатить: MOVE_BACK / DELETE_COPY / MOVES_BACK
    undo_src: str = ""
    undo_dst: str = ""
    undo_moves: list = None   # для NORMALIZE: список пар [куда, откуда]


def _unique_dst(dst: str) -> str:
    """Если файл с таким именем уже есть — добавляем суффикс, чтобы не затирать."""
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def execute(actions: List[Action],
            journal_path: str,
            log: Optional[Callable[[str], None]] = None,
            stop_on_error: bool = True) -> List[OpResult]:
    """Выполняет отмеченные действия. По умолчанию останавливается на первой
    ошибке (успешные до неё уже в journal). stop_on_error=False — продолжать."""
    log = log or (lambda *_: None)
    results: List[OpResult] = []
    batch_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    for a in actions:
        if a.kind == FLAG or not a.selected:
            continue
        try:
            if a.kind == MKSTRUCT:
                res = _do_mkstruct(a)
            elif a.kind == NORMALIZE:
                res = _do_normalize(a)
            elif a.kind in MOVE_KINDS:
                res = _do_move(a)
            elif a.kind in COPY_KINDS:
                res = _do_copy(a)
            else:
                res = OpResult(a, False, f"неизвестное действие {a.kind}")
        except Exception as e:  # noqa: BLE001
            res = OpResult(a, False, str(e))
        results.append(res)
        log(("✓ " if res.ok else "✗ ") + f"{a.title}: {os.path.basename(a.src)} — {res.message}")
        if not res.ok and stop_on_error:
            log("Остановка: ошибка при выполнении — остальные действия пропущены.")
            break

    _write_journal(journal_path, batch_id, results)
    return results


def _do_move(a: Action) -> OpResult:
    if not os.path.exists(a.src):
        return OpResult(a, False, "исходный файл не найден")
    for d in a.mkdirs:                       # создаём структуру (!EDIT/!PUBLISHED)
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.dirname(a.dst), exist_ok=True)
    dst = _unique_dst(a.dst)
    shutil.move(a.src, dst)
    return OpResult(a, True, f"→ {os.path.basename(dst)}",
                    undo_kind="MOVE_BACK", undo_src=dst, undo_dst=a.src)


def _do_normalize(a: Action) -> OpResult:
    """Разложить файлы нестандартной папки (a.src) по !EDIT/!PUBLISHED каталога
    версии (a.dst). Имена файлов сохраняются. Пустые папки убираются."""
    folder, catalog = a.src, a.dst
    if not os.path.isdir(folder):
        return OpResult(a, True, "папка уже отсутствует")
    pub_dir = a.payload.get("published_dir", "!PUBLISHED")
    edit_dir = a.payload.get("edit_dir", "!EDIT")
    pub_ext = {e.lower() for e in a.payload.get("published_extensions", [])}
    moves: List[list] = []                       # [куда, откуда] для отката
    for r, _dirs, files in os.walk(folder):
        for fn in files:
            src = os.path.join(r, fn)
            ext = os.path.splitext(fn)[1].lower()
            sub = pub_dir if ext in pub_ext else edit_dir
            dst_dir = os.path.join(catalog, sub)
            os.makedirs(dst_dir, exist_ok=True)
            dst = _unique_dst(os.path.join(dst_dir, fn))
            shutil.move(src, dst)
            moves.append([dst, src])
    # убрать опустевшие папки (снизу вверх)
    for r, _dirs, _files in os.walk(folder, topdown=False):
        try:
            if not os.listdir(r):
                os.rmdir(r)
        except OSError:
            pass
    if not moves:
        return OpResult(a, True, "файлов не было")
    return OpResult(a, True, f"разложено файлов: {len(moves)}",
                    undo_kind="MOVES_BACK", undo_moves=moves)


def _dirs_to_create(d: str) -> List[str]:
    """Цепочка несуществующих папок (от верхней к нижней), которые создаст makedirs."""
    chain = []
    cur = d
    while cur and not os.path.exists(cur):
        chain.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    chain.reverse()                       # сверху вниз
    return chain


def _do_mkstruct(a: Action) -> OpResult:
    created: List[str] = []
    for d in a.mkdirs:
        for c in _dirs_to_create(d):      # учитываем и родительские папки
            if c not in created:
                created.append(c)
        os.makedirs(d, exist_ok=True)
    if not created:
        return OpResult(a, True, "структура уже есть")
    return OpResult(a, True, "создано: " + ", ".join(os.path.basename(d) for d in created),
                    undo_kind="RMDIR", undo_src=os.pathsep.join(created), undo_dst="")


def _do_copy(a: Action) -> OpResult:
    if not os.path.exists(a.src):
        return OpResult(a, False, "исходный файл не найден")
    for d in a.mkdirs:
        os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.dirname(a.dst), exist_ok=True)
    dst = _unique_dst(a.dst)
    shutil.copy2(a.src, dst)
    return OpResult(a, True, f"скопирован → {os.path.basename(dst)}",
                    undo_kind="DELETE_COPY", undo_src=dst, undo_dst="")


# ---------- журнал и откат ----------

def _atomic_write_text(path: str, text: str) -> None:
    """Пишет файл атомарно: temp рядом → os.replace (на Windows тоже)."""
    from .config import unhide_file
    unhide_file(path)
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".docflow_jnl_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_journal(journal_path: str, batch_id: str, results: List[OpResult]) -> None:
    ok = [r for r in results if r.ok and r.undo_kind]
    if not ok:
        return
    entry = {
        "batch": batch_id,
        "time": _dt.datetime.now().isoformat(timespec="seconds"),
        "ops": [{"kind": r.action.kind, "undo_kind": r.undo_kind,
                 "undo_src": r.undo_src, "undo_dst": r.undo_dst,
                 "undo_moves": r.undo_moves or [],
                 "title": r.action.title} for r in ok],
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    # дописываем: читаем + атомарная перезапись (безопасно при сбое mid-write)
    prev = ""
    if os.path.exists(journal_path):
        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                prev = f.read()
        except OSError:
            prev = ""
    _atomic_write_text(journal_path, prev + line)


def read_journal(journal_path: str) -> List[dict]:
    if not os.path.exists(journal_path):
        return []
    out = []
    with open(journal_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def undo_last(journal_path: str, log: Optional[Callable[[str], None]] = None) -> int:
    """Откатывает последнюю операцию из журнала. Возвращает число откатанных действий."""
    log = log or (lambda *_: None)
    batches = read_journal(journal_path)
    if not batches:
        log("Нет операций для отката.")
        return 0
    last = batches[-1]
    count = 0
    for op in reversed(last["ops"]):
        try:
            if op["undo_kind"] == "MOVE_BACK":
                if os.path.exists(op["undo_src"]):
                    os.makedirs(os.path.dirname(op["undo_dst"]), exist_ok=True)
                    shutil.move(op["undo_src"], _unique_dst(op["undo_dst"]))
                    count += 1
            elif op["undo_kind"] == "DELETE_COPY":
                if os.path.exists(op["undo_src"]):
                    os.remove(op["undo_src"])
                    count += 1
            elif op["undo_kind"] == "MOVES_BACK":
                for dst, src in reversed(op.get("undo_moves", [])):
                    if os.path.exists(dst):
                        os.makedirs(os.path.dirname(src), exist_ok=True)
                        shutil.move(dst, _unique_dst(src))
                        count += 1
            elif op["undo_kind"] == "RMDIR":
                for d in reversed(op["undo_src"].split(os.pathsep)):
                    if os.path.isdir(d) and not os.listdir(d):
                        os.rmdir(d)
                        count += 1
        except Exception as e:  # noqa: BLE001
            log(f"✗ откат: {e}")
    # удаляем последнюю запись журнала (атомарная перезапись)
    remaining = batches[:-1]
    text = "".join(json.dumps(b, ensure_ascii=False) + "\n" for b in remaining)
    _atomic_write_text(journal_path, text)
    log(f"Откат операции от {last['time']}: возвращено {count} файлов.")
    return count
