# -*- coding: utf-8 -*-
"""Общие константы UI."""
from __future__ import annotations

import os

from docflow import planner

APP_TITLE = "DocFlow — комплектование проектной документации"
CHECK_ON, CHECK_OFF = "☑", "☐"

KIND_ORDER = planner.KIND_ORDER

# статусы разделов
DOT_OK = "🟢"        # всё в порядке
DOT_TODO = "🟠"      # есть действия для наведения порядка
DOT_ATTENTION = "🟡" # требует внимания (не распознано)
DOT_UNKNOWN = "⚪"   # ещё не сканировали

# тег цвета текста узла по статусу (эмодзи в ttk не цветные, красим текст)
STATUS_TAG = {DOT_OK: "st_ok", DOT_TODO: "st_todo",
              DOT_ATTENTION: "st_att", DOT_UNKNOWN: "st_unk"}

# индикатор у строки действия
KIND_DOT = {
    planner.MKSTRUCT: "🔴", planner.TRANSFER: "🔴", planner.MKCATALOG: "🟠",
    planner.ARCHIVE_FOLDER: "🟠", planner.RENAME: "🟠", planner.MOVE: "🟠",
    planner.ARCHIVE: "🟠", planner.COPY_LATEST: "🔵", planner.FLAG: "🟡",
}


def app_dir() -> str:
    return os.path.dirname(os.path.abspath(__import__("sys").argv[0])) or os.getcwd()
