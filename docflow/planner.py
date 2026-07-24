"""Формирование плана действий.

Порядок наведения порядка в 4300_ПД (именно в таком приоритете):
    1. MKCATALOG  — посторонний файл лежит прямо в папке раздела:
                    сформировать каталог {обозначение}_{дата} и перенести в него.
    2. ARCHIVE_FOLDER — есть две папки-версии одного тома: старую целиком в !ARCHIVE.
    3. RENAME     — файл внутри каталога назван не по эталону → переименовать.
    4. MOVE       — файл лежит не в той подпапке (pdf не в !PUBLISHED и т.п.).
    5. ARCHIVE    — устаревший одиночный файл перенести в архив.
И только ПОТОМ, отдельной кнопкой, формируется комплект:
    COPY_LATEST  — копировать актуальную версию в 8300_ВыпускПД\\!LATEST.

    FLAG          — файл требует внимания (не распознан / нет даты и т.п.)

Структура тома в 4300_ПД (для всех разделов, кроме сметы):
    {раздел}\\{обозначение}_{ггммдд}\\
        !EDIT        — редактируемые форматы (docx, dwg, xlsx …)
        !PUBLISHED   — pdf и sig (с подписями)
Для сметной документации (раздел 12 / шифр СМ) — только папка с версией:
    12_СМ\\{обозначение}_{ггммдд}\\<файлы>

План всегда формируется как ПРЕДЛОЖЕНИЕ. Ничего не выполняется, пока
пользователь не подтвердит (см. executor).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import registry
from .config import AppConfig, SERVICE_DIRS, is_ignored
from .naming import recognize, Recognized, Matcher, _nospace, canonical_name
from .registry import RegEntry, strip_proj_prefix, section_code
from .scanner import FileRec

MKSTRUCT = "MKSTRUCT"            # в разделе нет базовой структуры (нет !ARCHIVE)
# TRANSFER оставлен для совместимости UI/журнала; план скана его не создаёт —
# перенос на сервер только через TransferDialog (transfer.transfer).
TRANSFER = "TRANSFER"
MKCATALOG = "MKCATALOG"
ARCHIVE_FOLDER = "ARCHIVE_FOLDER"
RENAME = "RENAME"
MOVE = "MOVE"
NORMALIZE = "NORMALIZE"          # нестандартную папку разложить по !EDIT/!PUBLISHED
ARCHIVE = "ARCHIVE"
COPY_LATEST = "COPY_LATEST"
FLAG = "FLAG"

# порядок приоритета действий (для сортировки и нумерации шагов)
KIND_ORDER = {MKSTRUCT: 0, TRANSFER: 1, MKCATALOG: 2, ARCHIVE_FOLDER: 3, RENAME: 4,
              MOVE: 5, NORMALIZE: 6, ARCHIVE: 7, COPY_LATEST: 8, FLAG: 9}

KIND_TITLE = {
    MKSTRUCT: "Создать базовую структуру",
    TRANSFER: "Перенести на сервер",
    MKCATALOG: "Сформировать каталог и перенести",
    ARCHIVE_FOLDER: "Каталог-дубль → в архив",
    RENAME: "Переименовать в каталоге",
    MOVE: "Переместить в нужную папку",
    NORMALIZE: "Разложить по !EDIT/!PUBLISHED",
    ARCHIVE: "Старый файл → в архив",
    COPY_LATEST: "В комплект !LATEST",
    FLAG: "Требует внимания",
}

# действия, означающие нарушение структуры (раздел «красный/оранжевый»)
STRUCTURE_KINDS = {MKSTRUCT, TRANSFER, MKCATALOG, ARCHIVE_FOLDER, RENAME, MOVE, ARCHIVE}

# каталог версии: «523-ПИР-24-ПЗ_260619» или с точечной датой «…_23.09.25»
RE_CATALOG = re.compile(r"^(.*)_(\d{6})$")
RE_CAT_ANY = re.compile(r"^(.*)_(\d{6}|\d{2}[.\-]\d{2}[.\-]\d{2})$")


def _check(cfg: AppConfig, cid: str) -> bool:
    """Включена ли проверка cid (по умолчанию — да)."""
    return bool(getattr(cfg, "checks", None) is None or cfg.checks.get(cid, True))


def _ignored(name: str, cfg: AppConfig) -> bool:
    """Служебный/временный файл (Thumbs.db, *.tmp, *_DRAFT* …) — не трогаем."""
    return is_ignored(name, cfg.ignore_patterns)


def tolerated_dirs(cfg: AppConfig, entries: Optional[List[RegEntry]] = None) -> set:
    """Служебные имена папок, которые не считаем посторонними.
    !ARCHIVE обрабатывается отдельно (допустимость зависит от места)."""
    names = {d for d in SERVICE_DIRS if d not in (cfg.archive_name, "!ARCHIVE")}
    names.update({cfg.edit_dir, cfg.published_dir, "!REF"})
    names |= set(cfg.exclude_folders or [])
    for e in entries or []:
        names.add(_edit_of(e, cfg))
        names.add(_pub_of(e, cfg))
    return names


def cat_match(name: str):
    """Распознаёт имя каталога версии. Возвращает (префикс, дата_ггммдд) или (None, None).
    Понимает и ггммдд, и точечную дату дд.мм.гг → нормализует в ггммдд."""
    m = RE_CAT_ANY.match(name)
    if not m:
        return None, None
    prefix, d = m.group(1), m.group(2)
    if len(d) == 6 and d.isdigit():
        date6 = d
    else:
        dd, mm, yy = re.split(r"[.\-]", d)
        date6 = yy + mm + dd
    return prefix, date6


@dataclass
class Action:
    kind: str
    src: str
    dst: str = ""
    reason: str = ""
    key: str = ""           # шифр-ключ документа
    selected: bool = True   # отмечен ли для выполнения
    is_change: bool = False # относится ли к изменившимся с прошлого скана
    mkdirs: List[str] = field(default_factory=list)  # папки, которые создать
    group: str = ""         # раздел для группировки в дереве (01_ПЗ, 02_ПЗУ …)
    node: str = ""          # абсолютный путь узла дерева, к которому привязано действие
    payload: dict = field(default_factory=dict)      # данные для отдельных видов действий
    check: str = ""         # id проверки, породившей действие (для значимости/фильтра)

    @property
    def title(self) -> str:
        return KIND_TITLE.get(self.kind, self.kind)


def _area_root(entry: RegEntry, cfg: AppConfig) -> str:
    """Корень области: 4300_ПД для ПД, 1100_ИИ/04_Отчеты для ИИ."""
    return cfg.ii_abs if getattr(entry, "area", "ПД") == "ИИ" else cfg.target_pd_abs


def _section_folder(entry: RegEntry, cfg: AppConfig) -> str:
    """01_ПЗ, 04_КР, 05_ИОС, 12_СМ, 13_ИД — по номеру раздела.
    Для ИИ раздел задан явно (01_ИГДИ, 02_ИГИ …) в entry.section."""
    if getattr(entry, "section", ""):
        return entry.section
    return section_code(entry.razdel, entry.short or entry.key, cfg.razdel_codes)


def _first_component(rel: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", rel or "") if p]
    return parts[0] if parts else "Прочее"


def _is_smeta(entry: RegEntry, cfg: AppConfig) -> bool:
    if getattr(entry, "area", "ПД") == "ИИ":
        return False
    if (entry.short or "").strip().upper() in {s.upper() for s in cfg.smeta_short_codes}:
        return True
    try:
        return int(float(entry.razdel)) == int(float(cfg.smeta_razdel))
    except (ValueError, TypeError):
        return False


def _edit_of(entry: RegEntry, cfg: AppConfig) -> str:
    """Имя папки редактируемых форматов для тома (своё или из настроек)."""
    return getattr(entry, "edit_dir", "") or cfg.edit_dir


def _pub_of(entry: RegEntry, cfg: AppConfig) -> str:
    """Имя папки публикуемых файлов для тома (своё или из настроек).
    Именно из неё содержимое копируется в !LATEST."""
    return getattr(entry, "pub_dir", "") or cfg.published_dir


def _splits(entry: RegEntry, cfg: AppConfig) -> bool:
    """Делится ли том на две папки (ред./публ.). По умолчанию да; сметы — нет."""
    if not getattr(entry, "split", True):
        return False
    return not _is_smeta(entry, cfg)


def _loose_catalog(entry: RegEntry, cfg: AppConfig) -> bool:
    """Версия хранится одной папкой (без деления на ред./публ.) — только сметы
    или том, у которого явно отключено деление."""
    return not _splits(entry, cfg)


RE_SMETA_PARENT = re.compile(r"^(.*?-СМ\d+)", re.IGNORECASE)


def _smeta_parent(oboz: str) -> str:
    """Для ЛСР/ОСР/ВОР возвращает родительскую папку-часть:
    523-ПИР-24-СМ3_01-01-01_ЛСР → 523-ПИР-24-СМ3 ;
    523-ПИР-24-СМ4-ПОС-ВОР1     → 523-ПИР-24-СМ4 ; иначе пусто."""
    if re.search(r"(_ЛСР|_ОСР|-ВОР|_ВОР|-ЛСР|-ОСР|-СВОР|_СВОР)", oboz, re.IGNORECASE):
        m = RE_SMETA_PARENT.match(oboz)
        if m and m.group(1) != oboz:
            return m.group(1)
    return ""


def _doc_folder(entry: RegEntry, cfg: AppConfig) -> str:
    """Папка документа без даты: 4300_ПД/02_ПЗУ/523-ПИР-24-ПЗУ1.
    Для смет ЛСР/ОСР/ВОР — на уровень глубже: 12_СМ/523-ПИР-24-СМ3/<обозначение>."""
    base = os.path.join(_area_root(entry, cfg), _section_folder(entry, cfg))
    parent = _smeta_parent(entry.oboznachenie)
    if parent:
        return os.path.join(base, parent, entry.oboznachenie)
    return os.path.join(base, entry.oboznachenie)


def _doc_destination(entry: RegEntry, date6: str, ext: str, is_active: bool,
                     cfg: AppConfig) -> tuple[str, List[str]]:
    """Возвращает (папку для файла, список папок-структуры для создания).

    Уровни: {раздел}/{обозначение}/[!ARCHIVE/]{обозначение}_{дата}/[!EDIT|!PUBLISHED]
    """
    doc_abs = _doc_folder(entry, cfg)
    version = f"{entry.oboznachenie}_{date6}"
    base = os.path.join(doc_abs, version) if is_active \
        else os.path.join(doc_abs, cfg.archive_name, version)

    if not _splits(entry, cfg):              # сметы/неделимые — одной папкой
        return base, [os.path.join(doc_abs, cfg.archive_name), base]

    edit_dir, pub_dir = _edit_of(entry, cfg), _pub_of(entry, cfg)
    allowed = entry.extensions or cfg.published_extensions   # типы для публ. папки тома
    sub = pub_dir if ext.lower() in {e.lower() for e in allowed} else edit_dir
    mkdirs = [os.path.join(doc_abs, cfg.archive_name),
              os.path.join(base, edit_dir), os.path.join(base, pub_dir)]
    return os.path.join(base, sub), mkdirs


def _catalog_of(path: str, entry: RegEntry) -> tuple[Optional[str], Optional[str]]:
    """Если файл лежит внутри каталога версии {обозначение}_{дата} —
    вернуть (путь_каталога, дата). Проверяем родителя и деда (на случай
    подпапок !EDIT/!PUBLISHED)."""
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    for cand in (os.path.dirname(path), os.path.dirname(os.path.dirname(path))):
        prefix, date6 = cat_match(os.path.basename(cand))
        if prefix and _nospace(strip_proj_prefix(prefix)) == core:
            return cand, date6
    return None, None


def _recognize_via_catalog(fr: FileRec, matcher: Matcher) -> Optional[Recognized]:
    """Опознать файл по каталогу, в котором он лежит (если имя «кривое»).
    Папка {обозначение}_{дата} однозначно указывает на документ."""
    for cand in (os.path.dirname(fr.path), os.path.dirname(os.path.dirname(fr.path))):
        prefix, date6 = cat_match(os.path.basename(cand))
        if not prefix:
            continue
        entry = matcher.by_core(_nospace(strip_proj_prefix(prefix)))
        if not entry:
            continue
        ext = os.path.splitext(fr.name)[1]
        canon = canonical_name(entry, ext)
        if canon:
            return Recognized(matched=True, entry=entry, date6=date6, canonical=canon)
    return None


def _on_server(path: str, cfg: AppConfig) -> bool:
    """Файл уже лежит в серверной папке ПД (а не у субподрядчика)?"""
    base = os.path.normpath(cfg.target_pd_abs)
    return os.path.normpath(path).startswith(base + os.sep)


def build_plan(inventory: List[FileRec],
               matcher: Matcher,
               cfg: AppConfig,
               changed_paths: Optional[set] = None) -> List[Action]:
    """Главный план наведения порядка в 4300_ПД (см. модульную справку)."""
    changed_paths = changed_paths or set()
    actions: List[Action] = []

    # 1) распознавание (если имя не распознано — пробуем по каталогу)
    matched: List[tuple[FileRec, Recognized]] = []
    for fr in inventory:
        rec = recognize(fr.name, fr.path, matcher, cfg.date_from_mtime_if_absent)
        is_chg = fr.path in changed_paths
        good = rec.matched and bool(rec.canonical)
        if not good and fr.category == "ПД":
            alt = _recognize_via_catalog(fr, matcher)
            if alt:
                rec, good = alt, True
        if not good:
            grp = _section_folder(rec.entry, cfg) if (rec.entry and fr.category == "ПД") \
                else _first_component(fr.rel)
            if fr.category == "ПД":
                reason = f"посторонний файл (не по структуре): {fr.name}"
            else:
                reason = rec.reason or "не распознано"
            actions.append(Action(FLAG, fr.path, reason=reason,
                                  key=(rec.entry.key if rec.entry else ""),
                                  selected=False, is_change=is_chg, group=grp,
                                  node=os.path.dirname(fr.path)))  # показать в его папке
        else:
            matched.append((fr, rec))

    # версия = дата папки-каталога (если файл уже в каталоге), иначе из файла/mtime
    def _vdate(fr, rec) -> str:
        _cp, cd = _catalog_of(fr.path, rec.entry)
        return cd or rec.date6 or "000000"

    # 2) самая свежая версия по документу + каталоги версий
    newest_date: Dict[str, str] = {}
    catalogs: Dict[str, Dict[str, str]] = {}   # key -> {catalog_path: date}
    for fr, rec in matched:
        k = rec.entry.key
        vd = _vdate(fr, rec)
        if vd > newest_date.get(k, ""):
            newest_date[k] = vd
        # каталоги-дубли ищем только на сервере (у субподрядчика не трогаем)
        if fr.category == "ПД" and _on_server(fr.path, cfg):
            cat_path, cat_date = _catalog_of(fr.path, rec.entry)
            if cat_path:
                catalogs.setdefault(k, {})[cat_path] = cat_date

    # 3) каталоги-дубли (старее самой свежей версии) — целиком в !ARCHIVE
    archive_dirs: set = set()
    for k, cats in catalogs.items():
        nd = newest_date.get(k, "")
        for cat_path, cat_date in cats.items():
            if cat_date < nd:
                archive_dirs.add(os.path.normpath(cat_path))
                section_dir = os.path.dirname(cat_path)
                dst = os.path.join(section_dir, cfg.archive_name, os.path.basename(cat_path))
                if os.path.normpath(dst) != os.path.normpath(cat_path):
                    actions.append(Action(ARCHIVE_FOLDER, cat_path, dst,
                                          reason=f"{k}: дубль версии {cat_date} "
                                                 f"(актуальна {nd})",
                                          key=k, group=_section_folder_by_path(cat_path, cfg)))

    def _under_archive(path: str) -> bool:
        np = os.path.normpath(path)
        return any(np.startswith(a + os.sep) for a in archive_dirs)

    # 4) файлы: каталоги / переименование / перемещение / архив одиночек
    for fr, rec in matched:
        if _under_archive(fr.path):
            continue   # уедет вместе с каталогом-дублем
        is_chg = fr.path in changed_paths
        ext = os.path.splitext(fr.name)[1]
        entry = rec.entry
        vdate = _vdate(fr, rec)
        is_active = vdate == newest_date.get(entry.key)

        # файлы субподрядчиков (вне сервера) обрабатывает audit_external
        # (наводит порядок В ПАПКЕ субподрядчика); сюда не идём
        if not _on_server(fr.path, cfg):
            continue

        if fr.category != "ПД":
            if rec.canonical != fr.name:
                dst = os.path.join(os.path.dirname(fr.path), rec.canonical)
                actions.append(Action(RENAME, fr.path, dst,
                                      reason=f"{entry.key}: имя по эталону",
                                      key=entry.key, is_change=is_chg,
                                      group=_first_component(fr.rel)))
            continue

        grp = _section_folder(entry, cfg)
        is_sig = ext.lower() in {e.lower() for e in cfg.sig_extensions}
        dest_dir, mkdirs = _doc_destination(entry, vdate, ext, is_active, cfg)
        # подпись .sig кладём в !PUBLISHED, СОХРАНЯЯ имя (их много на том)
        target_name = fr.name if is_sig else rec.canonical
        dst = os.path.join(dest_dir, target_name)
        if os.path.normpath(dst) == os.path.normpath(fr.path):
            continue   # уже на месте и правильно назван

        # Перенос с субподряда на сервер — только через TransferDialog
        # (transfer.transfer); здесь обрабатываем только файлы на сервере.
        # (ранний continue выше уже отсёк несерверные пути)

        cur_dir = os.path.normpath(os.path.dirname(fr.path))
        same_dir = cur_dir == os.path.normpath(dest_dir)
        in_catalog = _catalog_of(fr.path, entry)[0] is not None

        if not is_active:
            kind = ARCHIVE
            reason = f"{entry.key}: версия {vdate} устарела (есть {newest_date[entry.key]})"
        elif not in_catalog:
            kind = MKCATALOG
            reason = (f"{entry.key}: подпись .sig → !PUBLISHED" if is_sig
                      else f"{entry.key}: версия {vdate} — собрать в каталог")
        elif same_dir:
            kind = RENAME
            reason = f"{entry.key}: имя не по эталону"
        else:
            kind = MOVE
            reason = (f"{entry.key}: подпись .sig → !PUBLISHED" if is_sig
                      else f"{entry.key}: переместить в нужную подпапку")

        actions.append(Action(kind, fr.path, dst, reason=reason, key=entry.key,
                              is_change=is_chg, mkdirs=mkdirs, group=grp,
                              node=os.path.dirname(dst)))

    # 4b) базовая структура по составу проекта: в каждом существующем разделе
    #     должны быть папки документов {обозначение} с папкой !ARCHIVE внутри.
    #     covered — корни областей, которые реально сканировались (есть файлы в
    #     инвентаре): там файловой раскладкой занимается код выше, не аудит.
    covered = set()
    inv_norm = [os.path.normpath(fr.path) for fr in inventory]
    for e in matcher.entries:
        r = os.path.normpath(_area_root(e, cfg))
        if r in covered:
            continue
        if any(p == r or p.startswith(r + os.sep) for p in inv_norm):
            covered.add(r)
    actions.extend(_base_structure_actions(cfg, matcher.entries, covered))
    # 4c) аудит субподрядных папок (структура/имена/«нет в составе») — до переноса
    actions.extend(audit_external(cfg, matcher.entries))

    # 5) защита от коллизий: если несколько файлов претендуют на одно имя —
    #    не выполняем, а помечаем «требует внимания»
    return _flag_dst_collisions(actions, cfg.sig_extensions)


def _flag_dst_collisions(actions: List[Action],
                         sig_extensions) -> List[Action]:
    """Если несколько файловых действий претендуют на один dst — заменить на FLAG."""
    file_moves = {TRANSFER, MKCATALOG, RENAME, MOVE, ARCHIVE}
    sig_exts = {e.lower() for e in (sig_extensions or [])}

    def _is_sig(a):
        return os.path.splitext(a.src)[1].lower() in sig_exts

    from collections import Counter
    dst_count = Counter(os.path.normpath(a.dst) for a in actions
                        if a.kind in file_moves and a.dst and not _is_sig(a))
    dups = {d for d, n in dst_count.items() if n > 1}
    if not dups:
        return actions
    fixed: List[Action] = []
    for a in actions:
        if a.kind in file_moves and a.dst and os.path.normpath(a.dst) in dups:
            fixed.append(Action(FLAG, a.src,
                                reason=f"{a.key}: на имя «{os.path.basename(a.dst)}» "
                                       f"претендует несколько файлов — проверьте вручную",
                                key=a.key, selected=False,
                                is_change=a.is_change, group=a.group))
        else:
            fixed.append(a)
    return fixed


def _section_folder_by_path(path: str, cfg: AppConfig) -> str:
    """Имя раздела по пути каталога (для группировки ARCHIVE_FOLDER)."""
    rel = os.path.relpath(path, cfg.target_pd_abs)
    return _first_component(rel)


# ===== ЕДИНЫЙ ДВИЖОК ПРОВЕРКИ (для сервера И субподряда) ==========================
def _audit_version_catalog(cfg: AppConfig, e: RegEntry, folder: str, proj: str,
                           group: str, do_files: bool) -> List[Action]:
    """Проверка ОДНОГО каталога версии {обозначение}_{дата}:
       • чужой шифр/год;  • обязательные ред./публ. папки;
       • (если do_files) имена и раскладка файлов по эталону.
    Одинаково для сервера и субподряда."""
    out: List[Action] = []
    base = os.path.basename(folder)
    pfx, _d = cat_match(base)
    csh = _shifr_of(pfx) if pfx else ""
    if _check(cfg, "foreign_shifr") and proj and csh and csh != proj:
        out.append(Action(FLAG, folder, selected=False, node=folder, group=group,
                          reason=f"другой шифр/год — проверить: {base} (в проекте {proj})",
                          check="foreign_shifr"))
    loose = _loose_catalog(e, cfg)
    edit_dir, pub_dir = _edit_of(e, cfg), _pub_of(e, cfg)
    if not loose and _check(cfg, "edit_pub"):
        for need in (edit_dir, pub_dir):
            if not os.path.isdir(os.path.join(folder, need)):
                out.append(Action(MKSTRUCT, folder, "",
                                  reason=f"нет {need} в каталоге версии {base}",
                                  key=e.key, mkdirs=[os.path.join(folder, need)],
                                  node=folder, group=group, check="edit_pub"))
    if do_files and _check(cfg, "loose_files"):
        allowed = {x.lower() for x in (e.extensions or cfg.published_extensions)}
        sig_exts = {x.lower() for x in cfg.sig_extensions}
        # проверяем корень каталога (разложить россыпь) и публ. папку (имена по
        # эталону). Внутрь !EDIT НЕ лезем — там рабочие исходники с произвольными
        # именами, их переименовывать не нужно.
        for sub in ([""] if loose else ["", pub_dir]):
            d = os.path.join(folder, sub) if sub else folder
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for fn in names:
                fp = os.path.join(d, fn)
                if not os.path.isfile(fp) or _ignored(fn, cfg):
                    continue
                ext = os.path.splitext(fn)[1]
                is_sig = ext.lower() in sig_exts
                to_pub = ext.lower() in allowed                # публикуемый тип
                tgt_dir = folder if loose else os.path.join(
                    folder, pub_dir if to_pub else edit_dir)
                # имя по эталону — ТОЛЬКО для публикуемого файла; ред. исходники
                # (dwg/docx и т.п.) и подписи оставляем с их именами
                canon = canonical_name(e, ext)
                tgt_name = (canon or fn) if (to_pub and not is_sig and not loose) else fn
                dst = os.path.join(tgt_dir, tgt_name)
                if os.path.normpath(dst) == os.path.normpath(fp):
                    continue
                same_dir = os.path.normpath(os.path.dirname(fp)) == os.path.normpath(tgt_dir)
                if same_dir and tgt_name == fn:
                    continue
                kind = RENAME if same_dir else MOVE
                reason = (f"{e.key}: имя по эталону: {fn}" if same_dir
                          else f"{e.key}: файл в {os.path.basename(tgt_dir)} (по эталону): {fn}")
                out.append(Action(kind, fp, dst, reason=reason, key=e.key,
                                  node=folder, group=group,
                                  mkdirs=([] if loose else
                                          [os.path.join(folder, edit_dir),
                                           os.path.join(folder, pub_dir)]),
                                  check=("edit_pub" if same_dir else "loose_files")))
    return out


def _audit_doc(cfg: AppConfig, e: RegEntry, doc: str, proj: str, group: str,
               do_files: bool) -> List[Action]:
    """Проверка ОДНОЙ папки документа: наличие !ARCHIVE, затем каждый каталог
    версии внутри. Одинаково для сервера и субподряда."""
    out: List[Action] = []
    arch = os.path.join(doc, cfg.archive_name)
    if _check(cfg, "doc_base") and not os.path.isdir(arch):
        out.append(Action(MKSTRUCT, doc, "",
                          reason=f"создать структуру документа {e.oboznachenie} "
                                 f"(папка + {cfg.archive_name})",
                          key=e.key, mkdirs=[arch], node=doc, group=group,
                          check="doc_base"))
    if os.path.isdir(doc):
        core = _nospace(strip_proj_prefix(e.oboznachenie))
        try:
            names = os.listdir(doc)
        except OSError:
            names = []
        for name in names:
            full = os.path.join(doc, name)
            if name == cfg.archive_name or not os.path.isdir(full):
                continue
            pfx, _d = cat_match(name)
            if not pfx or _nospace(strip_proj_prefix(pfx)) != core:
                continue
            out += _audit_version_catalog(cfg, e, full, proj, group, do_files)
    return out


def _published_pdf_issues(entry: RegEntry, folder: str, cfg: AppConfig) -> List[str]:
    """Доп. замечания по PDF перед переносом (комплектность публикуемой папки)."""
    issues: List[str] = []
    loose = _loose_catalog(entry, cfg)
    pub_dir = _pub_of(entry, cfg)
    pub_path = folder if loose else os.path.join(folder, pub_dir)
    if not os.path.isdir(pub_path):
        return issues
    core = _nospace(strip_proj_prefix(entry.oboznachenie))
    try:
        pdfs = [n for n in os.listdir(pub_path) if n.lower().endswith(".pdf")]
    except OSError:
        return ["нет доступа к папке"]
    if not pdfs:
        issues.append("нет pdf в публикуемой папке")
        return issues
    bad = [n for n in pdfs if not _nospace(
        strip_proj_prefix(os.path.splitext(n)[0])).startswith(core)]
    if bad:
        issues.append("не те pdf (чужое обозначение): " + ", ".join(bad[:3]))
    canon = canonical_name(entry, ".pdf")
    if canon and len(pdfs) == 1 and pdfs[0] != canon:
        issues.append(f"имя не по эталону (ожидается «{canon}»)")
    return issues


def _issues_from_audit_actions(actions: List[Action], entry: RegEntry,
                               cfg: AppConfig) -> List[str]:
    """Перевод Action единого движка в текстовые замечания (для transfer)."""
    issues: List[str] = []
    loose_names: List[str] = []
    edit_dir, pub_dir = _edit_of(entry, cfg), _pub_of(entry, cfg)
    seen_missing: set = set()
    for a in actions:
        if a.check == "foreign_shifr":
            base = os.path.basename(a.src)
            pfx, _d = cat_match(base)
            csh = _shifr_of(pfx) if pfx else ""
            m = re.search(r"\(в проекте ([^)]+)\)", a.reason or "")
            proj = m.group(1) if m else ""
            if csh and proj:
                issues.append(f"другой шифр/год в имени папки: {csh} ≠ {proj}")
            elif a.reason:
                issues.append(a.reason)
        elif a.kind == MKSTRUCT and a.check == "edit_pub":
            for d in a.mkdirs or []:
                name = os.path.basename(d)
                if name not in seen_missing:
                    seen_missing.add(name)
                    issues.append(f"нет папки {name}")
        elif a.kind == MOVE and a.check in ("loose_files", "edit_pub"):
            loose_names.append(os.path.basename(a.src))
    if loose_names:
        uniq = list(dict.fromkeys(loose_names))
        issues.append(f"файлы вне {pub_dir}/{edit_dir}: " + ", ".join(uniq[:3]))
    return issues


def validate_version_folder(entry: RegEntry, folder: str, cfg: AppConfig,
                            proj: str) -> List[str]:
    """Единая проверка каталога версии → список замечаний (сервер и субподряд).

    Использует тот же движок, что ``_audit_version_catalog`` / аудит 4300_ПД,
    плюс комплектность PDF перед переносом.
    """
    if not folder or not os.path.isdir(folder):
        return ["папка версии не найдена"]
    try:
        os.listdir(folder)
    except OSError:
        return ["нет доступа к папке"]
    actions = _audit_version_catalog(cfg, entry, folder, proj or "", "",
                                     do_files=True)
    issues = _issues_from_audit_actions(actions, entry, cfg)
    issues.extend(_published_pdf_issues(entry, folder, cfg))
    return issues


def _base_structure_actions(cfg: AppConfig, entries: List[RegEntry],
                            covered_roots: Optional[set] = None) -> List[Action]:
    """Аудит структуры по всем областям (ПД и ИИ). Документы каждой области
    проверяются в своём корне. covered_roots — корни, которые сканировались как
    источник (там разбором файлов занимается build_plan, дублировать не нужно)."""
    out: List[Action] = []
    covered_roots = covered_roots or set()
    proj = _project_shifr(entries)        # шифр объекта — из всех областей сразу
    # группируем по корню области, чтобы _foreign_folders запускался per-root
    by_root: Dict[str, List[RegEntry]] = {}
    for e in entries:
        by_root.setdefault(os.path.normpath(_area_root(e, cfg)), []).append(e)
    for root, ents in by_root.items():
        out += _structure_actions_in(cfg, root, ents, proj,
                                     scanned=os.path.normpath(root) in covered_roots)
    return out


def _structure_actions_in(cfg: AppConfig, root: str,
                          entries: List[RegEntry], proj: str = "",
                          scanned: bool = True) -> List[Action]:
    """Иерархический аудит структуры одной области (сверху вниз):
        1) раздел  2) папка документа  3) !ARCHIVE
        4) в существующих каталогах версий — обязательные ред./публ. папки
        5) файлы, лежащие прямо в каталоге версии (вне ред./публ.) — переместить.
    Файловую раскладку (п.5) делаем только для НЕсканируемых областей (ИИ),
    иначе этим занимается build_plan по инвентарю."""
    out: List[Action] = []
    if not root or not os.path.isdir(root):
        return out

    # тот же движок, что и для субподряда: проверка папки документа + каталогов.
    # На сервере ПД файлами занимается build_plan (scanned) → do_files=False;
    # для несканируемых областей (ИИ) — do_files=True.
    for e in entries:
        out += _audit_doc(cfg, e, _doc_folder(e, cfg), proj,
                          _section_folder(e, cfg), do_files=not scanned)
    # посторонние папки — то, чего нет в составе проекта (источник правды — JSON)
    out += _foreign_folders(cfg, root, entries, proj)
    return out


def _shifr_of(name: str) -> str:
    """Выделяет шифр объекта из начала имени и нормализует разделители
    (523_ПИР_24 → 523-ПИР-24). Форма шифра — из настроек (configure_shifr)."""
    m = registry.SHIFR_CORE.match(name or "")
    return re.sub(r"[_]", "-", m.group(0)) if m else ""


def _project_shifr(entries: List[RegEntry]) -> str:
    """Шифр объекта = самый частый среди обозначений (чтобы отдельный том
    с другим годом, напр. 523-ПИР-23-ИГДИ, не «перетягивал» весь проект)."""
    from collections import Counter
    cnt = Counter(s for s in (_shifr_of(e.oboznachenie) for e in entries) if s)
    return cnt.most_common(1)[0][0] if cnt else ""


def _foreign_folders(cfg: AppConfig, root: str,
                     entries: List[RegEntry], proj: str = None) -> List[Action]:
    """Папки на сервере, которых НЕТ в составе проекта, помечаются как посторонние.
    Версии с чужим шифром/годом помечаются «проверить»."""
    out: List[Action] = []
    if not root or not os.path.isdir(root):
        return out

    if proj is None:
        proj = _project_shifr(entries)
    np = os.path.normpath
    expected = set()        # ожидаемые папки (разделы, документы и их предки)
    doc_folders = set()     # папки документов (в них допустимы каталоги версий)
    archive_allowed = set() # папки, где допустима !ARCHIVE (документы и СМ-части)
    sec_cores: Dict[str, set] = {}  # папка раздела -> ядра обозначений её документов
    for e in entries:
        d = _doc_folder(e, cfg)
        doc_folders.add(np(d))
        archive_allowed.add(np(d))
        sec_dir = np(os.path.join(root, _section_folder(e, cfg)))
        sec_cores.setdefault(sec_dir, set()).add(_nospace(strip_proj_prefix(e.oboznachenie)))
        # !ARCHIVE допустима и на уровне РАЗДЕЛА (туда убирают целые устаревшие тома,
        # например когда том разбили на несколько новых)
        archive_allowed.add(np(os.path.join(root, _section_folder(e, cfg))))
        par = _smeta_parent(e.oboznachenie)
        if par:
            archive_allowed.add(np(os.path.join(root, _section_folder(e, cfg), par)))
        cur = d
        while len(np(cur)) > len(np(root)):
            expected.add(np(cur))
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

    # служебные подпапки структуры (молча пропускаем) + явно исключённые пользователем
    tolerated = tolerated_dirs(cfg, entries)
    # рабочие подпапки внутри томов (граф./текст. часть, подгрузки, ред. форматы) —
    # это ОТКЛОНЕНИЕ от стандарта. Список префиксов — из настроек проекта.
    work_prefixes = tuple(cfg.work_prefixes or [])

    def _is_workname(dn):
        return any(dn.startswith(p) for p in work_prefixes)

    # ядро обозначения → документ (чтобы по каталогу версии найти его типы файлов)
    core_map = {_nospace(strip_proj_prefix(e.oboznachenie)): e for e in entries}

    for dirpath, dirnames, _files in os.walk(root):
        keep = []
        for dn in dirnames:
            full = np(os.path.join(dirpath, dn))
            rel = os.path.relpath(full, root)
            if dn == cfg.archive_name:
                # !ARCHIVE допустима ТОЛЬКО в папке документа (не в каталоге версии)
                if np(dirpath) in archive_allowed:
                    continue
                if _check(cfg, "archive_place"):
                    out.append(Action(FLAG, full, selected=False, node=full,
                                      group=_first_component(rel), check="archive_place",
                                      reason=f"папка {cfg.archive_name} не на месте "
                                             f"(должна быть в папке документа): {rel}"))
                continue
            if dn in tolerated or _ignored(dn, cfg):
                continue                      # служебные/исключённые/по маске (*_DRAFT*) — молча
            if full in expected:
                keep.append(dn)               # ожидаемая — заходим внутрь
                continue
            cpfx = cat_match(dn)[0]
            if np(dirpath) in doc_folders and cpfx:
                keep.append(dn)               # каталог версии — допустим
                csh = _shifr_of(cpfx)
                if proj and csh and csh != proj and _check(cfg, "foreign_shifr"):
                    out.append(Action(FLAG, full, selected=False,
                                      node=os.path.dirname(full),
                                      group=_first_component(rel), check="foreign_shifr",
                                      reason=f"другой шифр/год — проверить: {dn} "
                                             f"(в проекте {proj})"))
                continue
            # каталог версии лежит прямо в папке РАЗДЕЛА (минуя папку документа) —
            # его ядро совпадает с документом этого раздела: ПЕРЕНЕСТИ внутрь.
            # Это выполнимое действие (перемещение папки), поэтому с галочкой.
            if cpfx and _nospace(strip_proj_prefix(cpfx)) in sec_cores.get(np(dirpath), set()):
                if _check(cfg, "version_in_doc"):
                    doc_dir = os.path.join(dirpath, cpfx)        # папка документа
                    dst = os.path.join(doc_dir, dn)
                    out.append(Action(MOVE, full, dst,
                                      reason=f"каталог версии вне папки документа — "
                                             f"перенести внутрь {cpfx}: {dn}",
                                      mkdirs=[os.path.join(doc_dir, cfg.archive_name)],
                                      node=os.path.dirname(full),
                                      group=_first_component(rel), check="version_in_doc"))
                continue
            if _is_workname(dn):
                if not _check(cfg, "work_folders"):
                    continue                  # проверка выключена — не углубляемся, молчим
                # рабочая/нестандартная папка тома (ГЧ, ТЧ, Подгрузки, Ред. формат…).
                # ГЛАВНОЕ действие — разложить её файлы по !EDIT/!PUBLISHED каталога
                # версии (если папка лежит внутри каталога версии). Исключение —
                # вторичный вариант (правый клик). Внутрь не углубляемся.
                cat_pfx = cat_match(os.path.basename(dirpath))[0]
                ent = core_map.get(_nospace(strip_proj_prefix(cat_pfx))) if cat_pfx else None
                if ent is not None:
                    exts = ent.extensions or cfg.published_extensions
                    edit_dir, pub_dir = _edit_of(ent, cfg), _pub_of(ent, cfg)
                    out.append(Action(NORMALIZE, full, dirpath, selected=False,
                                  node=full, group=_first_component(rel), check="work_folders",
                                  reason=f"нестандартная папка {dn}: разложить файлы по "
                                         f"{edit_dir}/{pub_dir} "
                                         f"(или правый клик → внести в исключения)",
                                  payload={"published_extensions": list(exts),
                                           "edit_dir": edit_dir,
                                           "published_dir": pub_dir}))
                else:
                    out.append(Action(FLAG, full, selected=False, node=full,
                                  group=_first_component(rel), check="work_folders",
                                  reason=f"нестандартная (рабочая) папка: {dn} — "
                                         f"внесите в исключения, если она нужна "
                                         f"(правый клик → «Добавить папку в исключения»)"))
                continue
            # всё прочее — посторонняя папка. Замечание вешаем на РОДИТЕЛЬСКИЙ узел,
            # а саму папку как узел структуры не показываем.
            if _check(cfg, "foreign_folders"):
                out.append(Action(FLAG, full,
                                  reason=f"посторонняя папка (нет в составе): {os.path.basename(full)}",
                                  selected=False, group=_first_component(rel),
                                  node=os.path.dirname(full), check="foreign_folders"))
        dirnames[:] = keep
    return out


# ---------- аудит субподрядных папок (наводим порядок ДО переноса) ----------
def _external_roots(cfg: AppConfig) -> List[str]:
    """Корни источников-субподрядчиков (включённые папки вне сервера)."""
    np = os.path.normpath
    server = [np(p) for p in (cfg.target_pd_abs, cfg.ii_abs, cfg.target_ii_abs) if p]
    out = []
    for s in cfg.sources:
        if not getattr(s, "enabled", True):
            continue
        p = np(cfg.abspath(s.path))
        if any(p == r or p.startswith(r + os.sep) for r in server):
            continue
        out.append(p)
    return out


def audit_external(cfg: AppConfig, entries: List[RegEntry]) -> List[Action]:
    """Проверка субподрядных папок: структура (!EDIT/!PUBLISHED), имена папок и
    файлов, и «отсутствует в составе проекта» для незнакомых томов."""
    out: List[Action] = []
    cores = {_nospace(strip_proj_prefix(e.oboznachenie)): e for e in entries}
    proj = _project_shifr(entries)
    service = tolerated_dirs(cfg, entries)
    np = os.path.normpath
    for root in _external_roots(cfg):
        if not os.path.isdir(root):
            continue
        recognized: set = set()           # папки документов и каталоги версий (по составу)
        unknown: List[tuple] = []         # (нормпуть, grp) — незнакомые папки, куда зашли
        for dp, dns, _fns in os.walk(root):
            keep = []
            for dn in dns:
                full = os.path.join(dp, dn)
                if dn == cfg.archive_name or dn in service or _ignored(dn, cfg):
                    continue
                pfx, d6 = cat_match(dn)
                grp = _first_component(os.path.relpath(full, root))
                # папка с датой — это каталог версии (тот же движок, что на сервере)
                if d6:
                    e = cores.get(_nospace(strip_proj_prefix(pfx)))
                    if e is not None:
                        recognized.add(np(full))
                        out += _audit_version_catalog(cfg, e, full, proj, grp,
                                                      do_files=True)
                    elif _check(cfg, "foreign_folders"):
                        out.append(Action(
                            FLAG, full, selected=False, node=full, group=grp,
                            reason=f"{dn}: каталог версии, которого нет в составе "
                                   f"проекта (неверное имя или лишняя папка)",
                            check="foreign_folders"))
                    continue                  # каталог версии — внутрь не углубляемся
                # папка без даты — документ из состава? (может лежать на любом уровне:
                # прямо в корне субподряда или внутри 4300_ПД/01_ПД, 1100_ИИ/01_ИГДИ…)
                e = cores.get(_nospace(strip_proj_prefix(dn)))
                if e is not None:
                    recognized.add(np(full))
                    try:
                        inner = os.listdir(full)
                    except OSError:
                        inner = []
                    has_cat = any(cat_match(n)[0] and os.path.isdir(os.path.join(full, n))
                                  for n in inner)
                    # тот же движок: !ARCHIVE + каждый каталог версии внутри
                    out += _audit_doc(cfg, e, full, proj, grp, do_files=True)
                    if not has_cat and _check(cfg, "doc_base"):
                        out.append(Action(
                            FLAG, full, selected=False, node=full, group=grp,
                            reason=f"{dn}: нет каталога версии {e.oboznachenie}_ггммдд — "
                                   f"структура нарушена: сформируйте каталог версии с "
                                   f"папками {_edit_of(e, cfg)}/{_pub_of(e, cfg)}",
                            check="doc_base"))
                    continue                  # документ обработан — внутрь не углубляемся
                unknown.append((np(full), grp))
                keep.append(dn)               # незнакомая папка — идём глубже
            dns[:] = keep
        # посторонние/лишние папки: незнакомая папка, под которой НЕТ ни одного
        # документа/каталога из состава — тупиковая ветка. Помечаем самую верхнюю
        # такую папку (вложенный мусор повторно не дублируем).
        if _check(cfg, "foreign_folders"):
            def _has_doc(p):
                return any(r == p or r.startswith(p + os.sep) for r in recognized)
            root_n = np(root)
            for full, grp in unknown:
                if _has_doc(full):
                    continue                  # это предок документа — папка нужна
                parent = os.path.dirname(full)
                if parent != root_n and not _has_doc(parent):
                    continue                  # родитель уже помечен как лишний — не дублируем
                out.append(Action(
                    FLAG, full, selected=False, node=full, group=grp,
                    reason=f"лишняя папка (нет документов из состава проекта): "
                           f"{os.path.basename(full)} — проверьте имя/содержимое",
                    check="foreign_folders"))
    return out


def build_latest_plan(inventory: List[FileRec],
                      matcher: Matcher,
                      cfg: AppConfig,
                      only_pd: bool = True) -> List[Action]:
    """Комплект !LATEST: по каждому документу — самая свежая актуальная версия."""
    actions: List[Action] = []
    latest_root = cfg.latest_abs
    pd_sub = "02_ПД"

    # выбираем самую свежую версию каждого документа
    exts = {e.lower() for e in (cfg.latest_extensions or [])}
    best: Dict[str, tuple[FileRec, Recognized]] = {}
    for fr in inventory:
        if only_pd and fr.category != "ПД":
            continue
        if exts and os.path.splitext(fr.name)[1].lower() not in exts:
            continue
        rec = recognize(fr.name, fr.path, matcher, cfg.date_from_mtime_if_absent)
        if not rec.matched or not rec.canonical or not rec.date6:
            continue
        k = rec.entry.key
        cur = best.get(k)
        if cur is None or (rec.date6 or "") > (cur[1].date6 or ""):
            best[k] = (fr, rec)

    for key, (fr, rec) in sorted(best.items()):
        sect = _section_folder(rec.entry, cfg)
        dst_dir = os.path.join(latest_root, pd_sub, sect)
        dst = os.path.join(dst_dir, rec.canonical)
        if os.path.exists(dst):
            continue   # уже актуальный лежит
        actions.append(Action(COPY_LATEST, fr.path, dst,
                              reason=f"{key}: актуальная версия {rec.date6} в комплект",
                              key=key, group=sect))
    return actions
