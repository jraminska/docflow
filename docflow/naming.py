"""Распознавание и формирование имён файлов по эталонному реестру.

Сопоставление строгое: имя файла должно НАЧИНАТЬСЯ с обозначения документа
(после нормализации префикса шифра объекта), а сразу за обозначением должен
идти разделитель «_» или конец имени / расширение. Это исключает ложные
совпадения с рабочими файлами вида «523-ПИР-24-ПЗУ1-Г.1.dwg».
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass
from typing import List, Optional

from .registry import RegEntry, DATE_TOKEN, strip_proj_prefix


RE_DATE6 = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")
RE_IUL = re.compile(r"(?:_УЛ\b|\bИУЛ\b|_ИУЛ\b)", re.IGNORECASE)


def _norm_core(s: str) -> str:
    """Нормализуем для сравнения: убираем префикс объекта, пробелы, нижний регистр."""
    return re.sub(r"\s+", "", strip_proj_prefix(s)).lower()


@dataclass
class Recognized:
    matched: bool
    entry: Optional[RegEntry] = None
    is_iul: bool = False
    date6: Optional[str] = None
    canonical: Optional[str] = None
    reason: str = ""


class Matcher:
    """Хранит записи реестра, отсортированные по длине ядра (длинные раньше)."""

    def __init__(self, entries: List[RegEntry]):
        self.entries = list(entries)
        self._items = sorted(
            ((e, _norm_core(e.oboznachenie)) for e in entries),
            key=lambda t: -len(t[1]),
        )
        self._by_core = {core: e for e, core in self._items}

    def by_core(self, core_norm: str) -> Optional[RegEntry]:
        """Точный поиск записи по нормализованному ядру обозначения."""
        return self._by_core.get(core_norm)

    def find(self, stem: str) -> Optional[RegEntry]:
        norm = re.sub(r"\s+", "", strip_proj_prefix(stem)).lower()
        for e, core in self._items:
            if not core or not norm.startswith(core):
                continue
            tail = norm[len(core):]
            # сразу за обозначением допустим только разделитель или конец
            if tail == "" or tail[0] in "_.":
                return e
        return None


def detect_date6(text: str) -> Optional[str]:
    best = None
    for m in RE_DATE6.finditer(text):
        mm, dd = int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            best = m.group(1) + m.group(2) + m.group(3)
    return best


def mtime_date6(path: str) -> str:
    return _dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%y%m%d")


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


RE_TAIL_DATE = re.compile(r"_?(\d{6})$")


def _middle_matches(stem: str, entry: RegEntry) -> bool:
    """Проверяет, что часть имени после шифра соответствует эталонной
    («Раздел ПД №7»). Отсекает составные/рабочие файлы того же раздела."""
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    nstem = _nospace(strip_proj_prefix(stem))
    if not nstem.startswith(core):
        return False
    rest = nstem[len(core):]
    rest = re.sub(r"_?ул$", "", rest)        # убрать маркер ИУЛа
    rest = RE_TAIL_DATE.sub("", rest)        # убрать дату в конце
    rest = rest.strip("_")
    expected = _nospace(entry.middle())
    return rest == expected


def recognize(filename: str, full_path: str, matcher: Matcher,
              date_from_mtime: bool = True) -> Recognized:
    stem, ext = os.path.splitext(filename)
    ext = ext.lower()

    entry = matcher.find(stem)
    if entry is None:
        return Recognized(matched=False, reason="шифр не распознан по реестру")
    if not _middle_matches(stem, entry):
        return Recognized(matched=False,
                          reason=f"{entry.key}: не итоговый раздел (составной/рабочий файл)")

    # дата нужна только для имени папки-версии (в имени файла её нет)
    date6 = detect_date6(stem) or (mtime_date6(full_path) if date_from_mtime else None)

    canonical = canonical_name(entry, ext)
    if not canonical:
        return Recognized(matched=True, entry=entry, date6=date6,
                          reason="в реестре нет шаблона имени")
    return Recognized(matched=True, entry=entry, date6=date6, canonical=canonical)


def canonical_name(entry: RegEntry, ext: str) -> Optional[str]:
    """Эталонное имя файла (без даты): подгоняем только расширение."""
    template = entry.doc_template
    if not template:
        return None
    cbase, cext = os.path.splitext(template)
    if ext and ext != cext.lower():
        return cbase + ext
    return template
