"""Перенос новых версий из субподрядных папок в 4300_ПД.

Работает как !LATEST, но в обратную сторону: сравнивает версии в папках
субподрядчиков (источники, не являющиеся серверными) с тем, что уже лежит
на сервере (4300_ПД / папка отчётов ИИ), и копирует ЦЕЛИКОМ папку версии
{обозначение}_{дата} в папку документа на сервере.

Старые версии на сервере не трогаем (их можно убрать в !ARCHIVE обычным
сканированием).

Атомарная замена папки версии (Windows / сетевые диски):
  1) копируем в соседний временный каталог ``{dest}.tmp_docflow``;
  2) если dest нет — ``os.rename(tmp, dest)``;
  3) если dest есть — rename-dance: dest → ``{dest}.bak_docflow``,
     tmp → dest, затем удаляем bak.
``os.replace`` надёжен для файлов; для каталогов на Windows используем
последовательность rename. При сбое на середине — откат bak → dest,
временные каталоги чистим; слияния old+new не оставляем (пропуск).
"""
from __future__ import annotations

import os
import shutil
import zlib
from typing import List, Optional, Tuple

from . import planner
from .config import AppConfig
from .naming import _nospace
from .registry import RegEntry, strip_proj_prefix

NEW = "🔵"     # на сервере этого документа ещё нет
NEWER = "🟠"   # у субподрядчика версия новее, чем на сервере
SAME = "🟢"    # на сервере уже не старее
DIFF = "🟣"    # та же дата версии, но состав/содержимое файлов отличается (!)
NONE_ = "⚪"   # у субподрядчиков версии нет

_SKIP = shutil.ignore_patterns("_", "*_DRAFT*", "*_draft*", "Thumbs.db",
                               "desktop.ini", "~$*", "*.tmp")


def external_sources(cfg: AppConfig) -> List:
    """Источники-субподрядчики: включённые папки, НЕ являющиеся серверными
    (не внутри 4300_ПД / 1100_ИИ). Пути — из ``planner._external_roots``."""
    roots = set(planner._external_roots(cfg))
    out = []
    for s in cfg.sources:
        if not getattr(s, "enabled", True):
            continue
        p = os.path.normpath(cfg.abspath(s.path))
        if p in roots:
            out.append(s)
    return out


def _server_newest(entry: RegEntry, cfg: AppConfig) -> Optional[str]:
    """Дата самой свежей версии документа на сервере (ггммдд) или None."""
    doc = planner._doc_folder(entry, cfg)
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    best = None
    if os.path.isdir(doc):
        for name in os.listdir(doc):
            if name == cfg.archive_name:
                continue
            full = os.path.join(doc, name)
            if not os.path.isdir(full):
                continue
            pfx, d6 = planner.cat_match(name)
            if pfx and _nospace(strip_proj_prefix(pfx)) == core:
                if best is None or (d6 or "") > best:
                    best = d6
    return best


def _scan_external(cfg: AppConfig, entries: List[RegEntry], sources) -> dict:
    """Самая свежая версия каждого документа среди субподрядных папок.
    Возвращает {ключ_документа: (дата, папка_версии, имя_источника)}."""
    cores = {_nospace(strip_proj_prefix(e.oboznachenie)): e for e in entries}
    found: dict = {}
    for s in sources:
        path = cfg.abspath(s.path)
        if not os.path.isdir(path):
            continue
        for dp, dns, _fns in os.walk(path):
            keep = []
            for dn in dns:
                pfx, d6 = planner.cat_match(dn)
                e = cores.get(_nospace(strip_proj_prefix(pfx))) if pfx else None
                if e is not None:
                    full = os.path.join(dp, dn)
                    cur = found.get(e.key)
                    if cur is None or (d6 or "") > (cur[0] or ""):
                        found[e.key] = (d6, full, s.name)
                    # внутрь каталога версии не углубляемся
                else:
                    keep.append(dn)
            dns[:] = keep
    return found


def _crc32(path: str, chunk: int = 1 << 20) -> Optional[int]:
    """Контрольная сумма CRC32 файла (быстрая, ловит подмену содержимого)."""
    c = 0
    try:
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(chunk), b""):
                c = zlib.crc32(b, c)
        return c & 0xFFFFFFFF
    except OSError:
        return None


def _fileset_crc(folder: str, cfg: AppConfig) -> dict:
    """{относительный_путь: CRC32} для всех значимых файлов папки версии."""
    out: dict = {}
    for dp, _dns, fns in os.walk(folder):
        for fn in fns:
            if planner._ignored(fn, cfg):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, folder).replace("\\", "/").lower()
            out[rel] = _crc32(full)
    return out


CRC_MANIFEST = ".docflow_crc.json"


def fix_crc(cfg: AppConfig, root: str) -> int:
    """Зафиксировать CRC32 всех файлов в папке (записать .docflow_crc.json).
    Используется для !LATEST и субподрядных папок — чтобы потом ловить перезапись."""
    import json
    if not root or not os.path.isdir(root):
        return 0
    fs = _fileset_crc(root, cfg)
    fs.pop(CRC_MANIFEST.lower(), None)
    p = os.path.join(root, CRC_MANIFEST)
    from .config import hide_file, unhide_file
    unhide_file(p)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"files": fs}, f, ensure_ascii=False, indent=2)
    hide_file(p)
    return len(fs)


def verify_crc(cfg: AppConfig, root: str):
    """Сверить текущие CRC32 с зафиксированными. Возвращает
    {changed, removed, added} (списки путей) или None, если фиксации нет."""
    import json
    p = os.path.join(root, CRC_MANIFEST)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            old = json.load(f).get("files", {})
    except (OSError, ValueError):
        return None
    now = _fileset_crc(root, cfg)
    now.pop(CRC_MANIFEST.lower(), None)
    changed = sorted(k for k in now if k in old and now[k] != old[k])
    removed = sorted(k for k in old if k not in now)
    added = sorted(k for k in now if k not in old)
    return {"changed": changed, "removed": removed, "added": added}


def _server_version_folder(entry: RegEntry, cfg: AppConfig, date: str) -> Optional[str]:
    """Папка версии на сервере с заданной датой (в т.ч. в !ARCHIVE) или None."""
    doc = planner._doc_folder(entry, cfg)
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    for d in (doc, os.path.join(doc, cfg.archive_name)):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            full = os.path.join(d, name)
            if not os.path.isdir(full):
                continue
            pfx, d6 = planner.cat_match(name)
            if pfx and _nospace(strip_proj_prefix(pfx)) == core and d6 == date:
                return full
    return None


def crc_diff(sub_folder: str, srv_folder: str, cfg: AppConfig):
    """Сравнение состава и содержимого (CRC32) двух папок версии.
    Возвращает (добавлено, удалено, изменено) — списки относительных путей."""
    a = _fileset_crc(sub_folder, cfg)
    b = _fileset_crc(srv_folder, cfg)
    added = sorted(k for k in a if k not in b)
    removed = sorted(k for k in b if k not in a)
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    return added, removed, changed


def _crc_hex(c) -> str:
    return f"{c:08X}" if c is not None else "—"


def _fileset_full(folder: str, cfg: AppConfig) -> dict:
    """{относительный_путь: {'crc':.., 'size':..}} для файлов папки (рекурсивно)."""
    out: dict = {}
    if not folder or not os.path.isdir(folder):
        return out
    for dp, _dns, fns in os.walk(folder):
        for fn in fns:
            if planner._ignored(fn, cfg):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, folder).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            out[rel] = {"crc": _crc32(full), "size": size}
    return out


def server_versions(entry: RegEntry, cfg: AppConfig) -> List[tuple]:
    """Все версии документа на сервере: [(дата, папка, в_архиве)] по возрастанию даты."""
    doc = planner._doc_folder(entry, cfg)
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    out = []
    for d, arch in ((doc, False), (os.path.join(doc, cfg.archive_name), True)):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            full = os.path.join(d, name)
            if not os.path.isdir(full):
                continue
            pfx, d6 = planner.cat_match(name)
            if pfx and _nospace(strip_proj_prefix(pfx)) == core:
                out.append((d6, full, arch))
    out.sort(key=lambda t: (t[0] or ""))
    return out


def folder_compare(sub_folder: str, srv_folder: str, cfg: AppConfig) -> List[dict]:
    """Пофайловое сравнение двух папок версии по CRC32 и именам. Ловит:
       • одинаковое имя, разный CRC32 (файл переписан);
       • одинаковый CRC32, но другое имя на сервере (переименовали);
       • файл есть только у одного из (два на сервере — один у субподрядчика).
    Возвращает строки {name, crc_sub, crc_srv, verdict, srv_name}."""
    a = _fileset_full(sub_folder, cfg)
    b = _fileset_full(srv_folder, cfg)
    rows: List[dict] = []
    used_b: set = set()
    for rel in sorted(a):
        ca = a[rel]["crc"]
        if rel in b:                                   # то же имя
            cb = b[rel]["crc"]
            used_b.add(rel)
            rows.append({"name": rel, "crc_sub": ca, "crc_srv": cb, "srv_name": rel,
                         "verdict": "совпадает" if ca == cb else "переписан (другой CRC32)"})
        else:                                          # имени нет на сервере
            match = [rb for rb in sorted(b) if rb not in used_b and b[rb]["crc"] == ca]
            if match:
                rb = match[0]
                used_b.add(rb)
                rows.append({"name": rel, "crc_sub": ca, "crc_srv": b[rb]["crc"],
                             "srv_name": rb,
                             "verdict": f"тот же файл, другое имя на сервере ({rb})"})
            else:
                rows.append({"name": rel, "crc_sub": ca, "crc_srv": None,
                             "srv_name": "", "verdict": "только у субподрядчика"})
    for rel in sorted(b):
        if rel in used_b:
            continue
        rows.append({"name": "", "crc_sub": None, "crc_srv": b[rel]["crc"],
                     "srv_name": rel, "verdict": "только на сервере"})
    return rows


def doc_detail(cfg: AppConfig, entry: RegEntry, sub_folder: str,
               sub_date: str) -> dict:
    """Данные для окна детального сравнения одного документа:
       все версии на сервере + пофайловое сравнение папки субподрядчика с
       версией сервера той же даты (если есть) или с самой свежей."""
    vers = server_versions(entry, cfg)
    target = None
    if sub_date:
        target = next((v for v in vers if v[0] == sub_date), None)
    if target is None and vers:
        target = vers[-1]                              # самая свежая
    srv_folder = target[1] if target else ""
    return {
        "oboznachenie": entry.oboznachenie,
        "versions": vers,                              # [(дата, папка, в_архиве)]
        "sub_folder": sub_folder,
        "sub_date": sub_date,
        "srv_folder": srv_folder,
        "srv_date": target[0] if target else None,
        "rows": folder_compare(sub_folder, srv_folder, cfg),
    }


def validate(entry: RegEntry, folder: str, cfg: AppConfig, proj: str) -> List[str]:
    """Проверка субподрядной папки версии перед переносом: структура и имена.
    Тот же движок, что аудит 4300_ПД / субподряда (``planner.validate_version_folder``)."""
    return planner.validate_version_folder(entry, folder, cfg, proj)


def compare(cfg: AppConfig, entries: List[RegEntry], sources=None) -> List[dict]:
    """Сводка по каждому документу: версия на сервере vs у субподрядчика +
    проверка структуры/имён субподрядной папки."""
    sources = sources if sources is not None else external_sources(cfg)
    found = _scan_external(cfg, entries, sources)
    proj = planner._project_shifr(entries)
    rows = []
    for e in entries:
        sub = found.get(e.key)
        srv = _server_newest(e, cfg)
        diff = None
        if not sub:
            status, sdate, folder, srcname = NONE_, None, None, None
        else:
            sdate, folder, srcname = sub
            if srv is None:
                status = NEW
            elif (sdate or "") > (srv or ""):
                status = NEWER
            else:
                status = SAME
        issues = validate(e, folder, cfg, proj) if folder else []
        # ПАРАНОИК-ПРОВЕРКА: если на сервере есть версия с ТОЙ ЖЕ датой —
        # сверяем состав и содержимое по CRC32 (вдруг подменили/добавили файл)
        if folder and sdate:
            srv_same = _server_version_folder(e, cfg, sdate)
            if srv_same:
                added, removed, changed = crc_diff(folder, srv_same, cfg)
                if added or removed or changed:
                    status = DIFF
                    diff = {"added": added, "removed": removed, "changed": changed}
                    issues.append(
                        f"та же версия {sdate}, но файлы отличаются от 4300_ПД "
                        f"(CRC32): новых +{len(added)}, нет −{len(removed)}, "
                        f"изменено {len(changed)}")
        rows.append({"entry": e, "server": srv, "sub": sdate, "folder": folder,
                     "source": srcname, "status": status, "issues": issues,
                     "diff": diff})
    return rows


_TMP_SUFFIX = ".tmp_docflow"
_BAK_SUFFIX = ".bak_docflow"


def _rmtree_quiet(path: str) -> None:
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _atomic_put_tree(src: str, dest: str) -> str:
    """Скопировать дерево src в dest атомарно через соседний temp.

    Возвращает ``"new"`` или ``"replaced"``. При ошибке после частичного
    rename пытается вернуть старый dest из bak; не оставляет смесь old+new.
    Бросает OSError / shutil.Error при окончательном провале.
    """
    parent = os.path.dirname(dest) or "."
    base = os.path.basename(dest)
    tmp = os.path.join(parent, base + _TMP_SUFFIX)
    bak = os.path.join(parent, base + _BAK_SUFFIX)

    # чужие leftovers от прошлого сбоя — убрать, чтобы не мешали
    _rmtree_quiet(tmp)
    _rmtree_quiet(bak)

    shutil.copytree(src, tmp, dirs_exist_ok=False, ignore=_SKIP)

    if not os.path.exists(dest):
        try:
            os.rename(tmp, dest)
        except OSError:
            _rmtree_quiet(tmp)
            raise
        return "new"

    # dest есть: rename-dance (без merge)
    moved_aside = False
    try:
        os.rename(dest, bak)
        moved_aside = True
        os.rename(tmp, dest)
    except OSError:
        if moved_aside and not os.path.exists(dest) and os.path.isdir(bak):
            try:
                os.rename(bak, dest)          # откат: вернуть старое
            except OSError:
                pass  # bak останется рядом — лучше, чем потерять dest
        _rmtree_quiet(tmp)
        raise
    _rmtree_quiet(bak)
    return "replaced"


def transfer(cfg: AppConfig, rows: List[dict]) -> Tuple[list, list]:
    """Копирует папку версии субподрядчика в папку документа на сервере как
    {обозначение}_{дата} (целиком), через временный соседний каталог.

    Если dest уже есть — атомарно заменяет (не dirs_exist_ok-merge).
    При сбое замены — пропуск с причиной, старое содержимое по возможности
    сохраняется. Возвращает (done, skipped):
      done    — [(обозначение, дата, \"new\"|\"replaced\"), ...];
      skipped — [(обозначение, дата, причина), ...].
    """
    done: List[tuple] = []
    skipped: List[tuple] = []
    for r in rows:
        e, folder, sdate = r["entry"], r.get("folder"), r.get("sub")
        if not folder or not sdate or not os.path.isdir(folder):
            continue
        doc = planner._doc_folder(e, cfg)
        os.makedirs(doc, exist_ok=True)
        dest = os.path.join(doc, f"{e.oboznachenie}_{sdate}")
        try:
            how = _atomic_put_tree(folder, dest)
        except (OSError, shutil.Error) as err:
            skipped.append((e.oboznachenie, sdate,
                            f"не удалось перенести «{os.path.basename(dest)}»: {err}"))
            continue
        done.append((e.oboznachenie, sdate, how))
    return done, skipped
