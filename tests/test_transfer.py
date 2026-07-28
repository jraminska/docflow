"""Тесты transfer: атомарная копия/замена папки версии."""
from __future__ import annotations

import os
import json
import sys
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig  # noqa: E402
from docflow.registry import RegEntry  # noqa: E402
from docflow import transfer as transfermod  # noqa: E402


def _entry() -> RegEntry:
    return RegEntry(oboznachenie="523-ПИР-24-ПЗ", key="ПЗ", razdel="1", short="ПЗ",
                    doc_template="523-ПИР-24-ПЗ_Раздел ПД №1.pdf")


def test_transfer_copies_when_absent(tmp_path):
    root = tmp_path / "proj"
    (root / "4300_ПД").mkdir(parents=True)
    src = tmp_path / "sub" / "523-ПИР-24-ПЗ_260602"
    src.mkdir(parents=True)
    (src / "a.pdf").write_text("ok", encoding="utf-8")

    cfg = AppConfig(project_root=str(root), target_pd="4300_ПД")
    done, skipped = transfermod.transfer(
        cfg, [{"entry": _entry(), "folder": str(src), "sub": "260602"}])
    assert skipped == []
    assert done == [("523-ПИР-24-ПЗ", "260602", "new")]
    dest = root / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260602"
    assert (dest / "a.pdf").read_text(encoding="utf-8") == "ok"
    # временный каталог не остаётся
    assert not (dest.parent / (dest.name + transfermod._TMP_SUFFIX)).exists()


def test_transfer_replaces_existing_atomically(tmp_path):
    """Dest уже есть — заменяем целиком (без merge leftover-файлов)."""
    root = tmp_path / "proj"
    pd = root / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ"
    pd.mkdir(parents=True)
    existing = pd / "523-ПИР-24-ПЗ_260601"
    existing.mkdir()
    (existing / "x.pdf").write_text("old", encoding="utf-8")
    (existing / "stale.pdf").write_text("leftover", encoding="utf-8")

    src = tmp_path / "sub" / "523-ПИР-24-ПЗ_260601"
    src.mkdir(parents=True)
    (src / "x.pdf").write_text("new", encoding="utf-8")
    (src / "y.pdf").write_text("extra", encoding="utf-8")

    cfg = AppConfig(project_root=str(root), target_pd="4300_ПД")
    done, skipped = transfermod.transfer(
        cfg, [{"entry": _entry(), "folder": str(src), "sub": "260601"}])
    assert skipped == []
    assert done == [("523-ПИР-24-ПЗ", "260601", "replaced")]
    assert (existing / "x.pdf").read_text(encoding="utf-8") == "new"
    assert (existing / "y.pdf").exists()
    # старый leftover не остался (не merge)
    assert not (existing / "stale.pdf").exists()
    assert not (pd / (existing.name + transfermod._BAK_SUFFIX)).exists()
    assert not (pd / (existing.name + transfermod._TMP_SUFFIX)).exists()


def test_transfer_skips_and_restores_on_replace_failure(tmp_path):
    """Если rename temp→dest падает после отвода dest — откатываем bak."""
    root = tmp_path / "proj"
    pd = root / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ"
    pd.mkdir(parents=True)
    existing = pd / "523-ПИР-24-ПЗ_260601"
    existing.mkdir()
    (existing / "x.pdf").write_text("old", encoding="utf-8")

    src = tmp_path / "sub" / "523-ПИР-24-ПЗ_260601"
    src.mkdir(parents=True)
    (src / "x.pdf").write_text("new", encoding="utf-8")

    cfg = AppConfig(project_root=str(root), target_pd="4300_ПД")
    dest = str(existing)
    bak = dest + transfermod._BAK_SUFFIX
    real_rename = os.rename
    calls = {"n": 0}

    def flaky_rename(src_p, dst_p):
        calls["n"] += 1
        # первый rename (dest→bak) — ок; второй (tmp→dest) — падаем
        if calls["n"] == 2 and os.path.normpath(dst_p) == os.path.normpath(dest):
            raise OSError("simulated network failure")
        return real_rename(src_p, dst_p)

    with mock.patch("os.rename", side_effect=flaky_rename):
        done, skipped = transfermod.transfer(
            cfg, [{"entry": _entry(), "folder": str(src), "sub": "260601"}])

    assert done == []
    assert len(skipped) == 1
    assert "не удалось" in skipped[0][2]
    # старое содержимое на месте (откат из bak)
    assert existing.is_dir()
    assert (existing / "x.pdf").read_text(encoding="utf-8") == "old"
    assert not os.path.isdir(bak)
    assert not os.path.isdir(dest + transfermod._TMP_SUFFIX)


def test_crc_manifest_is_shared_v2_and_reuses_unchanged_files(tmp_path):
    root = tmp_path / "!LATEST"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"content")
    cfg = AppConfig(project_root=str(tmp_path))

    assert transfermod.fix_crc(cfg, str(root)) == 1
    manifest = json.loads((root / transfermod.CRC_MANIFEST).read_text("utf-8"))
    assert manifest["version"] == 2
    assert set(manifest["files"]["a.pdf"]) == {"crc", "size", "mtime_ns"}

    with mock.patch.object(transfermod, "_crc32") as crc:
        result = transfermod.verify_crc(cfg, str(root))
    crc.assert_not_called()
    assert result["reused"] == 1
    assert result["hashed"] == 0
    assert not result["changed"]


def test_crc_verify_rehashes_only_changed_metadata(tmp_path):
    root = tmp_path / "!LATEST"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"first")
    (root / "b.pdf").write_bytes(b"stable")
    cfg = AppConfig(project_root=str(tmp_path))
    transfermod.fix_crc(cfg, str(root))

    (root / "a.pdf").write_bytes(b"new content with another size")
    real_crc = transfermod._crc32
    with mock.patch.object(transfermod, "_crc32", wraps=real_crc) as crc:
        result = transfermod.verify_crc(cfg, str(root))
    assert crc.call_count == 1
    assert result["hashed"] == 1
    assert result["reused"] == 1
    assert result["changed"] == ["a.pdf"]
