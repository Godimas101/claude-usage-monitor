"""The app's languages: which one to show, how to format for it, and the catalogs of text (#87, epic #49).

Chris's rule (2026-11-21): English by default, plus French, Spanish and Turkish. The app follows the Windows display
language when it is one the app offers, and falls back to English. A language is offered only once its native check is
signed off (#107): until then ``OFFERED`` holds English alone, so Windows' language can never pick a half-done catalog.

No UI imports. Windows is read through a small reader object, so everything here is pure under test: the resolver,
the format tag and the clock take what Windows said as arguments, and ``LocaleSource`` takes the reader it uses.

Catalogs are JSON files in ``webui/i18n`` (``<lang>.json``), shared with the dashboard, which gets them through the
local server (``/static/i18n/<lang>.js``, built from the JSON). ``en.json`` is the source of truth: a key missing from
another catalog shows its English text, and the lookup says so, so the caller can mark it as English (``lang="en"``)
and case it by English rules (a Turkish page would otherwise turn ``SESSION`` into ``SESSİON``).
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from usage_monitor.log import get_logger

log = get_logger(__name__)

DEFAULT = "en"
LANGUAGES: tuple[str, ...] = ("en", "fr", "es", "tr")       # every catalog the app has
# The languages Windows' language or the setting may pick. Changed only by a language's sign-off change (#107), which
# also adds it to the installer's [Languages] (#103).
OFFERED: tuple[str, ...] = ("en",)
# Only Windows' first display language counts, the one the installer reads too (#86's default). True takes the first
# offered language anywhere in Windows' list instead (#86's other choice).
WALK_DISPLAY_LANGUAGES = False
SETTING_VALUES: tuple[str, ...] = ("system", *LANGUAGES)
# Each language named in itself, the way the Language setting lists them (never translated).
NAMES: Mapping[str, str] = {"en": "English", "fr": "Français", "es": "Español", "tr": "Türkçe"}
# The format each language uses when Windows' regional format is of another language (#86's default).
BASE_FORMATS: Mapping[str, str] = {"en": "en-US", "fr": "fr-FR", "es": "es-ES", "tr": "tr-TR"}
CATALOG_DIR = Path(__file__).resolve().parent / "webui" / "i18n"

DEV_INSTANCE_ENV = "USAGE_MONITOR_DEV_INSTANCE"
DEV_LANG_ENV = "USAGE_MONITOR_LANG"   # a dev instance only: shows a catalog language that is not offered yet

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TAG = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")


# ── Pure rules ────────────────────────────────────────────────────────────────

def primary(tag: Any) -> str:
    """The primary subtag of a BCP 47 tag, lower case: ``fr-CA`` → ``fr``. Anything that is not a tag gives ""."""
    if not isinstance(tag, str):
        return ""
    tag = tag.strip().replace("_", "-")
    if not _TAG.match(tag):
        return ""
    return tag.split("-", 1)[0].lower()


def resolve_system(preferred: Sequence[str], offered: Sequence[str] | None = None, *, walk: bool | None = None) -> str:
    """The language "System default" stands for: the first Windows display language when it is offered, else English.

    ``preferred`` is Windows' ordered display-language list. Only its first entry counts, the one the installer reads
    too, so the app and Setup agree. ``walk`` (the other choice on #86) takes the first offered language anywhere in
    the list instead. ``offered`` and ``walk`` default to ``OFFERED`` and ``WALK_DISPLAY_LANGUAGES``, read at call time,
    so a test (or the sign-off change) that sets them is seen everywhere."""
    offered = OFFERED if offered is None else offered
    walk = WALK_DISPLAY_LANGUAGES if walk is None else walk
    for tag in preferred if walk else list(preferred)[:1]:
        language = primary(tag)
        if language in offered:
            return language
    return DEFAULT


def resolve(setting: Any, preferred: Sequence[str], offered: Sequence[str] | None = None, *,
            dev_override: str | None = None, walk: bool | None = None) -> str:
    """The app's language. A dev instance's override wins (any catalog language, offered or not); then an explicit
    setting, if offered; anything else (``system``, an unknown value, a language not offered) follows Windows."""
    if dev_override in LANGUAGES:
        return str(dev_override)
    offered = OFFERED if offered is None else offered
    if isinstance(setting, str) and setting in offered:
        return setting
    return resolve_system(preferred, offered, walk=walk)


def format_tag(language: str, regional: str | None) -> str:
    """The format tag (dates, numbers, money) for ``language``. English keeps today's en-US (#86's default). French,
    Spanish and Turkish use Windows' regional format when it is of the same language (fr-CA, es-MX), else their base."""
    base = BASE_FORMATS.get(language, BASE_FORMATS[DEFAULT])
    if language == DEFAULT:
        return base
    if regional and primary(regional) == language:
        return regional.strip().replace("_", "-")
    return base


def clock_hours(language: str, short_time: str | None) -> int:
    """12 or 24. English keeps today's 12-hour clock (#86's default); French, Spanish and Turkish follow Windows' own
    short-time pattern (``H`` is a 24-hour hour, ``h`` a 12-hour one), so the strip matches the taskbar clock beside
    it, and fall back to their own 24-hour clock."""
    if language == DEFAULT:
        return 12
    if short_time:
        pattern = re.sub(r"'[^']*'", "", short_time)            # quoted literal text is not a field
        if "H" in pattern:
            return 24
        if "h" in pattern:
            return 12
    return 24


def plural_category(language: str, count: float) -> str:
    """The CLDR plural category of ``count`` in ``language`` (CLDR 49 rules for the four languages): en and tr have
    one and other; fr and es have one, many and other. French counts 0 and 1, and 1.5, as one. A count that is not a
    finite number is "other", as Intl.PluralRules has it."""
    if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count):
        return "other"
    n = abs(count)
    whole = int(n)
    integer = n == whole
    if language == "fr":
        if whole in (0, 1):
            return "one"
        return "many" if integer and whole != 0 and whole % 1_000_000 == 0 else "other"
    if language == "es":
        if n == 1:
            return "one"
        return "many" if integer and whole != 0 and whole % 1_000_000 == 0 else "other"
    # en and tr: "one" only for exactly 1 with no fraction shown (callers pass whole counts).
    return "one" if n == 1 else "other"


def fill(text: str, values: Mapping[str, Any] | None = None) -> str:
    """Put named values into ``{name}`` placeholders. An unknown placeholder is left as written."""
    if not values:
        return text
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


# ── Windows ───────────────────────────────────────────────────────────────────

class WindowsLanguage(Protocol):
    """What the app reads from Windows. Each call may fail; a failure reads as "nothing known"."""

    def display_languages(self) -> list[str]: ...
    def regional_format(self) -> str | None: ...
    def short_time(self) -> str | None: ...


class NoWindows:
    """A reader that knows nothing: English, the base formats. For tests, tools and other platforms."""

    def display_languages(self) -> list[str]:
        return []

    def regional_format(self) -> str | None:
        return None

    def short_time(self) -> str | None:
        return None


class Win32Language:
    """The real reader, through kernel32. Never raises: a call that fails, by an error code or an exception, reads as
    nothing (the app shows English), and the first failure of each call is logged as a warning.

    It reads the display languages, never ``locale.getlocale()`` or ``getdefaultlocale()``: those report the regional
    format, which "is not a language itself" (Microsoft's NLS terminology)."""

    MUI_LANGUAGE_NAME = 0x8
    LOCALE_SSHORTTIME = 0x79
    LOCALE_NAME_MAX_LENGTH = 85

    def __init__(self) -> None:
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._preferred = kernel32.GetUserPreferredUILanguages
        self._preferred.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.ULONG), wintypes.LPWSTR,
                                    ctypes.POINTER(wintypes.ULONG)]
        self._preferred.restype = wintypes.BOOL
        self._default_ui = kernel32.GetUserDefaultUILanguage
        self._default_ui.argtypes = []
        self._default_ui.restype = wintypes.WORD
        self._lcid_name = kernel32.LCIDToLocaleName
        self._lcid_name.argtypes = [wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD]
        self._lcid_name.restype = ctypes.c_int
        self._locale_name = kernel32.GetUserDefaultLocaleName
        self._locale_name.argtypes = [wintypes.LPWSTR, ctypes.c_int]
        self._locale_name.restype = ctypes.c_int
        self._locale_info = kernel32.GetLocaleInfoEx
        self._locale_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPWSTR, ctypes.c_int]
        self._locale_info.restype = ctypes.c_int
        self._failed: set[str] = set()

    def _failure(self, call: str, reason: str) -> None:
        if call not in self._failed:
            self._failed.add(call)
            log.warning("Windows %s unreadable (%s); the default stands in", call, reason)

    @staticmethod
    def _error() -> str:
        return f"error {ctypes.get_last_error()}"

    def display_languages(self) -> list[str]:
        from ctypes import wintypes
        try:
            count, size = wintypes.ULONG(0), wintypes.ULONG(0)
            if self._preferred(self.MUI_LANGUAGE_NAME, ctypes.byref(count), None, ctypes.byref(size)) and size.value:
                buffer = ctypes.create_unicode_buffer(size.value)
                if self._preferred(self.MUI_LANGUAGE_NAME, ctypes.byref(count), buffer, ctypes.byref(size)):
                    names = [name for name in buffer[:size.value].split("\x00") if name]
                    if names:
                        return names
            names = self._default_ui_language()
            if not names:
                self._failure("display languages", self._error())
            return names
        except (OSError, ValueError) as exc:
            self._failure("display languages", type(exc).__name__)
            return []

    def _default_ui_language(self) -> list[str]:
        lcid = self._default_ui()
        buffer = ctypes.create_unicode_buffer(self.LOCALE_NAME_MAX_LENGTH)
        if lcid and self._lcid_name(lcid, buffer, len(buffer), 0) > 0 and buffer.value:
            return [buffer.value]
        return []

    def regional_format(self) -> str | None:
        try:
            buffer = ctypes.create_unicode_buffer(self.LOCALE_NAME_MAX_LENGTH)
            if self._locale_name(buffer, len(buffer)) <= 0:
                self._failure("regional format", self._error())
                return None
            return buffer.value or None
        except (OSError, ValueError) as exc:
            self._failure("regional format", type(exc).__name__)
            return None

    def short_time(self) -> str | None:
        try:
            buffer = ctypes.create_unicode_buffer(80)
            # None is LOCALE_NAME_USER_DEFAULT: the user's own setting, with any change they made to the format.
            if self._locale_info(None, self.LOCALE_SSHORTTIME, buffer, len(buffer)) <= 0:
                self._failure("short time", self._error())
                return None
            return buffer.value or None
        except (OSError, ValueError) as exc:
            self._failure("short time", type(exc).__name__)
            return None


def windows_reader() -> WindowsLanguage:
    """The reader for this platform: kernel32 on Windows, ``NoWindows`` elsewhere or when kernel32 cannot be bound."""
    if sys.platform != "win32":
        return NoWindows()
    try:
        return Win32Language()
    except (OSError, AttributeError) as exc:
        log.warning("Windows language calls unavailable (%s); using English", type(exc).__name__)
        return NoWindows()


class LazyWindows:
    """The app's reader: ``windows_reader()``, made on first use rather than when the app is built, so the log is set
    up by then and a reader that cannot bind kernel32 says so in it."""

    def __init__(self) -> None:
        self._reader: WindowsLanguage | None = None
        self._lock = threading.Lock()

    def _get(self) -> WindowsLanguage:
        with self._lock:
            if self._reader is None:
                self._reader = windows_reader()
            return self._reader

    def display_languages(self) -> list[str]:
        return self._get().display_languages()

    def regional_format(self) -> str | None:
        return self._get().regional_format()

    def short_time(self) -> str | None:
        return self._get().short_time()


# ── The locale ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Locale:
    """What the app shows text and formats in. ``system`` is what "System default" resolves to, so Settings can
    name it even while another language is chosen."""

    setting: str
    language: str
    system: str
    format: str
    clock: int
    offered: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {"setting": self.setting, "language": self.language, "system": self.system, "format": self.format,
                "clock": self.clock, "offered": list(self.offered)}


ENGLISH = Locale(setting="system", language=DEFAULT, system=DEFAULT, format=BASE_FORMATS[DEFAULT], clock=12,
                 offered=OFFERED)


def setting_value(value: Any) -> str:
    """The stored ``language`` setting as the app reads it: anything that is not a known value reads as ``system``."""
    return value if isinstance(value, str) and value in SETTING_VALUES else "system"


def dev_override(env: Mapping[str, str]) -> str | None:
    """``USAGE_MONITOR_LANG``, honoured only in a dev instance (``USAGE_MONITOR_DEV_INSTANCE=1``)."""
    if env.get(DEV_INSTANCE_ENV) != "1":
        return None
    value = (env.get(DEV_LANG_ENV) or "").strip().lower()
    return value or None


def build_locale(setting: Any, windows: WindowsLanguage, *, regional: str | None = None,
                 short_time: str | None = None, preferred: Sequence[str] | None = None,
                 offered: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> Locale:
    """The locale for ``setting``, from what Windows says (``preferred``, ``regional`` and ``short_time`` are read
    from ``windows`` when not given). A stored language that is not offered reads, and is reported, as ``system``:
    settings.json keeps it, so the release that offers it picks the choice up."""
    offered = tuple(OFFERED if offered is None else offered)
    stored = setting_value(setting)
    languages = list(preferred) if preferred is not None else windows.display_languages()
    system = resolve_system(languages, offered)
    language = resolve(stored, languages, offered, dev_override=dev_override(env or {}))
    region = regional if regional is not None else windows.regional_format()
    pattern = short_time if short_time is not None else windows.short_time()
    reported = stored if stored == "system" or stored in offered else "system"
    return Locale(setting=reported, language=language, system=system, format=format_tag(language, region),
                  clock=clock_hours(language, pattern), offered=offered)


class LocaleSource:
    """The app's current ``Locale``, from the ``language`` setting and Windows. Thread-safe; cheap to call.

    Windows is read on first use, not when the source is made, so the app's log is set up by then. The display
    languages are read once: changing them signs the user out, so a running app never sees a new one. The regional
    format and the clock are read again on ``refresh()``, which the app calls on WM_SETTINGCHANGE "intl". A reader that
    raises anything reads as nothing: the app runs in English rather than not at all."""

    def __init__(self, read_setting: Callable[[], Any], *, windows: WindowsLanguage | None = None,
                 env: Mapping[str, str] | None = None, offered: Sequence[str] | None = None) -> None:
        self._read_setting = read_setting
        self._windows = windows if windows is not None else NoWindows()
        self._env = dict(env if env is not None else os.environ)
        self._offered = None if offered is None else tuple(offered)
        self._lock = threading.Lock()
        self._loaded = False
        self._preferred: list[str] = []
        self._regional: str | None = None
        self._short_time: str | None = None

    @staticmethod
    def _read(call: Callable[[], Any], fallback: Any) -> Any:
        try:
            return call()
        except Exception as exc:   # a reader is outside code: whatever it raises, the app shows English
            log.warning("language reader failed (%s); English stands in", type(exc).__name__)
            return fallback

    def _ensure(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            self._preferred = [tag for tag in (self._read(self._windows.display_languages, []) or [])
                               if isinstance(tag, str)]
            self._regional = self._read(self._windows.regional_format, None)
            self._short_time = self._read(self._windows.short_time, None)

    def refresh(self) -> None:
        """Read the regional format and the clock again (the user changed them in Settings > Time & language)."""
        self._ensure()
        regional = self._read(self._windows.regional_format, None)
        short_time = self._read(self._windows.short_time, None)
        with self._lock:
            self._regional, self._short_time = regional, short_time

    def __call__(self) -> Locale:
        self._ensure()
        with self._lock:
            preferred, regional, short_time = list(self._preferred), self._regional, self._short_time
        return build_locale(self._read_setting(), self._windows, regional=regional or "", short_time=short_time or "",
                            preferred=preferred, offered=self._offered, env=self._env)


# ── Catalogs ──────────────────────────────────────────────────────────────────

def _read_catalog(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("catalog %s unreadable (%s); its keys show English", path.name, type(exc).__name__)
        return {}
    if not isinstance(data, dict):
        log.warning("catalog %s is not an object; its keys show English", path.name)
        return {}
    return {key: value for key, value in data.items() if not key.startswith("_")}   # "_comment" and the like


class Text(str):
    """Catalog text that knows the language it came from (English when the language's catalog lacks the key), so a
    caller that changes its case uses that language's rules, and marks it ``lang="en"`` where it is English."""

    language: str = DEFAULT

    def __new__(cls, value: str, language: str = DEFAULT) -> "Text":
        text = super().__new__(cls, value)
        text.language = language
        return text


@dataclass(frozen=True)
class Found:
    """A catalog entry and the language it came from (English when the language's catalog lacks the key)."""

    value: Any
    language: str


class Catalog:
    """One language's text, with English behind it. Keys are dotted names; plural entries are objects keyed by the
    language's CLDR categories."""

    def __init__(self, language: str, own: Mapping[str, Any], english: Mapping[str, Any]) -> None:
        self.language = language
        self._own = dict(own)
        self._english = dict(english)

    def lookup(self, key: str) -> Found | None:
        if self.language != DEFAULT and key in self._own:
            return Found(self._own[key], self.language)
        if key in self._english:
            return Found(self._english[key], DEFAULT)
        return None

    def text(self, key: str, values: Mapping[str, Any] | None = None) -> Text:
        """The text for ``key`` with its placeholders filled; the key itself when no catalog has it (a bug, logged).
        It carries the language it came from."""
        found = self.lookup(key)
        if found is None or not isinstance(found.value, str):
            log.warning("catalog key %r missing", key)
            return Text(key, DEFAULT)
        return Text(fill(found.value, values), found.language)

    def plural(self, key: str, count: float, values: Mapping[str, Any] | None = None) -> Text:
        """The plural entry for ``count`` (``{count}`` is filled in too). The category is chosen by the language the
        entry came from, so an English fallback follows English rules."""
        found = self.lookup(key)
        if found is None or not isinstance(found.value, dict):
            log.warning("catalog plural %r missing", key)
            return Text(key, DEFAULT)
        forms = found.value
        form = forms.get(plural_category(found.language, count)) or forms.get("other") or ""
        return Text(fill(str(form), {"count": count, **dict(values or {})}), found.language)

    def english(self) -> dict[str, Any]:
        return dict(self._english)

    def own(self) -> dict[str, Any]:
        return dict(self._own)


def load_catalog(language: str, directory: Path = CATALOG_DIR) -> Catalog:
    """The catalog for ``language`` (any catalog language), English behind it."""
    english = _read_catalog(directory / f"{DEFAULT}.json")
    own = english if language == DEFAULT else _read_catalog(directory / f"{language}.json")
    return Catalog(language if language in LANGUAGES else DEFAULT, own, english)


def page_script(language: str, directory: Path = CATALOG_DIR) -> str:
    """The catalog as the dashboard loads it (``/static/i18n/<lang>.js``): one script that sets ``MonitorI18n``.
    Built from the JSON on request, so each catalog exists once and the page's script-src stays 'self'. The format
    tag and the clock are not in it: they come with the locale (the ``monitor-locale`` meta and ``/api/state``)."""
    catalog = load_catalog(language, directory)
    payload = {
        "language": catalog.language,
        "strings": catalog.own() if catalog.language != DEFAULT else {},
        "fallback": catalog.english(),
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # "</" can never end the script early: the JSON's own slashes are escaped where they could.
    return "window.MonitorI18n = " + body.replace("</", "<\\/") + ";\n"
