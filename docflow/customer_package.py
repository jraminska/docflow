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


def build_package(cfg: AppConfig, source_root: str, target_dir: str,
                  selected_folders=None, progress=None) -> dict:
    """Скопировать выбранные ветки с сохранением структуры и создать реестр."""
    if not source_root or not os.path.isdir(source_root):
        raise FileNotFoundError("Папка !LATEST не найдена.")
    if not target_dir:
        raise ValueError("Не указана папка для комплекта.")
    os.makedirs(target_dir, exist_ok=True)
    rows = []
    copied = 0
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
                parts = rel.replace("\\", "/").split("/")
                rows.append({
                    "section": parts[1] if len(parts) > 2 else parts[0],
                    "document": parts[-2] if len(parts) > 1 else "",
                    "relative": rel,
                    "name": filename,
                    "extension": os.path.splitext(filename)[1].lower().lstrip("."),
                    "size": size,
                    "crc32": crc,
                })
                copied += 1
                if progress:
                    progress(copied, rel)
    registry = os.path.join(target_dir, REGISTRY_NAME)
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
    ws.merge_cells("A1:I1")
    ws["A1"] = "РЕЕСТР ПЕРЕДАННОЙ ДОКУМЕНТАЦИИ"
    ws["A1"].font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="1F4E78")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A2:I2")
    ws["A2"] = f"Источник: {source_root}"
    ws["A3"] = "Сформирован:"
    ws["B3"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    ws["D3"] = "Файлов:"
    ws["E3"] = len(rows)
    headers = [
        "№", "Раздел", "Документ / версия", "Имя файла", "Формат",
        "Размер, байт", "CRC32", "Относительный путь", "Открыть файл"]
    ws.append([])
    ws.append(headers)
    header_row = 5
    for index, row in enumerate(rows, start=1):
        excel_row = header_row + index
        ws.append([
            index, row["section"], row["document"], row["name"],
            row["extension"].upper(), row["size"], row["crc32"],
            row["relative"], "Открыть"])
        link = ws.cell(excel_row, 9)
        link.hyperlink = row["relative"]
        link.style = "Hyperlink"
    if rows:
        table = Table(displayName="TransferredDocuments",
                      ref=f"A{header_row}:I{header_row + len(rows)}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True,
            showFirstColumn=False, showLastColumn=False)
        ws.add_table(table)
    widths = {
        "A": 14, "B": 20, "C": 30, "D": 55, "E": 11,
        "F": 16, "G": 14, "H": 70, "I": 15}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=header_row, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(
                vertical="center", wrap_text=cell.column in (3, 4, 8))
    for cell in ws["F"][header_row:]:
        cell.number_format = "#,##0"
    for cell in ws["G"][header_row:]:
        cell.number_format = "@"
    ws.auto_filter.ref = f"A{header_row}:I{max(header_row, ws.max_row)}"
    wb.save(path)
