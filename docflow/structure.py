"""Состав проекта.

Состав проекта (разделы → документы) формируется ОДИН раз из эталонного
Excel и сохраняется в редактируемый файл project_structure.json. Дальше
программа работает с этим JSON: создаёт папки и распознаёт файлы.
Excel после этого нужен только при изменении состава.

Формат project_structure.json:
{
  "shifr": "523-ПИР-24",
  "sections": [
    { "code": "01_ПЗ", "razdel": "1", "documents": [
        { "oboznachenie": "523-ПИР-24-ПЗ", "short": "ПЗ",
          "razdel": "1", "podrazdel": "", "chast": "",
          "name": "Раздел 1. Пояснительная записка",
          "signers": ["Иванов", "Раминская Юлия Александровна"],
          "signature_level": "warn",
          "smeta": false,
          "doc_template": "523-ПИР-24-ПЗ_Раздел ПД №1_ггммдд.pdf",
          "iul_template": "523-ПИР-24-ПЗ_Раздел ПД №1_ггммдд_УЛ.pdf" }
    ] }
  ]
}
"""
from __future__ import annotations

import json
import os
import re
from typing import List

from .registry import RegEntry, strip_proj_prefix, section_code


def build_from_entries(entries: List[RegEntry], shifr: str = "",
                       smeta_codes=("СМ",), smeta_razdel="12",
                       razdel_codes=None, pub_ext=(".pdf", ".sig"),
                       smeta_ext=(".pdf", ".xlsx", ".gge", ".gsfx", ".sig"),
                       edit_dir="!EDIT", pub_dir="!PUBLISHED") -> dict:
    """Собирает структуру состава из документов реестра."""
    smeta_codes = {s.upper() for s in smeta_codes}
    sub_codes = {"ЛСР", "ОСР", "ВОР", "СВОР"}
    sections: dict[str, dict] = {}
    for e in entries:
        code = section_code(e.razdel, e.short, razdel_codes)
        sec = sections.setdefault(code, {"code": code, "razdel": str(e.razdel or ""),
                                         "documents": []})
        if any(d["oboznachenie"] == e.oboznachenie for d in sec["documents"]):
            continue
        is_smeta = (e.short or "").strip().upper() in smeta_codes
        if not is_smeta:
            try:
                is_smeta = int(float(e.razdel)) == int(float(smeta_razdel))
            except (ValueError, TypeError):
                pass
        sub = is_smeta or (e.short or "").strip().upper() in sub_codes
        sec["documents"].append({
            "oboznachenie": e.oboznachenie, "short": e.short,
            "razdel": str(e.razdel or ""), "podrazdel": str(e.podrazdel or ""),
            "chast": str(e.chast or ""), "tom": str(e.tom or ""),
            "name": e.name, "smeta": is_smeta,
            "signers": list(e.signers or []),
            "signature_level": e.signature_level or "warn",
            "extensions": list(smeta_ext if sub else pub_ext),  # допускаемые типы
            # деление каталога версии на ред./публ. (сметы — без деления, одной папкой)
            "split": not is_smeta, "edit_dir": edit_dir, "pub_dir": pub_dir,
            "doc_template": e.doc_template, "iul_template": e.iul_template,
        })

    def _key(c):
        m = re.match(r"\s*(\d+)", c)
        return (int(m.group(1)) if m else 999, c)

    return {"shifr": shifr,
            "sections": [sections[c] for c in sorted(sections, key=_key)]}


_RE_VER_SUFFIX = re.compile(r"_(?:\d{6}|\d{2}[.\-]\d{2}[.\-]\d{2})$")


_RE_II_SECTION = re.compile(r"^\d+\s*[_\-]\s*(.+)$")   # 01_ИГДИ, 08_ТО …


def build_ii_sections(ii_root_abs: str, ii_types, proj_shifr: str = "",
                      pub_ext=(".pdf", ".sig"),
                      edit_dir="!EDIT", pub_dir="!PUBLISHED") -> List[dict]:
    """Состав ИИ (инженерные изыскания).

    Программа НЕ знает заранее, сколько видов/томов изысканий в проекте — состав
    берётся с диска: каждая папка вида «NN_XXX» (01_ИГДИ, 08_ТО …) в корне
    отчётов становится разделом. Настроенные ii_types добавляются как «пустые»
    разделы (если папки ещё нет). В каждом виде на диске может быть НЕСКОЛЬКО
    томов-документов: ИГИ, ИГИ-Т.1, ИГИ-Г, ИГИ.1, ИГИ.2 — каждый отдельным
    документом. Если папок нет — один документ {шифр}-{вид}."""
    def _doc(oboz, short, secname):
        return {"oboznachenie": oboz, "short": short, "section": secname, "area": "ИИ",
                "razdel": "", "podrazdel": "", "chast": "",
                "name": f"Инженерные изыскания — {strip_proj_prefix(oboz)}",
                "signers": [],
                "signature_level": "warn",
                "smeta": False, "extensions": list(pub_ext), "split": True,
                "edit_dir": edit_dir, "pub_dir": pub_dir,
                "doc_template": "", "iul_template": ""}

    # 1) разделы с диска (все папки NN_XXX) + 2) настроенные виды (на случай пустого проекта)
    sections: dict[str, str] = {}        # secname -> short
    if os.path.isdir(ii_root_abs):
        for name in sorted(os.listdir(ii_root_abs)):
            if not os.path.isdir(os.path.join(ii_root_abs, name)):
                continue
            m = _RE_II_SECTION.match(name)
            if m:
                sections[name] = m.group(1).strip()
    for t in ii_types:
        code, short = str(t.get("code", "")), str(t.get("short", ""))
        secname = f"{code}_{short}" if code else short
        sections.setdefault(secname, short)

    out: List[dict] = []
    for secname in sorted(sections):
        short = sections[secname]
        sdir = os.path.join(ii_root_abs, secname)
        # вид + возможная часть: ИГИ, ИГИ-Т.1, ИГИ-Г, ИГИ.1 …  (но не каталог версии)
        pat = re.compile(rf"^{re.escape(short)}(?:[-.].*)?$", re.IGNORECASE)
        docs = []
        seen = set()
        if os.path.isdir(sdir):
            for name in sorted(os.listdir(sdir)):
                full = os.path.join(sdir, name)
                if not os.path.isdir(full) or _RE_VER_SUFFIX.search(name):
                    continue                      # файл или каталог версии — пропускаем
                core = strip_proj_prefix(name)
                if pat.match(core) and name.lower() not in seen:
                    seen.add(name.lower())
                    docs.append(_doc(name, short, secname))
        if not docs:                              # ничего не сдано — один документ по шифру
            oboz = f"{proj_shifr}-{short}" if proj_shifr else short
            docs = [_doc(oboz, short, secname)]
        out.append({"code": secname, "area": "ИИ", "documents": docs})
    return out


def to_entries(structure: dict) -> List[RegEntry]:
    """Обратно в список RegEntry для распознавателя/планировщика."""
    out: List[RegEntry] = []
    for key in ("sections", "ii_sections"):
        for sec in structure.get(key, []):
            for d in sec.get("documents", []):
                oboz = d.get("oboznachenie", "")
                if not oboz:
                    continue
                out.append(RegEntry(
                    oboznachenie=oboz, key=strip_proj_prefix(oboz),
                    razdel=str(d.get("razdel", "")), podrazdel=str(d.get("podrazdel", "")),
                    chast=str(d.get("chast", "")), short=d.get("short", ""),
                    tom=str(d.get("tom", "")),
                    name=d.get("name", ""), status="",
                    doc_template=d.get("doc_template", ""),
                    iul_template=d.get("iul_template", ""),
                    extensions=list(d.get("extensions", [])),
                    signers=list(d.get("signers", [])),
                    signature_level=str(d.get("signature_level", "warn") or "warn").lower(),
                    area=d.get("area", "ПД"), section=d.get("section", ""),
                    split=bool(d.get("split", not d.get("smeta", False))),
                    edit_dir=d.get("edit_dir", ""), pub_dir=d.get("pub_dir", ""),
                ))
    return out


def count_documents(structure: dict) -> int:
    return sum(len(s.get("documents", []))
               for key in ("sections", "ii_sections")
               for s in structure.get(key, []))


def save(structure: dict, path: str) -> None:
    path = os.path.normpath(path)                 # убираем двойные // и т.п.
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)


def load(path: str) -> dict:
    with open(os.path.normpath(path), "r", encoding="utf-8") as f:
        return json.load(f)
