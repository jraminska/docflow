"""Конвейер подписания комплекта.

Три шага (окно «Подписание»):
  1. СОБРАТЬ на подпись — из структурированной папки (!LATEST, 4300_ПД, 1100_ИИ
     или субподрядной — не важно) собираются файлы актуальной версии каждого
     документа (pdf/xml/gge …) в ОДНУ плоскую папку, чтобы руководство
     подписало весь комплект сразу сторонней программой.
  2. ДОБАВИТЬ ФАМИЛИЮ — после подписания к каждому .sig добавляется фамилия
     подписавшего: 523-…_Раздел ПД №4.pdf.sig → 523-…_Раздел ПД №4.pdf_Сидоров.sig.
  3. РАЗЛОЖИТЬ .sig — переименованные подписи раскладываются обратно в
     4300_ПД/1100_ИИ: каждый .sig по имени сопоставляется со своим документом
     и кладётся в !PUBLISHED самой свежей версии на сервере.

Ничего не удаляется: файлы копируются, подписи переименовываются на месте.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import List, Optional, Tuple

from . import planner
from .config import AppConfig
from .naming import _nospace
from .registry import RegEntry, strip_proj_prefix

# форматы, которые обычно подписывают (для сбора комплекта — можно менять в окне)
DEFAULT_SIGN_EXTS = [".pdf", ".xml", ".gge"]
# «документные» расширения — по ним отличаем свежую подпись (X.pdf.sig) от уже
# переименованной (X.pdf_Фамилия.sig)
DOC_EXTS = (".pdf", ".xml", ".gge", ".gsfx", ".xlsx", ".docx", ".dwg", ".dwx", ".ods")


# ---------- шаг 1: собрать комплект на подпись ----------
def _is_special_folder(name: str, cfg: AppConfig) -> bool:
    """Служебные папки (!ARCHIVE, !EDIT, !PUBLISHED …) — не разделы."""
    if not name or name.startswith("!"):
        return True
    low = name.lower()
    for attr in ("archive_name", "edit_dir", "published_dir"):
        val = getattr(cfg, attr, "") or ""
        if val and low == val.lower():
            return True
    return False


def list_signing_folders(source_root: str, cfg: AppConfig) -> List[str]:
    """Подпапки-разделы (или области) в корне источника для выбора при сборе.

    Для ``4300_ПД`` / ``1100_ИИ/04_Отчеты`` — разделы ``01_ПЗ``, ``05_ИОС``…
    Для ``!LATEST`` с раскладкой ``{area}/{section}`` — области ``02_ПД``,
    ``01_ИИ`` (выбор целой области). Служебные ``!*`` не включаются.
    Возвращает отсортированные имена папок (не полные пути).
    """
    return [path for path, depth in list_signing_folder_tree(source_root, cfg)
            if depth == 0]


def list_signing_folder_tree(source_root: str, cfg: AppConfig) -> List[tuple[str, int]]:
    """Иерархия доступных для подписания папок: (относительный путь, глубина).

    Обход идёт от комплекта/сервера через 4300_ПД/1100_ИИ, разделы и тома.
    Служебные папки и каталоги конкретных версий в дерево не включаются:
    версия является содержимым выбранного тома, а не отдельным пунктом выбора.
    """
    if not source_root or not os.path.isdir(source_root):
        return []
    result: List[tuple[str, int]] = []

    def walk(folder: str, rel_parent: str, depth: int) -> None:
        try:
            entries = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            return
        for name in entries:
            full = os.path.join(folder, name)
            if not os.path.isdir(full) or _is_special_folder(name, cfg):
                continue
            prefix, date = planner.cat_match(name)
            if prefix and date:
                continue
            rel = os.path.join(rel_parent, name) if rel_parent else name
            result.append((rel, depth))
            walk(full, rel, depth + 1)

    walk(source_root, "", 0)
    return result


def _newest_versions(root: str, cfg: AppConfig) -> dict:
    """{ядро_обозначения: (дата, папка_каталога_версии)} — самая свежая версия
    каждого документа в дереве (по каталогам {обозначение}_{дата})."""
    best: dict = {}
    if not root or not os.path.isdir(root):
        return best
    for dp, dns, _fns in os.walk(root):
        keep = []
        for dn in dns:
            if dn == cfg.archive_name:            # архив не берём
                continue
            pfx, d6 = planner.cat_match(dn)
            if pfx and d6:
                core = _nospace(strip_proj_prefix(pfx))
                cur = best.get(core)
                if cur is None or d6 > cur[0]:
                    best[core] = (d6, os.path.join(dp, dn))
                # внутрь каталога версии не углубляемся
            else:
                keep.append(dn)
        dns[:] = keep
    return best


def _merge_newest(best: dict, more: dict) -> None:
    """Объединить результаты ``_newest_versions`` (берём более свежую дату)."""
    for core, val in more.items():
        cur = best.get(core)
        if cur is None or val[0] > cur[0]:
            best[core] = val


def collect_for_signing(cfg: AppConfig, source_root: str, target_dir: str,
                        exts=None, selected_folders=None) -> Tuple[List[str], int]:
    """Собрать актуальные файлы (exts) из структурированной папки в ПЛОСКУЮ
    target_dir. Редактируемые исходники (!EDIT) не берём. Возвращает
    (список_скопированных_имён, число_пропущенных_дублей).

    ``selected_folders`` — имена или абсолютные пути дочерних папок-разделов
    под ``source_root``. ``None`` — сканировать весь корень (как раньше);
    пустой список — ничего не копировать.
    """
    exts = exts or DEFAULT_SIGN_EXTS
    want = {(e if e.startswith(".") else "." + e).lower() for e in exts}
    os.makedirs(target_dir, exist_ok=True)
    edit_names = {cfg.edit_dir.lower(), "!edit"}

    if selected_folders is None:
        scan_roots = [source_root]
    else:
        scan_roots = []
        for item in selected_folders:
            if not item:
                continue
            path = item if os.path.isabs(item) else os.path.join(source_root, item)
            path = os.path.normpath(path)
            if os.path.isdir(path):
                scan_roots.append(path)

    best: dict = {}
    for root in scan_roots:
        _merge_newest(best, _newest_versions(root, cfg))

    copied: List[str] = []
    skipped = 0
    for _core, (_d6, cat) in sorted(best.items()):
        for dp, dns, fns in os.walk(cat):
            dns[:] = [d for d in dns if d.lower() not in edit_names]  # без ред. форматов
            for fn in fns:
                if planner._ignored(fn, cfg):
                    continue
                if os.path.splitext(fn)[1].lower() not in want:
                    continue
                src = os.path.join(dp, fn)
                dst = os.path.join(target_dir, fn)
                if os.path.exists(dst):
                    skipped += 1
                    continue
                shutil.copy2(src, dst)
                copied.append(fn)
    return copied, skipped


# ---------- шаг 2: добавить фамилию к подписям ----------
def add_surname(folder: str, surname: str, doc_exts=None) -> Tuple[list, list]:
    """X.pdf.sig → X.pdf_{surname}.sig для всех .sig в папке (рекурсивно).
    Уже переименованные (заканчиваются на _Фамилия.sig, а не на .pdf/.xml…)
    пропускаются. Возвращает (переименовано[(старое,новое)], пропущено[имена])."""
    surname = (surname or "").strip().replace("/", "-").replace("\\", "-")
    done: list = []
    skipped: list = []
    if not surname or not os.path.isdir(folder):
        return done, skipped
    exts = tuple(e.lower() for e in (doc_exts or DOC_EXTS))
    for dp, _dns, fns in os.walk(folder):
        for fn in sorted(fns):
            if not fn.lower().endswith(".sig"):
                continue
            base = fn[:-4]                        # без .sig
            if os.path.splitext(base)[1].lower() not in exts:
                skipped.append(fn)                # уже с фамилией / не по шаблону
                continue
            new = f"{base}_{surname}.sig"
            src, dst = os.path.join(dp, fn), os.path.join(dp, new)
            if os.path.exists(dst):
                skipped.append(fn)
                continue
            try:
                os.rename(src, dst)
                done.append((fn, new))
            except OSError:
                skipped.append(fn)
    return done, skipped


# ---------- шаг 3: разложить подписи на сервер ----------
def _base_from_sig(name: str) -> str:
    """523-…Часть №1.pdf_Сидоров.sig → 523-…Часть №1.pdf ; …pdf.sig → …pdf."""
    if not name.lower().endswith(".sig"):
        return name
    base = name[:-4]
    # если после расширения документа идёт _Фамилия — отрезаем
    m = re.match(r"^(.*\.[A-Za-z0-9]+)_[^_.]+$", base)
    return m.group(1) if m else base


def _server_pub_dir(entry: RegEntry, cfg: AppConfig) -> Tuple[Optional[str], Optional[str]]:
    """(дата, папка публикуемых файлов) самой свежей версии документа на сервере.
    Для смет/неделимых — сама папка версии."""
    doc = planner._doc_folder(entry, cfg)
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    best, bestdir = None, None
    if os.path.isdir(doc):
        for name in os.listdir(doc):
            full = os.path.join(doc, name)
            if name == cfg.archive_name or not os.path.isdir(full):
                continue
            pfx, d6 = planner.cat_match(name)
            if pfx and _nospace(strip_proj_prefix(pfx)) == core:
                if best is None or (d6 or "") > best:
                    best, bestdir = d6, full
    if not bestdir:
        return None, None
    if planner._loose_catalog(entry, cfg):
        return best, bestdir
    return best, os.path.join(bestdir, planner._pub_of(entry, cfg))


def _match_entry(base: str, cores: list) -> Optional[RegEntry]:
    """Найти документ по имени файла: ядро обозначения — префикс имени.
    Самое длинное совпадение (чтобы КР10 не путался с КР1)."""
    stem = os.path.splitext(base)[0]
    bcore = _nospace(strip_proj_prefix(stem))
    for core, e in cores:                         # cores отсортированы по длине убыв.
        if core and bcore.startswith(core):
            return e
    return None


def distribute_by_name(cfg: AppConfig, sig_folder: str, target_root: str,
                       skip_archive: bool = True) -> List[dict]:
    """Разложить .sig в указанную папку (напр. субподрядную): каждый .sig
    кладётся РЯДОМ с файлом того же имени, найденным в target_root и подпапках.
    Сопоставление — по полному имени файла (X.pdf.sig ↔ X.pdf). Возвращает
    [{sig, status, dst}]."""
    out: List[dict] = []
    if not os.path.isdir(sig_folder) or not os.path.isdir(target_root):
        return out
    # индекс файлов целевой папки: имя → [папки, где лежит]
    index: dict = {}
    for dp, _dns, fns in os.walk(target_root):
        for fn in fns:
            index.setdefault(fn, []).append(dp)
    for dp, _dns, fns in os.walk(sig_folder):
        for fn in sorted(fns):
            if not fn.lower().endswith(".sig"):
                continue
            base = os.path.basename(_base_from_sig(fn))     # X.pdf
            dirs = index.get(base, [])
            if skip_archive:
                dirs = [d for d in dirs
                        if cfg.archive_name not in d.replace("/", os.sep).split(os.sep)]
            if not dirs:
                out.append({"sig": fn, "status": f"нет файла «{base}» в папке", "dst": ""})
                continue
            for d in dirs:
                src = os.path.join(dp, fn)
                dst = os.path.join(d, fn)
                if os.path.normpath(src) == os.path.normpath(dst):
                    out.append({"sig": fn, "status": "уже на месте", "dst": dst})
                    continue
                try:
                    shutil.copy2(src, dst)
                    out.append({"sig": fn, "status": "ок", "dst": dst})
                except OSError as ex:
                    out.append({"sig": fn, "status": f"ошибка: {ex}", "dst": ""})
    return out


def distribute_sig(cfg: AppConfig, entries: List[RegEntry], folder: str) -> List[dict]:
    """Разложить .sig из папки по серверу (4300_ПД/1100_ИИ): каждый по имени
    сопоставляется с документом и кладётся в !PUBLISHED свежей версии.
    Возвращает [{sig, status, dst}] (status: ок/не распознан/нет версии/ошибка)."""
    cores = sorted(((_nospace(strip_proj_prefix(e.oboznachenie)), e) for e in entries),
                   key=lambda t: len(t[0]), reverse=True)
    out: List[dict] = []
    if not os.path.isdir(folder):
        return out
    for dp, _dns, fns in os.walk(folder):
        for fn in sorted(fns):
            if not fn.lower().endswith(".sig"):
                continue
            src = os.path.join(dp, fn)
            base = _base_from_sig(fn)
            e = _match_entry(base, cores)
            if e is None:
                out.append({"sig": fn, "status": "не распознан", "dst": ""})
                continue
            _date, pubdir = _server_pub_dir(e, cfg)
            if not pubdir:
                out.append({"sig": fn, "status": "нет версии на сервере",
                            "dst": "", "key": e.key})
                continue
            try:
                os.makedirs(pubdir, exist_ok=True)
                shutil.copy2(src, os.path.join(pubdir, fn))
                out.append({"sig": fn, "status": "ок", "dst": os.path.join(pubdir, fn),
                            "key": e.key})
            except OSError as ex:
                out.append({"sig": fn, "status": f"ошибка: {ex}", "dst": ""})
    return out
