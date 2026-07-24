"""Настройки приложения.

Конфигурация хранится в обычном JSON-файле рядом с программой
(config.json). Это позволяет помощникам менять пути и правила
без участия программиста.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List


DEFAULT_IGNORE = ["Thumbs.db", "desktop.ini", ".DS_Store", "~$*", "*.tmp", "*.lnk",
                  "*.url", "*_DRAFT*", "*_draft*", "*_Draft*", ".docflow*"]

# Типы проверок структуры. Каждую можно включить/выключить в настройках —
# это и есть «настраиваемые типы проверок» (без правки кода).
CHECKS = {
    "doc_base":        "Папка документа + !ARCHIVE",
    "edit_pub":        "Папки ред./публ. в каждом каталоге версии",
    "loose_files":     "Файлы вне ред./публ. папок (изыскания)",
    "version_in_doc":  "Каталог версии должен быть внутри папки документа",
    "archive_place":   "!ARCHIVE на своём месте",
    "foreign_shifr":   "Чужой шифр/год — проверить",
    "foreign_folders": "Посторонние папки (нет в составе)",
    "work_folders":    "Нестандартные (рабочие) папки",
}
DEFAULT_CHECKS = {k: True for k in CHECKS}

# Проверки сгруппированы по этапам (как в сервисе проверки смет) — для удобного
# включения/выключения целыми блоками.
CHECK_GROUPS = [
    ("Этап 1. Структура папок",
     ["doc_base", "edit_pub", "archive_place", "version_in_doc"]),
    ("Этап 2. Шифр и посторонние папки",
     ["foreign_shifr", "foreign_folders", "work_folders"]),
    ("Этап 3. Файлы и комплектность",
     ["loose_files"]),
]
# Значимость по умолчанию: error — «структура нарушена» (красный),
# warn — «требует внимания» (жёлтый).
DEFAULT_CHECK_LEVELS = {
    "doc_base": "error", "edit_pub": "error", "archive_place": "warn",
    "version_in_doc": "error", "foreign_shifr": "warn", "foreign_folders": "warn",
    "work_folders": "warn", "loose_files": "error",
}
# Служебные подпапки, которые НЕ являются версиями документации
SERVICE_DIRS = ["!ARCHIVE", "!EDIT", "!PUBLISHED", "!WORK", "!SUPPORT", "!LINKS",
                "!INITIAL", "!LATEST", "DOC", "DWG", "PDF"]
# Рабочие подпапки, в которые при сканировании НЕ заходим по умолчанию.
# !EDIT и !PUBLISHED сканируем — это части структуры тома (нужно для
# распознавания уже разложенных файлов и архивации старых версий).
SCAN_SKIP_DIRS = ["!ARCHIVE", "!WORK", "!SUPPORT", "!LINKS",
                  "!INITIAL", "!LATEST", "DOC", "DWG"]


@dataclass
class Source:
    """Папка-источник, которую сканируем."""
    name: str               # понятное имя ("Сервер ПД", "Внешний диск — субподряд")
    path: str               # путь к папке
    category: str = "ПД"    # "ПД" или "ИИ" — куда относится документация
    enabled: bool = True


@dataclass
class AppConfig:
    projects: List[str] = field(default_factory=list)  # список корней проектов (объектов)
    project_root: str = ""                       # активный корень проекта на сервере
    object_name: str = ""                        # наименование объекта (для титульных листов)
    registry_path: str = ""                      # путь к xlsx «Наименование файлов»
    registry_sheet: str = "ПИР"                  # лист с реестром
    target_pd: str = "4300_ПД"                   # папка ПД (относительно project_root)
    target_ii: str = "1100_ИИ"                   # папка ИИ
    latest_dir: str = "8300_ВыпускПД/!LATEST"    # папка актуального комплекта
    # вложенность папок внутри !LATEST (задаёт пользователь). Плейсхолдеры:
    #   {area}    — 02_ПД или 01_ИИ,  {section} — 01_ПЗ/05_ИОС…,
    #   {razdel}  — номер раздела,     {short}   — шифр раздела (ПЗ, ИОС1),
    #   {oboznachenie} — полное обозначение.
    # Внутри этой папки всегда лежит каталог версии {обозначение}_{дата}.
    # Примеры: "{area}/{section}"  ·  "{section}"  ·  "{area}"  ·  ""(плоско).
    latest_layout: str = "{area}/{section}"
    archive_name: str = "!ARCHIVE"               # имя подпапки архива версий
    sources: List[Source] = field(default_factory=list)
    ignore_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    scan_skip_dirs: List[str] = field(default_factory=lambda: list(SCAN_SKIP_DIRS))
    exclude_folders: List[str] = field(default_factory=list)  # папки, исключённые из проверки
    latest_extensions: List[str] = field(default_factory=lambda: [".pdf"])  # что класть в !LATEST
    # --- раскладка томов по структуре {обозначение}_{дата} ---
    edit_dir: str = "!EDIT"                       # подпапка для редактируемых форматов
    published_dir: str = "!PUBLISHED"             # подпапка для pdf/sig
    published_extensions: List[str] = field(default_factory=lambda: [".pdf", ".sig"])
    # допускаемые типы для смет/ВОР (в каталоге версии)
    smeta_extensions: List[str] = field(default_factory=lambda: [".pdf", ".xlsx", ".gge", ".gsfx", ".sig"])
    sig_extensions: List[str] = field(default_factory=lambda: [".sig"])  # подписи, не переименовываем
    smeta_short_codes: List[str] = field(default_factory=lambda: ["СМ"])  # без !EDIT/!PUBLISHED
    smeta_sub_codes: List[str] = field(default_factory=lambda: ["ВОР", "СВОР", "ЛСР", "ОСР"])  # внутри СМ
    smeta_razdel: str = "12"                      # раздел сметы
    # имя раздела по его номеру (когда документы внутри имеют разный шифр).
    # Для остальных разделов имя берётся из шифра документа.
    razdel_codes: dict = field(default_factory=lambda: {"12": "СМ", "13": "ИД"})
    # форма шифра объекта (регулярное выражение). По умолчанию «число-буквы-число»
    # (523-ПИР-24, 519-ПИР-23). Для другого формата — поменять здесь, код не трогать.
    # Напр.: 728-ПСД-25 подходит под этот же шаблон; 728-ПСД → r"\d+[-_][А-Яа-яA-Za-z]+"
    shifr_pattern: str = r"\d+[-_][A-Za-zА-Яа-яЁё]+[-_]\d+"
    # «рабочие»/нестандартные подпапки тома (граф./текст. часть, подгрузки, ред.):
    # помечаются как отклонение и предлагаются к раскладке/исключению.
    work_prefixes: list = field(default_factory=lambda: ["ГЧ", "ТЧ", "Подгрузки", "Ред"])
    # какие проверки включены (см. CHECKS). Выключенная проверка не формирует
    # замечаний — узел остаётся зелёным по этому правилу.
    checks: dict = field(default_factory=lambda: dict(DEFAULT_CHECKS))
    # значимость каждой проверки: "error" (красный) или "warn" (жёлтый)
    check_levels: dict = field(default_factory=lambda: dict(DEFAULT_CHECK_LEVELS))
    # инженерные изыскания (ИИ) — параллельная область
    ii_root: str = "1100_ИИ/04_Отчеты"          # корень отчётов ИИ (отн. project_root)
    ii_latest_sub: str = "01_ИИ"                # подпапка ИИ в !LATEST
    ii_types: list = field(default_factory=lambda: [
        {"code": "01", "short": "ИГДИ"}, {"code": "02", "short": "ИГИ"},
        {"code": "03", "short": "ИГМИ"}, {"code": "04", "short": "ИЭИ"},
        {"code": "05", "short": "ИТО"}, {"code": "06", "short": "АРХ"},
        {"code": "07", "short": "ВОП"}])
    use_hash: bool = False                       # сверять содержимое по хэшу (медленнее)
    date_from_mtime_if_absent: bool = True       # брать дату из файла, если её нет в имени

    # ---- сервис ----
    def abspath(self, rel: str) -> str:
        if not rel:
            return ""
        if os.path.isabs(rel):
            return os.path.normpath(rel)          # убираем двойные // и т.п.
        return os.path.normpath(os.path.join(self.project_root, rel))

    @property
    def target_pd_abs(self) -> str:
        return self.abspath(self.target_pd)

    @property
    def target_ii_abs(self) -> str:
        return self.abspath(self.target_ii)

    @property
    def ii_abs(self) -> str:
        """Абсолютный путь к корню отчётов ИИ (1100_ИИ/04_Отчеты)."""
        return self.abspath(self.ii_root)

    @property
    def latest_abs(self) -> str:
        return self.abspath(self.latest_dir)


def app_data_dir() -> str:
    """Личная папка программы (одна на пользователя): %APPDATA%\\DocFlow.
    Не зависит от того, где лежит .exe — поэтому ничего «не расползается»."""
    base = (os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
            or os.path.expanduser("~"))
    d = os.path.join(base, "DocFlow")
    os.makedirs(d, exist_ok=True)
    return d


def default_config_path() -> str:
    """config.json — в личной папке %APPDATA%\\DocFlow (а не рядом с .exe)."""
    new = os.path.join(app_data_dir(), "config.json")
    exe_dir = os.path.dirname(os.path.abspath(__import__("sys").argv[0])) or os.getcwd()
    old = os.path.join(exe_dir, "config.json")
    if os.path.exists(old) and not os.path.exists(new):   # перенос со старого места
        try:
            os.replace(old, new)
        except OSError:
            pass
    return new


def hide_file(path: str) -> None:
    """Сделать файл скрытым (Windows). На других ОС — без эффекта."""
    try:
        import ctypes
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:  # noqa: BLE001
        pass


def unhide_file(path: str) -> None:
    """Снять атрибут «скрытый» — иначе Windows не даёт перезаписать файл в режиме w."""
    try:
        import ctypes
        if os.path.exists(path):
            FILE_ATTRIBUTE_NORMAL = 0x80
            ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_NORMAL)
    except Exception:  # noqa: BLE001
        pass


def load(path: str | None = None) -> AppConfig:
    path = path or default_config_path()
    if not os.path.exists(path):
        return AppConfig()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    srcs = [Source(**s) for s in data.pop("sources", [])]
    cfg = AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__annotations__})
    cfg.sources = srcs
    if cfg.project_root:
        cfg.project_root = os.path.normpath(cfg.project_root)   # без двойных //
    cfg.projects = [os.path.normpath(p) for p in cfg.projects if p]
    return cfg


def save(cfg: AppConfig, path: str | None = None) -> None:
    path = path or default_config_path()
    data = asdict(cfg)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---- настройки конкретного проекта (хранятся в корне проекта) ----
# Чтобы при переключении объектов не лазить в настройки — у каждого проекта
# свои папки/реестр, записанные в .docflow_settings.json в его корне.
PROJECT_FIELDS = [
    "object_name",
    "registry_path", "registry_sheet", "target_pd", "target_ii", "latest_dir",
    "latest_layout",
    "archive_name", "ignore_patterns", "scan_skip_dirs", "exclude_folders",
    "latest_extensions", "edit_dir", "published_dir", "published_extensions",
    "smeta_extensions", "sig_extensions", "smeta_short_codes", "smeta_sub_codes",
    "smeta_razdel", "ii_root", "ii_latest_sub", "ii_types",
    "shifr_pattern", "work_prefixes", "checks", "check_levels",
    "razdel_codes", "use_hash", "date_from_mtime_if_absent",
]


def settings_dict(cfg: AppConfig) -> dict:
    """Настройки проекта (для встраивания в project_structure.json)."""
    d = {f: getattr(cfg, f) for f in PROJECT_FIELDS}
    d["sources"] = [asdict(s) for s in cfg.sources]
    return d


def apply_settings(cfg: AppConfig, data: dict) -> None:
    """Применить блок настроек к cfg (project_root не трогаем)."""
    for f in PROJECT_FIELDS:
        if f in data:
            setattr(cfg, f, data[f])
    if "sources" in data:
        cfg.sources = [Source(**s) for s in data["sources"]]


def project_settings_path(project_root: str) -> str:
    return os.path.normpath(os.path.join(project_root, ".docflow_settings.json"))


def save_project_settings(cfg: AppConfig) -> None:
    if not cfg.project_root:
        return
    data = {f: getattr(cfg, f) for f in PROJECT_FIELDS}
    data["sources"] = [asdict(s) for s in cfg.sources]
    p = project_settings_path(cfg.project_root)
    unhide_file(p)                       # снять «скрытый», иначе перезапись упадёт
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    hide_file(p)


def load_project_settings(cfg: AppConfig) -> bool:
    """Загружает настройки активного проекта в cfg. True — если файл найден."""
    if not cfg.project_root:
        return False
    p = project_settings_path(cfg.project_root)
    if not os.path.exists(p):
        return False
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    for fld in PROJECT_FIELDS:
        if fld in data:
            setattr(cfg, fld, data[fld])
    if "sources" in data:
        cfg.sources = [Source(**s) for s in data["sources"]]
    return True
