"""Комплект !LATEST: сравнение с 4300_ПД и обновление.

Версия документа определяется по самому свежему каталогу версии
{обозначение}_{ггммдд} в папке документа (кроме !ARCHIVE).

Что в !LATEST лежит сейчас — храним в манифесте .docflow_latest.json
(в корне !LATEST): {ключ_документа: дата}. По нему и сравниваем.

При обновлении:
  • актуальная папка версии копируется в !LATEST (см. latest_layout),
  • манифест обновляется,
  • дата (ггммдд) пишется в Excel в колонку «Актуальная версия на дату»
    ТОЛЬКО для документов, которые попали в !LATEST,
  • старые версии этого документа в !LATEST удаляются (это исключение
    из правила «в рабочих томах только архивируем» — комплект !LATEST
    должен содержать одну актуальную версию; удаление — после подтверждения
    в диалоге). Лишнее в !LATEST (дубли/чужой шифр) — remove_paths.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zlib
from typing import List, Optional

from . import planner
from .config import AppConfig
from .naming import _nospace, canonical_name
from .registry import (RegEntry, strip_proj_prefix, _find_header, _numstr,
                       _gen_oboz, _has_sub, _norm, openpyxl, parse_row)
from .transfer import crc_diff

LATEST_MANIFEST = ".docflow_latest.json"
PD_SUB = "02_ПД"


def _file_crc32(path: str) -> str:
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def _catalog_date_for_path(path: str, stop_root: str) -> Optional[str]:
    """Дата ближайшего каталога версии над файлом."""
    current = os.path.dirname(os.path.abspath(path))
    stop = os.path.abspath(stop_root)
    while current == stop or current.startswith(stop + os.sep):
        _prefix, date6 = planner.cat_match(os.path.basename(current))
        if date6:
            return date6
        if current == stop:
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def check_explanatory_note_updates(xml_path: str, latest_root: str) -> dict:
    """Проверить, нужно ли обновлять XML пояснительной записки.

    Имена и CRC32 берутся из элементов ``FileName``/``FileChecksum`` XML.
    Для каждого упомянутого файла ищется самая свежая копия в ``!LATEST``.
    Обновление требуется, если каталог найденной копии новее каталога ПЗ
    либо содержимое файла изменилось при той же версии.
    """
    if not os.path.isfile(xml_path):
        raise FileNotFoundError(f"XML ПЗ не найден: {xml_path}")
    if not os.path.isdir(latest_root):
        raise FileNotFoundError(f"Папка !LATEST не найдена: {latest_root}")
    pz_date = _catalog_date_for_path(xml_path, latest_root)
    if not pz_date:
        raise ValueError(
            "Не удалось определить версию ПЗ: XML должен находиться внутри "
            "каталога вида {обозначение}_{ггммдд}.")

    root = ET.parse(xml_path).getroot()
    declared: dict[str, dict] = {}
    for file_node in root.iter():
        if file_node.tag.rsplit("}", 1)[-1] not in ("File", "SignFile", "ModelFile"):
            continue
        name = checksum = ""
        for child in file_node.iter():
            local = child.tag.rsplit("}", 1)[-1]
            if local == "FileName" and child.text:
                name = child.text.strip()
            elif local == "FileChecksum" and child.text:
                checksum = child.text.strip().upper()
        if name:
            declared[name.casefold()] = {"name": name, "checksum": checksum}

    newest: dict[str, dict] = {}
    wanted = set(declared)
    for dirpath, dirnames, filenames in os.walk(latest_root):
        dirnames[:] = [d for d in dirnames if d != "!ARCHIVE"]
        for filename in filenames:
            key = filename.casefold()
            if key not in wanted:
                continue
            path = os.path.join(dirpath, filename)
            version = _catalog_date_for_path(path, latest_root)
            if not version:
                continue
            current = newest.get(key)
            if current is None or version > current["version"]:
                newest[key] = {"path": path, "version": version}

    updates = []
    for key, found in newest.items():
        item = declared[key]
        version_newer = found["version"] > pz_date
        current_crc = ""
        checksum_changed = False
        # Более новая дата уже достаточна для вывода. Дорогой CRC32 нужен
        # только при той же версии — чтобы заметить перезапись файла внутри
        # существующего каталога, не читая сотни больших PDF/IFC по сети.
        if found["version"] == pz_date and item["checksum"]:
            current_crc = _file_crc32(found["path"])
            checksum_changed = current_crc != item["checksum"]
        if version_newer or checksum_changed:
            updates.append({
                "name": item["name"],
                "version": found["version"],
                "path": found["path"],
                "version_newer": version_newer,
                "checksum_changed": checksum_changed,
                "xml_checksum": item["checksum"],
                "current_checksum": current_crc,
            })
    updates.sort(key=lambda row: (row["version"], row["name"].casefold()),
                 reverse=True)
    return {
        "xml_path": xml_path,
        "pz_version": pz_date,
        "declared_files": len(declared),
        "matched_files": len(newest),
        "needs_update": bool(updates),
        "updates": updates,
    }


def _latest_sub(entry, cfg: AppConfig) -> str:
    """Подпапка !LATEST для области документа: 02_ПД или 01_ИИ."""
    if getattr(entry, "area", "ПД") == "ИИ":
        return cfg.ii_latest_sub or "01_ИИ"
    return PD_SUB


def latest_dest_root(cfg: AppConfig, entry) -> str:
    """Папка внутри !LATEST, куда кладётся каталог версии документа. Вложенность
    задаётся пользователем в настройках (cfg.latest_layout). Внутри всегда
    лежит {обозначение}_{дата}."""
    layout = getattr(cfg, "latest_layout", None)
    if layout is None:
        layout = "{area}/{section}"
    ph = {"area": _latest_sub(entry, cfg),
          "section": planner._section_folder(entry, cfg),
          "razdel": str(getattr(entry, "razdel", "") or ""),
          "short": getattr(entry, "short", "") or "",
          "oboznachenie": entry.oboznachenie}
    try:
        rel = layout.format(**ph)
    except (KeyError, IndexError, ValueError):
        rel = os.path.join(ph["area"], ph["section"])
    parts = [p for p in re.split(r"[\\/]+", rel) if p]
    return os.path.join(cfg.latest_abs, *parts) if parts else cfg.latest_abs

OK = "🟢"          # в !LATEST актуальная версия
NEWER = "🟠"       # в 4300_ПД есть новее
ABSENT = "🔴"      # в !LATEST нет
NO_PD = "⚪"       # в 4300_ПД нет версии
DIFF = "🟣"        # та же дата версии, но содержимое отличается от 4300_ПД (CRC32)


# ---------- манифест (в корне проекта, не в папке !LATEST) ----------
def manifest_path(cfg: AppConfig) -> str:
    base = cfg.project_root or cfg.latest_abs
    return os.path.join(base, LATEST_MANIFEST)


def load_manifest(cfg: AppConfig) -> dict:
    for p in (manifest_path(cfg), os.path.join(cfg.latest_abs, LATEST_MANIFEST)):
        if os.path.exists(p):                 # читаем и из старого места (миграция)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                # битый манифест — как будто его нет (пересоберём при обновлении)
                pass
    return {}


def save_manifest(cfg: AppConfig, man: dict) -> None:
    from .config import hide_file, unhide_file
    p = manifest_path(cfg)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    unhide_file(p)                        # снять «скрытый» перед перезаписью
    with open(p, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    hide_file(p)
    # убрать старый манифест из папки !LATEST, если остался
    old = os.path.join(cfg.latest_abs, LATEST_MANIFEST)
    if os.path.normpath(old) != os.path.normpath(p) and os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            # старый путь мог быть занят — новый уже записан
            pass


# ---------- определение свежей версии в 4300_ПД ----------
def newest_pd_version(entry: RegEntry, cfg: AppConfig):
    """Возвращает (дата, папка-источник для копирования) самой свежей версии.
    Для обычных документов источник — !PUBLISHED каталога версии;
    для смет (ЛСР/ОСР/ВОР/СВОР, раздел 12) — вся папка версии целиком."""
    doc = planner._doc_folder(entry, cfg)
    if not os.path.isdir(doc):
        return None, None
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    best_date, best_dir = None, None
    try:
        names = os.listdir(doc)
    except OSError:
        return None, None
    for name in names:
        full = os.path.join(doc, name)
        if name == cfg.archive_name or not os.path.isdir(full):
            continue
        prefix, d6 = planner.cat_match(name)
        if not prefix or _nospace(strip_proj_prefix(prefix)) != core:
            continue
        if best_date is None or d6 > best_date:
            best_date, best_dir = d6, full
    if not best_dir:
        return None, None
    # неделимые тома (сметы) — берём всю папку версии; остальные — папку публикуемых
    # файлов тома (по умолчанию !PUBLISHED, может быть НЕРЕД и т.п.)
    if planner._loose_catalog(entry, cfg):
        src = best_dir
    else:
        src = os.path.join(best_dir, planner._pub_of(entry, cfg))
    if not os.path.isdir(src):
        src = None
    return best_date, src


def compare(cfg: AppConfig, entries: List[RegEntry]) -> List[dict]:
    """Сводка по каждому документу для вкладки !LATEST."""
    man = load_manifest(cfg)
    rows = []
    for e in entries:
        pd_date, src = newest_pd_version(e, cfg)
        lat = man.get(e.key)
        diff = None
        if pd_date is None:
            status = NO_PD
        elif lat is None:
            status = ABSENT
        elif lat < pd_date:
            status = NEWER
        else:
            status = OK
            # та же дата версии — файл в 4300_ПД могли переписать/поправить,
            # не меняя каталог версии: сверяем состав и содержимое по CRC32
            # (та же «параноик-проверка», что и при переносе от субподрядчика)
            if src:
                dest_root = latest_dest_root(cfg, e)
                dest = os.path.join(dest_root, f"{e.oboznachenie}_{lat}")
                if os.path.isdir(dest):
                    added, removed, changed = crc_diff(src, dest, cfg)
                    if added or removed or changed:
                        status = DIFF
                        diff = {"added": added, "removed": removed, "changed": changed}
        rows.append({"entry": e, "pd": pd_date, "latest": lat,
                     "src": src, "status": status, "diff": diff})
    return rows


# ---------- обновление !LATEST ----------
def update_latest(cfg: AppConfig, rows: List[dict]) -> dict:
    """Копирует папку версии каждого выбранного документа в !LATEST целиком
    (содержимое !PUBLISHED — для обычных, всю папку версии — для смет).
    Старые версии этого документа в !LATEST удаляются. Возвращает
    {обозначение: дата} для записи в Excel."""
    man = load_manifest(cfg)
    updated = {}
    for r in rows:
        e, pd_date, src = r["entry"], r["pd"], r["src"]
        if not pd_date or not src or not os.path.isdir(src):
            continue
        dest_root = latest_dest_root(cfg, e)
        os.makedirs(dest_root, exist_ok=True)
        core = _nospace(strip_proj_prefix(e.oboznachenie))
        # удаляем старые версии этого документа в !LATEST
        for name in os.listdir(dest_root):
            full = os.path.join(dest_root, name)
            if not os.path.isdir(full):
                continue
            prefix, _d = planner.cat_match(name)
            if prefix and _nospace(strip_proj_prefix(prefix)) == core:
                shutil.rmtree(full, ignore_errors=True)
        dest = os.path.join(dest_root, f"{e.oboznachenie}_{pd_date}")
        # не тащим в !LATEST служебные/рабочие папки и мусор
        skip = shutil.ignore_patterns("_", "*_DRAFT*", "*_draft*", "Thumbs.db",
                                      "desktop.ini", "~$*", "*.tmp")
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=skip)
        man[e.key] = pd_date
        updated[e.oboznachenie] = pd_date
    save_manifest(cfg, man)
    return updated


# ---------- лишнее в !LATEST ----------
def latest_extras(cfg: AppConfig, entries: List[RegEntry]) -> List[dict]:
    """Что в !LATEST лишнее: дубли версий (есть новее), чужой шифр/год, папки
    с обозначением не из состава. Работает при ЛЮБОЙ вложенности (layout) —
    ищет каталоги версий по всему дереву !LATEST. Возвращает [{path, reason}]."""
    from .naming import _nospace
    from .registry import strip_proj_prefix
    extras: List[dict] = []
    root = cfg.latest_abs
    if not os.path.isdir(root):
        return extras
    proj = planner._project_shifr(entries)
    cores = {_nospace(strip_proj_prefix(e.oboznachenie)) for e in entries}
    # каталог версии: (папка-родитель, ядро) -> [(имя, дата, путь)]
    by_parent_core: dict = {}
    manifest_names = {LATEST_MANIFEST.lower(), ".docflow_crc.json"}
    for dp, dns, _fns in os.walk(root):
        keep = []
        for dn in dns:
            full = os.path.join(dp, dn)
            pfx, d6 = planner.cat_match(dn)
            if pfx:                                   # это каталог версии — внутрь не идём
                core = _nospace(strip_proj_prefix(pfx))
                rel = os.path.relpath(full, root)
                csh = planner._shifr_of(pfx)
                if proj and csh and csh != proj:
                    extras.append({"path": full, "reason": f"чужой шифр/год — {rel}"})
                elif cores and core not in cores:
                    extras.append({"path": full,
                                   "reason": f"обозначение не из состава — {rel}"})
                else:
                    by_parent_core.setdefault((dp, core), []).append((dn, d6, full))
                continue
            keep.append(dn)                           # обычная папка — углубляемся
        dns[:] = keep
    # дубли версий одного документа в одной папке — оставить самую свежую
    for (_dp, _core), lst in by_parent_core.items():
        if len(lst) > 1:
            lst.sort(key=lambda t: t[1] or "")
            for name, _d6, full in lst[:-1]:
                rel = os.path.relpath(full, root)
                extras.append({"path": full, "reason": f"дубль версии (есть новее) — {rel}"})
    return extras


def remove_paths(paths: List[str]) -> int:
    n = 0
    for p in paths:
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                n += 1
            elif os.path.exists(p):
                os.remove(p)
                n += 1
        except OSError:
            # файл мог исчезнуть/быть занят — идём дальше по списку
            pass
    return n


# ---------- запись версий в Excel ----------
def write_excel_versions(xlsx_path: str, sheet: Optional[str],
                         updates: dict, smeta_sub=("ВОР", "СВОР", "ЛСР", "ОСР"),
                         smeta_razdel="12", backup_path: Optional[str] = None) -> int:
    """Пишет дату (ггммдд) в колонку «Актуальная версия на дату» для документов
    из updates ({обозначение: дата}). Делает резервную копию xlsx. Возвращает
    число обновлённых строк."""
    if openpyxl is None or not updates:
        return 0
    bak = backup_path or (xlsx_path + ".bak")       # резервная копия
    if backup_path:
        from .config import hide_file, unhide_file
        unhide_file(bak)                            # снять «скрытый» перед перезаписью
        shutil.copy2(xlsx_path, bak)
        hide_file(bak)
    else:
        shutil.copy2(xlsx_path, bak)
    # 1) читаем ВЫЧИСЛЕННЫЕ значения (data_only) — иначе ячейки-формулы
    #    (=CONCATENATE(...)) вернут текст формулы, и обозначение не сойдётся
    from .registry import _pick_sheet
    wbv = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    wsv, rows = _pick_sheet(wbv, sheet)
    sheet_name = wsv.title
    header_idx, colmap = _find_header(rows)
    data_start = (header_idx + 1) if header_idx is not None else 0
    ver_col = _find_version_col(rows)
    proj = _project_shifr_local(rows, colmap, data_start)
    wbv.close()
    if ver_col is None:
        return 0

    # какие строки писать (тем же разбором, что и при загрузке состава)
    to_write = {}
    for i, r in enumerate(rows[data_start:], start=data_start):
        e = parse_row(r, colmap, proj, smeta_sub, smeta_razdel)
        if e and e.oboznachenie in updates:
            to_write[i] = updates[e.oboznachenie]
    if not to_write:
        return 0

    # 2) пишем дату в колонку версии (в обычный, не read_only, экземпляр)
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]
    for i, date in to_write.items():
        ws.cell(row=i + 1, column=ver_col + 1, value=date)
    wb.save(xlsx_path)
    wb.close()
    return len(to_write)


def _find_version_col(rows) -> Optional[int]:
    for r in rows[:15]:
        for j, c in enumerate(r):
            if "актуальная версия" in _norm(c):
                return j
    return None


def _project_shifr_local(rows, colmap, data_start) -> str:
    from .registry import SHIFR_CORE
    for r in rows[:8]:
        for c in r:
            s = str(c or "").strip()
            m = SHIFR_CORE.fullmatch(s)
            if m:
                return s
    j = colmap.get("oboznachenie")
    if j is not None:
        for r in rows[data_start:]:
            if j < len(r) and r[j]:
                m = SHIFR_CORE.match(str(r[j]).strip())
                if m:
                    return m.group(0)
    return ""


def _row_oboznachenie(r, colmap, proj, sub_codes, smeta_razdel) -> str:
    def cell(field):
        j = colmap.get(field)
        return "" if (j is None or j >= len(r) or r[j] is None) else str(r[j]).strip()
    chast = _numstr(cell("chast"))
    short = cell("short")
    short_u = short.upper()
    raw = cell("oboznachenie")
    if short_u in ("ЛСР", "ОСР"):
        code = raw or cell("nomer")
        part = chast or "3"
        return f"{proj}-СМ{part}_{code}_{short_u}" if (proj and code) else raw
    podr = _numstr(cell("podrazdel"))
    kniga = _numstr(cell("kniga"))
    return raw or _gen_oboz(proj, short, podr, chast, kniga)
