"""Единый движок аудита: сервер 4300_ПД и субподряд / transfer.validate."""
from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from docflow.config import AppConfig, Source, is_ignored  # noqa: E402
from docflow.registry import RegEntry  # noqa: E402
from docflow import planner  # noqa: E402
from docflow import scanner  # noqa: E402
from docflow import transfer as transfermod  # noqa: E402


def _entry(**kw) -> RegEntry:
    base = dict(oboznachenie="523-ПИР-24-ПЗ", key="ПЗ", razdel="1", short="ПЗ",
                doc_template="523-ПИР-24-ПЗ_Раздел ПД №1.pdf")
    base.update(kw)
    return RegEntry(**base)


def _cfg(tmp_path, **kw) -> AppConfig:
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    (root / "4300_ПД").mkdir(exist_ok=True)
    opts = dict(project_root=str(root), target_pd="4300_ПД")
    opts.update(kw)
    return AppConfig(**opts)


def _mk_version(folder, *, with_edit=True, with_pub=True, loose_pdf=None, pub_pdf=None):
    folder.mkdir(parents=True, exist_ok=True)
    if with_edit:
        (folder / "!EDIT").mkdir(exist_ok=True)
    if with_pub:
        (folder / "!PUBLISHED").mkdir(exist_ok=True)
    if loose_pdf:
        (folder / loose_pdf).write_bytes(b"%PDF")
    if pub_pdf and with_pub:
        (folder / "!PUBLISHED" / pub_pdf).write_bytes(b"%PDF")


def test_is_ignored_shared_by_scanner_and_planner():
    cfg = AppConfig(ignore_patterns=["*_DRAFT*", "Thumbs.db"])
    assert is_ignored("Thumbs.db", cfg.ignore_patterns)
    assert is_ignored(".docflow_crc.json", cfg.ignore_patterns)
    assert is_ignored("note_DRAFT.pdf", cfg.ignore_patterns)
    assert not is_ignored("ok.pdf", cfg.ignore_patterns)
    assert scanner._ignored("Thumbs.db", cfg.ignore_patterns) is True
    assert planner._ignored("Thumbs.db", cfg) is True
    assert scanner._ignored("ok.pdf", cfg.ignore_patterns) is False


def test_tolerated_dirs_merges_service_and_excludes():
    cfg = AppConfig(exclude_folders=["МоиЧерновики"], edit_dir="!EDIT",
                    published_dir="!PUBLISHED")
    e = _entry(edit_dir="РЕД", pub_dir="НЕРЕД")
    names = planner.tolerated_dirs(cfg, [e])
    assert "!EDIT" in names and "!PUBLISHED" in names
    assert "!LATEST" in names and "!REF" in names
    assert "МоиЧерновики" in names
    assert "РЕД" in names and "НЕРЕД" in names
    assert "!ARCHIVE" not in names  # обрабатывается отдельно


def test_audit_version_catalog_same_on_server_and_external(tmp_path):
    """Одинаковые нарушения → одинаковые check-коды в серверной и внешней папке."""
    cfg = _cfg(tmp_path)
    e = _entry()
    proj = "523-ПИР-24"

    srv = tmp_path / "proj" / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260601"
    ext = tmp_path / "sub" / "523-ПИР-24-ПЗ_260601"
    for folder in (srv, ext):
        _mk_version(folder, with_edit=False, with_pub=False, loose_pdf="loose.pdf")

    a_srv = planner._audit_version_catalog(cfg, e, str(srv), proj, "01_ПЗ", True)
    a_ext = planner._audit_version_catalog(cfg, e, str(ext), proj, "sub", True)
    assert {a.check for a in a_srv} == {a.check for a in a_ext}
    assert "edit_pub" in {a.check for a in a_srv}
    assert "loose_files" in {a.check for a in a_srv}


def test_validate_uses_shared_engine(tmp_path):
    cfg = _cfg(tmp_path)
    e = _entry()
    folder = tmp_path / "sub" / "523-ПИР-24-ПЗ_260601"
    _mk_version(folder, with_edit=False, with_pub=False, loose_pdf="x.pdf")

    via_planner = planner.validate_version_folder(e, str(folder), cfg, "523-ПИР-24")
    via_transfer = transfermod.validate(e, str(folder), cfg, "523-ПИР-24")
    assert via_planner == via_transfer
    assert any("нет папки" in i for i in via_transfer)
    assert any("файлы вне" in i for i in via_transfer)


def test_validate_foreign_shifr_and_pdf_completeness(tmp_path):
    cfg = _cfg(tmp_path)
    e = _entry()
    # чужой год в имени каталога
    bad = tmp_path / "sub" / "523-ПИР-23-ПЗ_260601"
    _mk_version(bad, pub_pdf="чужой.pdf")
    issues = transfermod.validate(e, str(bad), cfg, "523-ПИР-24")
    assert any("другой шифр/год" in i for i in issues)

    ok = tmp_path / "sub" / "523-ПИР-24-ПЗ_260602"
    _mk_version(ok, with_edit=True, with_pub=True)  # пустой !PUBLISHED
    issues2 = transfermod.validate(e, str(ok), cfg, "523-ПИР-24")
    assert any("нет pdf" in i for i in issues2)


def test_audit_doc_and_external_share_engine(tmp_path):
    """Папка документа на сервере и у субподряда вызывают один _audit_doc."""
    cfg = _cfg(tmp_path)
    e = _entry()
    proj = "523-ПИР-24"

    # серверная папка документа без !ARCHIVE и с каталогом версии без !EDIT/!PUBLISHED
    doc_srv = tmp_path / "proj" / "4300_ПД" / "01_ПЗ" / "523-ПИР-24-ПЗ"
    ver = doc_srv / "523-ПИР-24-ПЗ_260601"
    _mk_version(ver, with_edit=False, with_pub=False)

    acts_srv = planner._audit_doc(cfg, e, str(doc_srv), proj, "01_ПЗ", True)
    assert any(a.check == "doc_base" for a in acts_srv)
    assert any(a.check == "edit_pub" for a in acts_srv)

    # субподряд: тот же документ прямо в корне источника
    sub_root = tmp_path / "subcontractor"
    doc_ext = sub_root / "523-ПИР-24-ПЗ"
    ver_ext = doc_ext / "523-ПИР-24-ПЗ_260601"
    _mk_version(ver_ext, with_edit=False, with_pub=False)
    cfg.sources = [Source(name="Суб", path=str(sub_root), enabled=True)]

    acts_ext = planner.audit_external(cfg, [e])
    assert any(a.check == "doc_base" for a in acts_ext)
    assert any(a.check == "edit_pub" for a in acts_ext)


def test_audit_doc_renames_version_folder_with_file_title(tmp_path):
    cfg = _cfg(tmp_path)
    e = _entry(
        oboznachenie="523-ПИР-24-ТХ1", key="ТХ1", razdel="6", short="ТХ",
        doc_template="523-ПИР-24-ТХ1_Раздел ПД №6 Часть №1.pdf")
    doc = (tmp_path / "proj" / "4300_ПД" / "06_ТХ"
           / "523-ПИР-24-ТХ1")
    bad = doc / "523-ПИР-24-ТХ1_Раздел ПД №6 Часть №1_260727"
    _mk_version(bad)

    actions = planner._audit_doc(
        cfg, e, str(doc), "523-ПИР-24", "06_ТХ", True)

    rename = next(a for a in actions if a.kind == planner.RENAME
                  and a.src == str(bad))
    assert rename.dst == str(doc / "523-ПИР-24-ТХ1_260727")


def test_xml_from_composition_is_valid_in_published_folder(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.published_extensions.append(".xml")
    e = _entry()
    folder = (tmp_path / "proj" / "4300_ПД" / "01_ПЗ"
              / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260727")
    _mk_version(folder)
    xml = folder / "!PUBLISHED" / "523-ПИР-24-ПЗ_Раздел ПД №1.xml"
    xml.write_text("<document/>", encoding="utf-8")

    actions = planner._audit_version_catalog(
        cfg, e, str(folder), "523-ПИР-24", "01_ПЗ", True)

    assert not any(a.src == str(xml) for a in actions)


def test_audit_reports_each_missing_required_signature(tmp_path):
    cfg = _cfg(tmp_path)
    e = _entry(signers=["Иванов", "Раминская Юлия Александровна"])
    folder = (tmp_path / "proj" / "4300_ПД" / "01_ПЗ"
              / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260728")
    filename = "523-ПИР-24-ПЗ_Раздел ПД №1.pdf"
    _mk_version(folder, pub_pdf=filename)
    (folder / "!PUBLISHED" / f"{filename}_Иванов.sig").write_bytes(b"sig")

    actions = planner._audit_version_catalog(
        cfg, e, str(folder), "523-ПИР-24", "01_ПЗ", True)
    missing = [a for a in actions if a.check == "missing_signatures"]

    assert len(missing) == 1
    assert "Раминская Юлия Александровна" in missing[0].reason
    assert "Иванов" not in missing[0].reason


def test_audit_accepts_signer_before_document_extension(tmp_path):
    cfg = _cfg(tmp_path)
    e = _entry(signers=["Раминская Юлия Александровна"])
    folder = (tmp_path / "proj" / "4300_ПД" / "01_ПЗ"
              / "523-ПИР-24-ПЗ" / "523-ПИР-24-ПЗ_260728")
    filename = "523-ПИР-24-ПЗ_Раздел ПД №1.pdf"
    _mk_version(folder, pub_pdf=filename)
    (folder / "!PUBLISHED" /
     "523-ПИР-24-ПЗ_Раздел ПД №1_Раминская.pdf.sig").write_bytes(b"sig")

    actions = planner._audit_version_catalog(
        cfg, e, str(folder), "523-ПИР-24", "01_ПЗ", True)

    assert not any(a.check == "missing_signatures" for a in actions)


def test_structure_reads_signers_from_project_composition():
    from docflow import structure

    data = {"sections": [{"documents": [{
        "oboznachenie": "523-ПИР-24-ПЗ", "razdel": "1", "short": "ПЗ",
        "signers": ["Иванов", "Петров"]}]}]}

    entry = structure.to_entries(data)[0]

    assert entry.signers == ["Иванов", "Петров"]
