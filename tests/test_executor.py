"""Тесты executor: _unique_dst, stop_on_error и journal."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.executor import (  # noqa: E402
    _atomic_write_text, _unique_dst, execute, read_journal,
)
from docflow.planner import Action, MKSTRUCT, RENAME  # noqa: E402


def test_unique_dst_no_conflict(tmp_path):
    p = tmp_path / "a.pdf"
    assert _unique_dst(str(p)) == str(p)


def test_unique_dst_adds_suffix(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_text("1", encoding="utf-8")
    got = _unique_dst(str(p))
    assert got == str(tmp_path / "a (2).pdf")
    (tmp_path / "a (2).pdf").write_text("2", encoding="utf-8")
    got2 = _unique_dst(str(p))
    assert got2 == str(tmp_path / "a (3).pdf")


def test_execute_stops_on_first_error(tmp_path):
    missing = str(tmp_path / "no_such.pdf")
    ok_dir = tmp_path / "struct"
    journal = str(tmp_path / "journal.jsonl")
    acts = [
        Action(RENAME, missing, str(tmp_path / "b.pdf"), selected=True),
        Action(MKSTRUCT, str(ok_dir), "", selected=True,
               mkdirs=[str(ok_dir / "!ARCHIVE")]),
    ]
    results = execute(acts, journal, stop_on_error=True)
    assert len(results) == 1
    assert not results[0].ok
    assert not (ok_dir / "!ARCHIVE").exists()


def test_execute_continue_on_error(tmp_path):
    missing = str(tmp_path / "no_such.pdf")
    arch = tmp_path / "doc" / "!ARCHIVE"
    journal = str(tmp_path / "journal.jsonl")
    acts = [
        Action(RENAME, missing, str(tmp_path / "b.pdf"), selected=True),
        Action(MKSTRUCT, str(tmp_path / "doc"), "", selected=True,
               mkdirs=[str(arch)]),
    ]
    results = execute(acts, journal, stop_on_error=False)
    assert len(results) == 2
    assert not results[0].ok
    assert results[1].ok
    assert arch.is_dir()


def test_journal_atomic_write_and_skips_bad_lines(tmp_path):
    jnl = tmp_path / "journal.jsonl"
    _atomic_write_text(str(jnl), '{"batch":"1","ops":[]}\nnot-json\n{"batch":"2","ops":[]}\n')
    batches = read_journal(str(jnl))
    assert [b["batch"] for b in batches] == ["1", "2"]
    # после успешной записи tmp не остаётся рядом
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".docflow_jnl_")]
    assert leftovers == []
