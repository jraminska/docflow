"""Поиск дубликатов файлов по содержимому (CRC32) и наведение порядка.

Задача: в захламлённой папке (напр. субподрядной, где всё свалено вместе)
найти одинаковые по содержимому файлы и оставить по одной копии.

Сравнение точное: сначала группируем по размеру, затем внутри одинакового
размера сверяем CRC32 — совпал размер и CRC32 ⇒ файлы идентичны (имена и даты
могут отличаться).

Действия над лишними копиями (ничего не удаляется безвозвратно):
  • УДАЛИТЬ         — перенести копию в папку !УДАЛЕННЫЕ (с откатом).
  • ЗАМЕНИТЬ В ВЕРСИИ — если идентичная копия лежит ВНЕ каталога версии, а в
    каталоге версии {обозначение}_{дата} лежит та же (по CRC), оставляем более
    РАННЮЮ копию, помещаем её в каталог версии (с нужным именем), остальные
    (более поздние) — в !УДАЛЕННЫЕ.
Оставляемая копия по умолчанию — самая ранняя по дате.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Callable, List, Optional

from . import planner
from .config import AppConfig
from .planner import Action, MOVE
from .transfer import _crc32

REMOVED = "!УДАЛЕННЫЕ"

# «Плохое» расположение для копии, которую лучше НЕ оставлять при равных датах.
_BAD_MARKERS = ("!archive", "!удаленные", "!дубликаты", "!work", "_draft", "draft",
                "tmp", "temp", "копия", "- копия", "copy", " (2)", " (3)")


def human(size: float) -> str:
    """Человекочитаемый размер: 1536 → «1.5 КБ»."""
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def file_date(path: str) -> str:
    """Дата и время изменения файла «дд.мм.гггг чч:мм» (для показа в списке).
    Время важно: при одинаковой дате оставляемая копия выбирается по времени."""
    t = mtime(path)
    return _dt.datetime.fromtimestamp(t).strftime("%d.%m.%Y %H:%M") if t else "—"


def _in_version_catalog(path: str) -> bool:
    """Файл лежит внутри каталога версии {обозначение}_{дата}?"""
    for part in re.split(r"[\\/]+", path):
        if planner.cat_match(part)[0]:
            return True
    return False


def version_slot(files: List[str]) -> Optional[str]:
    """Копия, лежащая в каталоге версии (её место/имя — «правильные»)."""
    for p in files:
        if _in_version_catalog(p):
            return p
    return None


def _default_keep(files: List[str]) -> str:
    """Какую копию оставить по умолчанию: самую РАННЮЮ по дате/времени. При
    ОДИНАКОВОМ времени приоритет у копии, которая уже в каталоге версии
    (её не нужно двигать), затем — «чистое» место, ближе к корню, короче имя."""
    def score(p: str):
        low = p.lower()
        bad = any(m in low for m in _BAD_MARKERS)
        in_cat = _in_version_catalog(p)
        return (mtime(p), 0 if in_cat else 1, 1 if bad else 0,
                p.count(os.sep), len(os.path.basename(p)))
    return min(files, key=score)


def _is_archive(path: str, cfg: AppConfig) -> bool:
    """Файл лежит внутри папки !ARCHIVE (на любом уровне)?"""
    arch = (cfg.archive_name or "!ARCHIVE").lower()
    return any(part.lower() == arch for part in re.split(r"[\\/]+", path))


def _active(g: dict) -> List[str]:
    """Файлы группы вне !ARCHIVE (с ними и работаем)."""
    return [f for f in g["files"] if f not in g["archive"]]


def find_duplicates(cfg: AppConfig, root: str,
                    progress: Optional[Callable[[str], None]] = None,
                    skip_archive: bool = False) -> List[dict]:
    """Группы одинаковых по содержимому файлов (len>1). Каждая:
       {crc, size, files, archive:set, keep, slot, notify_only, has_archive}.
    Файлы из !ARCHIVE НЕ трогаются — только уведомление «уже есть в !ARCHIVE».
    skip_archive=True — вообще не заходить в !ARCHIVE (без уведомлений)."""
    progress = progress or (lambda *_: None)
    if not root or not os.path.isdir(root):
        return []
    rem = REMOVED.lower()
    arch = (cfg.archive_name or "!ARCHIVE").lower()
    by_size: dict = {}
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d.lower() != rem and not planner._ignored(d, cfg)
                  and not (skip_archive and d.lower() == arch)]
        for fn in fns:
            if planner._ignored(fn, cfg):
                continue
            full = os.path.join(dp, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            by_size.setdefault(size, []).append(full)
            n += 1
    progress(f"Файлов просмотрено: {n}. Сверяю CRC32…")
    groups: List[dict] = []
    for size, paths in by_size.items():
        if len(paths) < 2 or size == 0:
            continue
        by_crc: dict = {}
        for p in paths:
            c = _crc32(p)
            if c is None:
                continue
            by_crc.setdefault(c, []).append(p)
        for c, ps in by_crc.items():
            if len(ps) < 2:
                continue
            fs = sorted(ps)
            archive = {f for f in fs if _is_archive(f, cfg)}
            active = [f for f in fs if f not in archive]
            if not active:
                continue                     # дубли только внутри !ARCHIVE — не трогаем
            groups.append({
                "crc": c, "size": size, "files": fs, "archive": archive,
                "keep": _default_keep(active), "slot": version_slot(active),
                "notify_only": len(active) == 1 and bool(archive),
                "has_archive": bool(archive)})
    # сначала те, где реально есть что убрать; потом уведомления по !ARCHIVE
    groups.sort(key=lambda g: (len(_active(g)) - 1) * g["size"], reverse=True)
    return groups


def wasted_bytes(groups: List[dict]) -> int:
    """Объём лишних копий ВНЕ архива (то, что реально освободится)."""
    return sum((len(_active(g)) - 1) * g["size"] for g in groups)


def file_action(g: dict, path: str) -> str:
    """Что произойдёт с файлом (для показа):
       archive — в !ARCHIVE, не трогаем; keep/version/replaced/delete — как раньше."""
    if path in g["archive"]:
        return "archive"
    keep, slot = g.get("keep"), g.get("slot")
    if g.get("notify_only"):
        return "keep"                        # единственная актуальная копия — остаётся
    to_version = bool(slot and keep and keep != slot)
    if path == keep:
        return "version" if to_version else "keep"
    if to_version and path == slot:
        return "replaced"            # старый файл в каталоге версии — уедет
    return "delete"


def build_actions(cfg: AppConfig, root: str, groups: List[dict],
                  removed: str = REMOVED) -> List[Action]:
    """Действия MOVE (для executor'а — с журналом/откатом):
       • «удалить» → root/!УДАЛЕННЫЕ/ (плоско);
       • «заменить в версии» → освободить слот в каталоге версии и поставить
         туда оставляемую (раннюю) копию под именем слота.
    Файлы из !ARCHIVE НЕ трогаются; для «только в архиве» действий нет."""
    actions: List[Action] = []
    rroot = os.path.join(root, removed)

    def _to_removed(p: str, reason: str):
        # структуру в !УДАЛЕННЫЕ не держим — кладём плоско (совпадения имён
        # executor разведёт суффиксом « (2)»); откат вернёт на исходное место
        dst = os.path.join(rroot, os.path.basename(p))
        actions.append(Action(MOVE, p, dst, selected=True, reason=reason,
                              mkdirs=[rroot], node=os.path.dirname(p), group=removed))

    for g in groups:
        active = _active(g)
        if len(active) < 2 and not (g.get("slot") and g.get("keep") != g.get("slot")):
            continue                         # уведомление по !ARCHIVE — без действий
        keep, slot = g.get("keep"), g.get("slot")
        to_version = bool(slot and keep and keep != slot)
        if to_version:
            # 1) освобождаем слот каталога версии (старый файл → в удалённые)
            _to_removed(slot, f"заменяется более ранней копией (CRC {g['crc']:08X})")
            # 2) оставляемую (раннюю) копию кладём в слот под именем слота
            dst = os.path.join(os.path.dirname(slot), os.path.basename(slot))
            actions.append(Action(
                MOVE, keep, dst, selected=True,
                reason=f"заменить в каталоге версии (оставляем раннюю копию "
                       f"{os.path.basename(keep)})",
                mkdirs=[os.path.dirname(slot)], node=os.path.dirname(slot),
                group=os.path.basename(os.path.dirname(slot))))
            drop = {keep, slot}
        else:
            drop = {keep}
        for p in active:                     # только вне !ARCHIVE
            if p in drop:
                continue
            _to_removed(p, f"дубликат (CRC {g['crc']:08X}) — оставляем "
                           f"{os.path.basename(keep)}")
    return actions
