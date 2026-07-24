"""Тесты naming: _nospace, recognize, canonical_name."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.naming import (  # noqa: E402
    Matcher, _nospace, canonical_name, recognize,
)
from docflow.registry import RegEntry  # noqa: E402


def _entry(**kw) -> RegEntry:
    defaults = dict(
        oboznachenie="523-ПИР-24-ПЗ",
        key="ПЗ",
        razdel="1",
        short="ПЗ",
        doc_template="523-ПИР-24-ПЗ_Раздел ПД №1.pdf",
    )
    defaults.update(kw)
    return RegEntry(**defaults)


def test_nospace():
    assert _nospace("  A  B  ") == "ab"
    assert _nospace("") == ""
    assert _nospace(None) == ""


def test_canonical_name_keeps_ext_swap():
    e = _entry()
    assert canonical_name(e, ".pdf") == "523-ПИР-24-ПЗ_Раздел ПД №1.pdf"
    assert canonical_name(e, ".docx") == "523-ПИР-24-ПЗ_Раздел ПД №1.docx"
    assert canonical_name(_entry(doc_template=""), ".pdf") is None


def test_recognize_ok_and_date_from_name():
    m = Matcher([_entry()])
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "x.pdf")
        open(path, "wb").close()
        rec = recognize("523-ПИР-24-ПЗ_Раздел ПД №1_260615.pdf", path, m,
                        date_from_mtime=False)
    assert rec.matched
    assert rec.entry.key == "ПЗ"
    assert rec.date6 == "260615"
    assert rec.canonical == "523-ПИР-24-ПЗ_Раздел ПД №1.pdf"


def test_recognize_rejects_unknown():
    m = Matcher([_entry()])
    rec = recognize("random_file.pdf", r"C:\tmp\random_file.pdf", m,
                    date_from_mtime=False)
    assert not rec.matched
    assert "не распознан" in rec.reason


def test_recognize_rejects_compound_tail():
    m = Matcher([_entry()])
    rec = recognize("523-ПИР-24-ПЗ-Г.1.dwg", r"C:\tmp\x.dwg", m,
                    date_from_mtime=False)
    assert not rec.matched
