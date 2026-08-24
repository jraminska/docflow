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
    в диалоге). Лишнее в !LATEST (дубли, чужой шифр, файлы и папки вне
    каталогов версии, документы не в своей папке) — remove_paths.
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
from .config import AppConfig, is_ignored
from .naming import _nospace, canonical_name
from .registry import (RegEntry, strip_proj_prefix, _find_header, _numstr,
                       _gen_oboz, _has_sub, _norm, openpyxl, parse_row)

LATEST_MANIFEST = ".docflow_latest.json"
PD_SUB = "02_ПД"


def _latest_ignored_dir(name: str, cfg: AppConfig) -> bool:
    """Папка, которую нельзя переносить в !LATEST.

    Используем те же проектные настройки, что и при сканировании:
    ``scan_skip_dirs`` и пользовательские ``exclude_folders``.
    Сравнение имён регистронезависимое — это соответствует поведению Windows.
    """
    key = str(name or "").casefold()
    skipped = {str(x).casefold() for x in (cfg.scan_skip_dirs or []) if x}
    excluded = {str(x).casefold() for x in (cfg.exclude_folders or []) if x}
    return key in skipped or key in excluded


def _latest_copy_ignore(cfg: AppConfig):
    """Callback для shutil.copytree: единые правила исключений DocFlow.

    - файлы и папки по ``cfg.ignore_patterns``;
    - папки из ``cfg.scan_skip_dirs``;
    - папки из ``cfg.exclude_folders``.

    Callback вызывается ``copytree`` на каждом уровне дерева, поэтому правила
    действуют и для вложенных папок внутри !PUBLISHED.
    """
    patterns = list(cfg.ignore_patterns or [])

    def ignore(directory: str, names: List[str]) -> List[str]:
        out: List[str] = []
        for name in names:
            if is_ignored(name, patterns):
                out.append(name)
                continue
            full = os.path.join(directory, name)
            if os.path.isdir(full) and _latest_ignored_dir(name, cfg):
                out.append(name)
        return out

    return ignore


def _latest_fileset_crc(folder: str, cfg: AppConfig) -> dict:
    """CRC32 значимых файлов с теми же исключениями, что при копировании.

    Это важно для статуса !LATEST: исключённые папки не должны давать ложный
    DIFF после того, как мы намеренно не скопировали их из !PUBLISHED.
    """
    out: dict = {}
    patterns = list(cfg.ignore_patterns or [])
    for dp, dns, fns in os.walk(folder):
        dns[:] = [d for d in dns
                  if not is_ignored(d, patterns) and not _latest_ignored_dir(d, cfg)]
        for fn in fns:
            if is_ignored(fn, patterns):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, folder).replace("\\", "/").lower()
            out[rel] = _file_crc32(full)
    return out


def _latest_crc_diff(src_folder: str, dest_folder: str, cfg: AppConfig):
    """Сравнить источник и !LATEST только по файлам, которые подлежат копированию."""
    src = _latest_fileset_crc(src_folder, cfg)
    dest = _latest_fileset_crc(dest_folder, cfg)
    added = sorted(k for k in src if k not in dest)
    removed = sorted(k for k in dest if k not in src)
    changed = sorted(k for k in src if k in dest and src[k] != dest[k])
    return added, removed, changed


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
    parent = planner._smeta_parent(getattr(entry, "oboznachenie", "") or "")
    if parent:
        # ВОР/ЛСР/ОСР/СВОР — внутрь родительской части: 12_СМ/хх-ПИР-СМ3/…
        section = ph["section"]
        if section and section in parts:
            parts.insert(parts.index(section) + 1, parent)
        else:
            parts.append(parent)
    return os.path.join(cfg.latest_abs, *parts) if parts else cfg.latest_abs


def _same_dir(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _iter_version_catalogs(root: str, skip_dir=None):
    """Каталоги версии {обозначение}_{ггммдд} по дереву. Внутрь них не заходим."""
    if not os.path.isdir(root):
        return
    for dp, dns, _fns in os.walk(root):
        keep = []
        for dn in dns:
            if skip_dir and skip_dir(dn):
                continue
            pfx, d6 = planner.cat_match(dn)
            if pfx:
                yield dp, dn, pfx, d6, os.path.join(dp, dn)
            else:
                keep.append(dn)
        dns[:] = keep


def _remove_matching_catalogs(root: str, core: str) -> None:
    """Удаляет все каталоги версии документа (по ядру обозначения) в !LATEST."""
    for _dp, _dn, pfx, _d6, full in list(_iter_version_catalogs(root)):
        if _nospace(strip_proj_prefix(pfx)) == core:
            shutil.rmtree(full, ignore_errors=True)

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
            dest_root = latest_dest_root(cfg, e)
            dest = os.path.join(dest_root, f"{e.oboznachenie}_{lat}")
            # в манифесте дата есть, но каталог лежит не там (например ВОР
            # остался прямо в 12_СМ вместо 12_СМ/хх-ПИР-СМ2) — это «нет»
            if not os.path.isdir(dest):
                status = ABSENT
            else:
                status = OK
                # та же дата версии — файл в 4300_ПД могли переписать/поправить,
                # не меняя каталог версии: сверяем состав и содержимое по CRC32.
                # Исключённые из копирования папки в сравнении также не участвуют.
                if src:
                    added, removed, changed = _latest_crc_diff(src, dest, cfg)
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

    При копировании на любом уровне дерева исключаются:
    ``ignore_patterns``, ``scan_skip_dirs`` и ``exclude_folders``.
    Старые версии этого документа в !LATEST удаляются. Возвращает
    {обозначение: дата} для записи в Excel.
    """
    man = load_manifest(cfg)
    updated = {}
    for r in rows:
        e, pd_date, src = r["entry"], r["pd"], r["src"]
        if not pd_date or not src or not os.path.isdir(src):
            continue
        dest_root = latest_dest_root(cfg, e)
        os.makedirs(dest_root, exist_ok=True)
        core = _nospace(strip_proj_prefix(e.oboznachenie))
        # старые версии — по всему !LATEST, не только в новой папке:
        # иначе при смене раскладки (ВОР из 12_СМ → 12_СМ/хх-ПИР-СМ2)
        # прежняя копия останется «лишней».
        _remove_matching_catalogs(cfg.latest_abs, core)
        dest = os.path.join(dest_root, f"{e.oboznachenie}_{pd_date}")
        shutil.copytree(src, dest, dirs_exist_ok=True,
                        ignore=_latest_copy_ignore(cfg))
        man[e.key] = pd_date
        updated[e.oboznachenie] = pd_date
    save_manifest(cfg, man)
    return updated


# ---------- лишнее в !LATEST ----------
def latest_extras(cfg: AppConfig, entries: List[RegEntry]) -> List[dict]:
    """Что в !LATEST лишнее: дубли версий, чужой шифр/год, каталоги не из состава,
    каталоги не в своей папке, лишние файлы и посторонние папки.
    Работает при любой вложенности (layout). Возвращает [{path, reason}]."""
    extras: List[dict] = []
    root = cfg.latest_abs
    if not os.path.isdir(root):
        return extras
    proj = planner._project_shifr(entries)
    cores = {_nospace(strip_proj_prefix(e.oboznachenie)) for e in entries}
    expected_parent: dict = {}
    expected_dirs = {os.path.normcase(os.path.normpath(root))}
    for e in entries:
        core = _nospace(strip_proj_prefix(e.oboznachenie))
        dest = os.path.normpath(latest_dest_root(cfg, e))
        expected_parent[core] = dest
        cur = dest
        while True:
            expected_dirs.add(os.path.normcase(os.path.normpath(cur)))
            if _same_dir(cur, root):
                break
            parent = os.path.dirname(cur)
            if parent == cur or len(os.path.normpath(parent)) < len(os.path.normpath(root)):
                break
            cur = parent

    excluded = {str(x).casefold() for x in (cfg.exclude_folders or []) if x}
    patterns = list(cfg.ignore_patterns or [])
    service_files = {LATEST_MANIFEST.lower(), ".docflow_crc.json"}

    def skip_dir(name: str) -> bool:
        if is_ignored(name, patterns):
            return True
        return str(name).casefold() in excluded

    by_parent_core: dict = {}
    found_catalogs: List[tuple] = []
    for dp, dn, pfx, d6, full in _iter_version_catalogs(root, skip_dir=skip_dir):
        found_catalogs.append((dp, dn, pfx, d6, full))
        core = _nospace(strip_proj_prefix(pfx))
        rel = os.path.relpath(full, root)
        csh = planner._shifr_of(pfx)
        if proj and csh and csh != proj:
            extras.append({"path": full, "reason": f"чужой шифр/год — {rel}"})
        elif cores and core not in cores:
            extras.append({"path": full,
                           "reason": f"обозначение не из состава — {rel}"})
        else:
            expected = expected_parent.get(core)
            if expected and not _same_dir(dp, expected):
                extras.append({
                    "path": full,
                    "reason": f"не в своей папке — {rel} "
                              f"(ожидается {os.path.relpath(expected, root)})",
                })
            else:
                by_parent_core.setdefault((dp, core), []).append((dn, d6, full))

    for (_dp, _core), lst in by_parent_core.items():
        if len(lst) > 1:
            lst.sort(key=lambda t: t[1] or "")
            for name, _d6, full in lst[:-1]:
                rel = os.path.relpath(full, root)
                extras.append({"path": full, "reason": f"дубль версии (есть новее) — {rel}"})

    legitimate = set(expected_dirs)
    for dp, _dn, _pfx, _d6, _full in found_catalogs:
        cur = os.path.normpath(dp)
        while True:
            legitimate.add(os.path.normcase(cur))
            if _same_dir(cur, root):
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    root_key = os.path.normcase(os.path.normpath(root))
    for dp, dns, fns in os.walk(root):
        keep = []
        for dn in dns:
            if skip_dir(dn):
                continue
            if planner.cat_match(dn)[0]:
                continue
            full = os.path.join(dp, dn)
            keep.append(dn)
            n = os.path.normcase(os.path.normpath(full))
            if n in legitimate:
                continue
            parent_key = os.path.normcase(os.path.normpath(dp))
            if parent_key != root_key and parent_key not in legitimate:
                continue
            rel = os.path.relpath(full, root)
            extras.append({"path": full, "reason": f"лишняя папка — {rel}"})
        dns[:] = keep
        if os.path.normcase(os.path.normpath(dp)) not in legitimate:
            continue
        for fn in fns:
            if is_ignored(fn, patterns) or fn.lower() in service_files:
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root)
            extras.append({"path": full, "reason": f"лишний файл — {rel}"})
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
