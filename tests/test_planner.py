"""Тесты planner: cat_match и коллизии dst → FLAG."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.planner import (  # noqa: E402
    Action, FLAG, MKCATALOG, MOVE, RENAME, TRANSFER, cat_match, _flag_dst_collisions,
)


@pytest.mark.parametrize("name,prefix,date6", [
    ("523-ПИР-24-ПЗ_260619", "523-ПИР-24-ПЗ", "260619"),
    ("523-ПИР-24-ПЗУ1_23.09.25", "523-ПИР-24-ПЗУ1", "250923"),
    ("523-ПИР-24-ИОС1.1_01-06-26", "523-ПИР-24-ИОС1.1", "260601"),
])
def test_cat_match_ok(name, prefix, date6):
    p, d = cat_match(name)
    assert p == prefix
    assert d == date6


@pytest.mark.parametrize("name", [
    "523-ПИР-24-ПЗ",
    "!ARCHIVE",
    "readme.txt",
    "523-ПИР-24-ПЗ_26061",   # 5 цифр — не дата
])
def test_cat_match_none(name):
    assert cat_match(name) == (None, None)


def test_flag_dst_collisions_marks_dupes():
    dst = os.path.normpath(r"C:\srv\01_ПЗ\doc\file.pdf")
    acts = [
        Action(MKCATALOG, r"C:\a\one.pdf", dst, key="ПЗ", group="01_ПЗ"),
        Action(RENAME, r"C:\a\two.pdf", dst, key="ПЗ", group="01_ПЗ"),
        Action(RENAME, r"C:\a\ok.pdf", r"C:\srv\other.pdf", key="ПЗ", group="01_ПЗ"),
    ]
    out = _flag_dst_collisions(acts, [".sig"])
    kinds = [a.kind for a in out]
    assert kinds.count(FLAG) == 2
    assert RENAME in kinds
    flagged = [a for a in out if a.kind == FLAG]
    assert all(not a.selected for a in flagged)
    assert "претендует несколько" in flagged[0].reason


def test_flag_dst_collisions_skips_sig():
    dst = os.path.normpath(r"C:\srv\file.sig")
    acts = [
        Action(MOVE, r"C:\a\one.sig", dst, key="ПЗ"),
        Action(MOVE, r"C:\a\two.sig", dst, key="ПЗ"),
    ]
    out = _flag_dst_collisions(acts, [".sig"])
    assert all(a.kind == MOVE for a in out)


def test_transfer_constant_kept_for_compat():
    assert TRANSFER == "TRANSFER"
