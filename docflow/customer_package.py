"""Формирование комплекта для передачи заказчику из !LATEST."""
from __future__ import annotations

import os
import shutil
import zlib
from datetime import datetime
from typing import Iterable, List

from .config import AppConfig
from . import planner

REGISTRY_NAME = "Реестр_переданной_документации.xlsx"
SERVICE_FILES = {".docflow_crc.json", ".docflow_latest.json", REGISTRY_NAME.lower()}


def _crc32(path: str, chunk: int = 1 << 20) -> str:
    crc = 0
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(chunk), b""):
            crc = zlib.crc32(block, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def _scan_roots(source_root: str, selected_folders) -> List[str]:
    if selected_folders is None:
        return [source_root]
    roots = []
    for item in selected_folders:
        path = item if os.path.isabs(item) else os.path.join(source_root, item)
        path = os.path.normpath(path)
        if os.path.isdir(path):
            roots.append(path)
    return roots


def _entry_index(entries) -> dict:
    result = {}
    for entry in entries or []:
        for value in (entry.oboznachenie, entry.key):
            if value:
                result[value.casefold().replace(" ", "")] = entry
    return result


def _file_context(rel: str, entry_lookup: dict) -> tuple:
    parts = rel.replace("\\", "/").split("/")
    entry = None
    version = ""
    designation = ""
    for part in reversed(parts[:-1]):
        prefix, date = planner.cat_match(part)
        if prefix:
            version = date or ""
            designation = prefix
            entry = entry_lookup.get(prefix.casefold().replace(" ", ""))
            break
    section = parts[1] if len(parts) > 2 else parts[0]
    if entry is not None:
        section = entry.section or section
        designation = entry.oboznachenie or designation
    volume_name = (
        getattr(entry, "name", "") if entry is not None else ""
    ) or designation or (parts[-2] if len(parts) > 1 else "")
    return entry, section, designation, volume_name, version


def _signer_names(entry) -> List[str]:
    names = []
    for signer in getattr(entry, "signers", []) if entry is not None else []:
        if isinstance(signer, dict):
            signer = signer.get("name") or signer.get("fio") or ""
        signer = str(signer).strip()
        if signer:
            names.append(signer)
    return names


def _signature_signer(filename: str, entry) -> str:
    folded = filename.casefold().replace("ё", "е")
    for signer in _signer_names(entry):
        surname = signer.split()[0].casefold().replace("ё", "е")
        if surname and surname in folded:
            return signer
    return ""


def build_package(cfg: AppConfig, source_root: str, target_dir: str,
                  selected_folders=None, progress=None, entries=None) -> dict:
    """Скопировать выбранные ветки с сохранением структуры и создать реестр."""
    if not source_root or not os.path.isdir(source_root):
        raise FileNotFoundError("Папка !LATEST не найдена.")
    if not target_dir:
        raise ValueError("Не указана папка для комплекта.")
    os.makedirs(target_dir, exist_ok=True)
    rows = []
    copied = 0
    entry_lookup = _entry_index(entries)
    for scan_root in _scan_roots(source_root, selected_folders):
        for dirpath, dirnames, filenames in os.walk(scan_root):
            dirnames[:] = [
                d for d in dirnames
                if d != cfg.archive_name and not planner._ignored(d, cfg)]
            for filename in sorted(filenames, key=str.casefold):
                if (filename.casefold() in SERVICE_FILES
                        or planner._ignored(filename, cfg)):
                    continue
                src = os.path.join(dirpath, filename)
                rel = os.path.relpath(src, source_root)
                dst = os.path.join(target_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                size = os.path.getsize(dst)
                crc = _crc32(dst)
                entry, section, designation, volume_name, version = _file_context(
                    rel, entry_lookup)
                is_signature = filename.casefold().endswith(".sig")
                rows.append({
                    "section": section,
                    "designation": designation,
                    "volume_name": volume_name,
                    "version": version,
                    "relative": rel,
                    "name": filename,
                    "extension": os.path.splitext(filename)[1].lower().lstrip("."),
                    "size": size,
                    "crc32": crc,
                    "kind": "Подпись" if is_signature else "Документ",
                    "signer": _signature_signer(filename, entry) if is_signature else "",
                    "required_signers": ", ".join(_signer_names(entry)),
                })
                copied += 1
                if progress:
                    progress(copied, rel)
    registry = os.path.join(target_dir, REGISTRY_NAME)
    rows.sort(key=lambda row: (
        row["section"].casefold(), row["designation"].casefold(),
        row["version"], row["name"].casefold().replace(".sig", ""),
        row["kind"] == "Подпись"))
    _write_registry(registry, rows, source_root)
    return {
        "target": target_dir,
        "registry": registry,
        "files": copied,
        "bytes": sum(row["size"] for row in rows),
        "rows": rows,
    }


def _write_registry(path: str, rows: Iterable[dict], source_root: str) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен модуль openpyxl — невозможно создать Excel-реестр."
        ) from exc
    rows = list(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "Реестр"
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A6"
    ws.merge_cells("A1:L1")
    ws["A1"] = "РЕЕСТР ПЕРЕДАННОЙ ДОКУМЕНТАЦИИ"
    ws["A1"].font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="1F4E78")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A2:L2")
    ws["A2"] = f"Источник: {source_root}"
    ws["A3"] = "Сформирован:"
    ws["B3"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    ws["D3"] = "Файлов:"
    ws["E3"] = len(rows)
    headers = [
        "№", "Раздел", "Обозначение", "Наименование тома", "Версия",
        "Имя файла документа / подписи", "Тип", "Подписант / требуемые подписи",
        "Формат", "Размер, байт", "CRC32", "Путь к файлу"]
    ws.append([])
    ws.append(headers)
    header_row = 5
    for index, row in enumerate(rows, start=1):
        excel_row = header_row + index
        ws.append([
            index, row["section"], row["designation"], row["volume_name"],
            row["version"], row["name"], row["kind"],
            row["signer"] or (
                "Требуются: " + row["required_signers"]
                if row["required_signers"] else ""),
            row["extension"].upper(), row["size"], row["crc32"],
            row["relative"]])
        link = ws.cell(excel_row, 12)
        link.hyperlink = row["relative"]
        link.style = "Hyperlink"
    if rows:
        table = Table(displayName="TransferredDocuments",
                      ref=f"A{header_row}:L{header_row + len(rows)}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True,
            showFirstColumn=False, showLastColumn=False)
        ws.add_table(table)
    widths = {
        "A": 7, "B": 18, "C": 24, "D": 48, "E": 12, "F": 55,
        "G": 13, "H": 38, "I": 10, "J": 16, "K": 14, "L": 70}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=header_row, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(
                vertical="center", wrap_text=cell.column in (3, 4, 6, 8, 12))
    for cell in ws["J"][header_row:]:
        cell.number_format = "#,##0"
    for cell in ws["K"][header_row:]:
        cell.number_format = "@"
    ws.auto_filter.ref = f"A{header_row}:L{max(header_row, ws.max_row)}"
    wb.save(path)
