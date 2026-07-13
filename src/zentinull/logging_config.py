"""Structured logging — shared across all Zentinull modules.

Usage:
    from logging_config import get_logger
    log = get_logger("ingest.sp")
    log.info({"event": "fetched", "table": "sp_devices", "rows": 581, "elapsed_ms": 1234})
    # → 19:21:23.456 [zig.ingest.sp] INFO  table=sp_devices rows=581 elapsed_ms=1234
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _cfg_attr(name: str, default: str = "") -> str:
    """Lazy import config to avoid circular import at module level."""
    from . import config as _cfg

    return getattr(_cfg, name, default)


class StructuredFormatter(logging.Formatter):
    """Key=value structured output — human-readable, grep-friendly.

    If the log message is a dict, it's rendered as key=value pairs.
    Otherwise treated as a normal string.
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23]
        name = record.name
        level = record.levelname

        # If msg is a dict, render as key=value
        if isinstance(record.msg, dict):
            parts = [f"{k}={_fmt_val(v)}" for k, v in record.msg.items()]
            msg = " ".join(parts)
        else:
            msg = record.msg % record.args if record.args else str(record.msg)

        base = f"{ts} [{name}] {level:5s} {msg}"

        if record.exc_info and record.exc_info[1]:
            base += f"\n{self.formatException(record.exc_info)}"

        if hasattr(record, "request_id") and record.request_id and record.request_id != "-":
            base += f" request_id={record.request_id}"

        return base


def _fmt_val(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, int | float):
        return str(v)
    s = str(v)
    if " " in s or "=" in s or not s:
        return json.dumps(s)
    return s


class JsonFormatter(logging.Formatter):
    """JSON-line output — for log aggregation systems."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).isoformat()
        if isinstance(record.msg, dict):
            obj: dict[str, Any] = {
                "ts": ts,
                "logger": record.name,
                "level": record.levelname,
                **record.msg,
            }
        else:
            obj = {
                "ts": ts,
                "logger": record.name,
                "level": record.levelname,
                "msg": record.msg % record.args if record.args else str(record.msg),
            }
        if hasattr(record, "request_id") and record.request_id and record.request_id != "-":
            obj["request_id"] = record.request_id
        if record.exc_info and record.exc_info[1]:
            obj["error"] = str(record.exc_info[1])
        return json.dumps(obj, default=str)


def _stdout_is_tty() -> bool:
    """Return True when stdout is a terminal — used to auto-enable colors."""
    try:
        return sys.stdout.isatty()
    except OSError:
        return False


class PrettyFormatter(logging.Formatter):
    """Ultra-clean colored output — short timestamps, color-coded keys/values.

    Format: ``HH:MM:SS  module  KEY=val KEY=val``

    Level is only shown for non-INFO (WRN, ERR, CRI, DBG).
    Colors auto-disable when stdout is not a TTY.
    """

    def __init__(self, use_colors: bool | None = None) -> None:
        super().__init__()
        self._color = use_colors if use_colors is not None else _stdout_is_tty()

    def _esc(self, code: str) -> str:
        """ANSI escape when colors enabled, empty string otherwise."""
        return f"\033[{code}m" if self._color else ""

    def format(self, record: logging.LogRecord) -> str:
        e = self._esc

        ts = datetime.now(UTC).strftime("%H:%M:%S")
        name = record.name.removeprefix("zig.")

        # Level label — only for non-INFO
        lvl = record.levelname
        if lvl == "WARNING":
            level = f"{e('1;33')}WRN{e('0')} "
        elif lvl == "ERROR":
            level = f"{e('1;31')}ERR{e('0')} "
        elif lvl == "CRITICAL":
            level = f"{e('1;31')}CRI{e('0')} "
        elif lvl == "DEBUG":
            level = f"{e('2')}DBG{e('0')} "
        else:
            level = ""

        # Message — dict → key=value, string → as-is
        if isinstance(record.msg, dict):
            parts: list[str] = []
            for k, v in record.msg.items():
                parts.append(f"{e('34')}{k}{e('0')}={self._color_val(v, e)}")
            msg = " ".join(parts)
        else:
            msg = record.msg % record.args if record.args else str(record.msg)

        base = f"{e('2')}{ts}{e('0')}  {level}{e('36')}{name}{e('0')}  {msg}"

        if record.exc_info and record.exc_info[1]:
            base += f"\n{e('1;31')}{self.formatException(record.exc_info)}{e('0')}"

        if hasattr(record, "request_id") and record.request_id and record.request_id != "-":
            base += f" {e('2')}rid={record.request_id}{e('0')}"
        return base

    def _color_val(self, v: Any, e: Any) -> str:
        """Color a value by its type."""
        if v is None:
            return f"{e('2')}null{e('0')}"
        if isinstance(v, bool):
            return f"{e('35')}{str(v).lower()}{e('0')}"
        if isinstance(v, int | float):
            return f"{e('33')}{v}{e('0')}"
        s = str(v)
        if " " in s or "=" in s or not s:
            s = json.dumps(s)
        return f"{e('32')}{s}{e('0')}"


class BrutalistFormatter(logging.Formatter):
    """Brutalist-styled output — block-char badges, vivid ANSI, heavy vertical rules.

    Format: ``■ ERR  12:34:56 │ ingest.fg    message   key=val``

    Palette (truecolor ANSI):
      - Error badge:   white on cyber-red      bg #FF3E3E  fg #FFFFFF
      - Warning badge: black on gold            bg #FFD700  fg #000000
      - Info badge:    black on electric-cyan   bg #00D4AA  fg #000000
      - Debug badge:   dim, no background
      - Keys:          matrix green             #00FF9F
      - Values:        white                    #FFFFFF
      - Module:        muted gray               #999999
      - Separator:     dark gray                #444444

    Colors auto-disable when stdout is not a TTY.
    """

    # ── Palette constants ──────────────────────────────────────────────────
    _ERR_BG = "48;2;255;62;62"  # cyber-red background
    _ERR_FG = "38;2;255;255;255"  # white text
    _WRN_BG = "48;2;255;215;0"  # gold background
    _WRN_FG = "38;2;0;0;0"  # black text
    _INF_BG = "48;2;0;212;170"  # electric-cyan background
    _INF_FG = "38;2;0;0;0"  # black text
    _KEY_FG = "38;2;0;255;159"  # matrix green
    _VAL_FG = "38;2;255;255;255"  # white
    _MOD_FG = "38;2;153;153;153"  # muted gray
    _BAR_FG = "38;2;68;68;68"  # dark separator
    _DIM = "2"  # dim
    _BOLD = "1"  # bold
    _RST = "0"  # reset
    _MODULE_WIDTH = 14  # characters — module names are padded to this width for column alignment

    def __init__(self, use_colors: bool | None = None) -> None:
        super().__init__()
        self._color = use_colors if use_colors is not None else _stdout_is_tty()

    def _e(self, code: str) -> str:
        """ANSI escape when colors enabled, empty string otherwise."""
        return f"\033[{code}m" if self._color else ""

    def format(self, record: logging.LogRecord) -> str:
        e = self._e

        ts = datetime.now(UTC).strftime("%H:%M:%S")
        name = record.name.removeprefix("zig.")
        lvl = record.levelname
        # ── Level badge ───────────────────────────────────────────────────
        if lvl in ("ERROR", "CRITICAL"):
            abbr = "CRI" if lvl == "CRITICAL" else "ERR"
            glyph_char = "■"
            badge = f"{e(self._ERR_BG)}{e(self._ERR_FG)}{e(self._BOLD)} {glyph_char} {abbr} {e(self._RST)}"
        elif lvl == "WARNING":
            glyph_char = "◆"
            badge = f"{e(self._WRN_BG)}{e(self._WRN_FG)}{e(self._BOLD)} {glyph_char} WRN {e(self._RST)}"
        elif lvl == "DEBUG":
            badge = f"{e(self._DIM)}· DBG{e(self._RST)}"
        else:  # INFO
            glyph_char = "●"
            badge = f"{e(self._INF_BG)}{e(self._INF_FG)}{e(self._BOLD)} {glyph_char} INF {e(self._RST)}"
        # ── Message body ──────────────────────────────────────────────────
        if isinstance(record.msg, dict):
            parts: list[str] = []
            for k, v in record.msg.items():
                parts.append(f"{e(self._KEY_FG)}{k}{e(self._RST)}{e(self._VAL_FG)}={_fmt_val(v)}{e(self._RST)}")
            body = "  ".join(parts)
        else:
            raw = record.msg % record.args if record.args else str(record.msg)
            body = f"{e(self._VAL_FG)}{raw}{e(self._RST)}"

        # ── Assemble ──────────────────────────────────────────────────────
        bar = f"{e(self._BAR_FG)}│{e(self._RST)}"
        ts_part = f"{e(self._DIM)}{ts}{e(self._RST)}"
        mod_part = f"{e(self._MOD_FG)}{name.ljust(self._MODULE_WIDTH)}{e(self._RST)}"

        base = f"{badge} {ts_part} {bar} {mod_part}  {body}"

        if record.exc_info and record.exc_info[1]:
            base += f"\n{e(self._ERR_FG)}{e(self._BOLD)}  ╰─ {self.formatException(record.exc_info)}{e(self._RST)}"

        if hasattr(record, "request_id") and record.request_id and record.request_id != "-":
            base += f"  {e(self._DIM)}rid={record.request_id}{e(self._RST)}"

        return base


# ── Format template engine ──────────────────────────────────────────────────

#: Regex for template variables: ``{key}``, ``{key:U}``, ``{key:B}``, ``{key:ms}``
_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)(?::(U|B|ms))?\}")


def _render_template(template: str, data: dict[str, object], *, fmt_fn: Callable[[Any], str] = _fmt_val) -> str:
    """Render a format template string by substituting ``{key}`` variables from *data*.

    Supported formatters:
      ``{key}``    — raw value (via *fmt_fn*)
      ``{key:U}``  — uppercase
      ``{key:B}``  — bracket-wrap ``[value]``
      ``{key:ms}`` — format milliseconds: ``<1000`` → ``234ms``, ``>=1000`` → ``1.7s``

    *fmt_fn* controls value formatting — use ``str`` for unquoted headline
    mode, or the default ``_fmt_val`` for key=value output.
    """

    def _replace(m: re.Match[str]) -> str:
        key = m.group(1)
        fmt_spec = m.group(2)
        val = data.get(key)
        if val is None:
            return ""
        s = fmt_fn(val)
        if fmt_spec == "U":
            return s.upper()
        if fmt_spec == "B":
            return f"[{s}]"
        if fmt_spec == "ms":
            if isinstance(val, int | float | str):
                try:
                    n = int(val)
                except (ValueError, TypeError):
                    return s
                if n >= 1000:
                    return f" ──── {n / 1000:.1f}s"
                return f" ──── {n}ms"
            return s
        return s

    return _TEMPLATE_VAR_RE.sub(_replace, template)


class RegexBrutalistFormatter(BrutalistFormatter):
    """Regex-powered brutalist formatter — pattern-matched highlighting + format templates.

    Extends BrutalistFormatter with two layers:

    1. **Format templates** — structured status lines for known events.
       When a dict message matches a format rule, it is rendered as a clean
       one-liner instead of raw key=value. Example::

           log.info({"event": "ingested", "source": "fg", "rows": 250, "elapsed_ms": 1700})
           # → ● INF  12:34:56 │ pipeline  [INGEST] - FG | [250] ──── 1.7s

    2. **Regex highlighting** — falls back to colorized key=value when no
       format template matches. Keys/values are matched against color rules;
       unmatched content is dimmed.

    Configuration via environment variables:

    ``ZENTINULL_LOG_RULES``
        ``@@``-separated ``regex:style`` pairs for color highlighting.

    ``ZENTINULL_LOG_FORMATS``
        ``@@``-separated ``pattern~template`` pairs for structured rendering.
        Template variables use ``{key}`` syntax with optional formatters:
        ``{key:U}`` uppercase, ``{key:B}`` bracket-wrap, ``{key:ms}`` ms→human.
        Example::

            ingested~[INGEST] - {source:U} | [{rows}] ──── {elapsed_ms:ms}

    ``ZENTINULL_LOG_SHOW``
        ``all`` (default) or ``matches`` — dim non-matching lines.

    Colors auto-disable when stdout is not a TTY.
    """

    # ── Default highlight rules ───────────────────────────────────────────
    _DEFAULT_RULES: tuple[tuple[str, str], ...] = (
        (r"error|fail|fatal|exception|critical|traceback", "bold_red"),
        (r"done|complete|success|ok|finished", "bold_green"),
        (r"warning|warn", "yellow"),
        (r"started|begin|initializing", "cyan"),
        (r"elapsed|duration|took|ms$", "magenta"),
        (r"inserted|ingested|exported|loaded|copied|fetched|saved", "matrix"),
        (r"^\d+$", "gold"),
    )

    # ── Named styles → ANSI codes ─────────────────────────────────────────
    _NAMED_STYLES: dict[str, str] = {
        "bold_red": "1;38;2;255;62;62",
        "bold_green": "1;38;2;0;255;128",
        "yellow": "38;2;255;215;0",
        "cyan": "38;2;0;212;255",
        "magenta": "38;2;255;85;255",
        "matrix": "38;2;0;255;159",
        "gold": "38;2;255;180;0",
        "dim": "2",
    }

    # ── Default format templates ──────────────────────────────────────────
    _DEFAULT_FORMATS: tuple[tuple[str, str], ...] = (
        (r"event=ingested\b", "[INGEST] - {source:U} | [{rows}]{elapsed_ms:ms}"),
        (r"event=inserted\b", "[INSERT] - {source:U} | {table} [{rows}]{elapsed_ms:ms}"),
        (r"event=exported\b", "[EXPORT] - {source} | {records} records"),
        (r"event=export_complete\b", "[EXPORT] ✓ {total_records} records{elapsed_ms:ms}"),
        (r"event=ingest_failed\b", "[INGEST] - {source:U} | ✗ {error}"),
        (r"event=pipeline_stage\b", "[PIPE] ── {stage:U} ──"),
        (r"event=pipeline_complete\b", "[PIPE] ✓ done | {devices} devices{elapsed_ms:ms}"),
        (r"event=mesh_loaded\b", "[LOAD] ✓ mesh | {devices} devices, {records} records{elapsed_ms:ms}"),
        (r"event=backup_started\b", "[BACKUP] ── {output_dir}"),
        (r"event=copied\b", "[BACKUP] ✓ {file} | {size_bytes:B}"),
        (r"event=server_start\b", "[API] ── {url}"),
        (r"event=done\b", "✓ {step} {status}{elapsed_ms:ms}"),
    )

    def __init__(self, use_colors: bool | None = None) -> None:
        super().__init__(use_colors)
        self._rules = self._load_rules()
        self._format_rules = self._load_formats()
        from zentinull import config

        self._show_mode = os.environ.get("ZENTINULL_LOG_SHOW", getattr(config, "LOG_SHOW", "all")).lower()

    # ── Rule loading ──────────────────────────────────────────────────────

    def _load_rules(self) -> list[tuple[re.Pattern[str], str]]:
        """Parse ZENTINULL_LOG_RULES into (compiled_regex, ansi_code) pairs."""
        from zentinull import config

        raw = os.environ.get("ZENTINULL_LOG_RULES", getattr(config, "LOG_RULES", ""))
        if not raw:
            return [(re.compile(p, re.I), self._resolve_ansi(s)) for p, s in self._DEFAULT_RULES]

        rules: list[tuple[re.Pattern[str], str]] = []
        for chunk in raw.split("@@"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split(":", 1)
            if len(parts) != 2:
                continue
            pat_str, style = parts[0].strip(), parts[1].strip()
            if not pat_str or not style:
                continue
            with suppress(re.error):
                rules.append((re.compile(pat_str, re.I), self._resolve_ansi(style)))
        return rules or [(re.compile(p, re.I), self._resolve_ansi(s)) for p, s in self._DEFAULT_RULES]

    def _resolve_ansi(self, style: str) -> str:
        """Map a style name or raw ANSI code to an ANSI escape parameter string."""
        return self._NAMED_STYLES.get(style, style)

    # ── Matching ──────────────────────────────────────────────────────────

    def _match_style(self, text: str) -> str | None:
        """Return the ANSI code for the first rule matching *text*, or None."""
        for pat, ansi in self._rules:
            if pat.search(text):
                return ansi
        return None

    # ── Format templates ──────────────────────────────────────────────────

    def _load_formats(self) -> list[tuple[re.Pattern[str], str]]:
        """Parse ZENTINULL_LOG_FORMATS into (compiled_regex, template_string) pairs.

        Format: ``pattern~template@@pattern~template``
        The ``~`` separates the match regex from the format string.
        """
        from zentinull import config

        raw = os.environ.get("ZENTINULL_LOG_FORMATS", getattr(config, "LOG_FORMATS", ""))
        if not raw:
            return [(re.compile(p, re.I), t) for p, t in self._DEFAULT_FORMATS]

        rules: list[tuple[re.Pattern[str], str]] = []
        for chunk in raw.split("@@"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split("~", 1)
            if len(parts) != 2:
                continue
            pat_str, template = parts[0].strip(), parts[1].strip()
            if not pat_str or not template:
                continue
            with suppress(re.error):
                rules.append((re.compile(pat_str, re.I), template))
        return rules or [(re.compile(p, re.I), t) for p, t in self._DEFAULT_FORMATS]

    def _match_format(self, data: dict[str, object]) -> str | None:
        """Return the rendered template if *data* matches a format rule, or None.

        The match runs against a flattened ``key=value`` string built from the dict,
        so a rule like ``event=ingested`` matches the ``event`` key's value.
        """
        if not self._format_rules:
            return None
        search_str = " ".join(f"{k}={_fmt_val(v)}" for k, v in data.items())
        for pat, template in self._format_rules:
            if pat.search(search_str):
                return _render_template(template, data)
        return None

    def _styled(self, text: str, ansi: str) -> str:
        """Wrap *text* in the given ANSI escape codes.

        When colors are disabled this is a no-op — the escape sequences
        collapse to empty strings because ``_e`` returns ``""``.
        """
        e = self._e
        return f"{e(ansi)}{text}{e(self._RST)}"

    # ── Format ────────────────────────────────────────────────────────────

    def format(self, record: logging.LogRecord) -> str:
        e = self._e

        ts = datetime.now(UTC).strftime("%H:%M:%S")
        name = record.name.removeprefix("zig.")
        lvl = record.levelname

        # ── Level badge (same as Brutalist) ────────────────────────────────
        if lvl in ("ERROR", "CRITICAL"):
            abbr = "CRI" if lvl == "CRITICAL" else "ERR"
            glyph_char = "■"
            badge = f"{e(self._ERR_BG)}{e(self._ERR_FG)}{e(self._BOLD)} {glyph_char} {abbr} {e(self._RST)}"
        elif lvl == "WARNING":
            glyph_char = "◆"
            badge = f"{e(self._WRN_BG)}{e(self._WRN_FG)}{e(self._BOLD)} {glyph_char} WRN {e(self._RST)}"
        elif lvl == "DEBUG":
            badge = f"{e(self._DIM)}· DBG{e(self._RST)}"
        else:  # INFO
            glyph_char = "●"
            badge = f"{e(self._INF_BG)}{e(self._INF_FG)}{e(self._BOLD)} {glyph_char} INF {e(self._RST)}"

        # ── Message body ───────────────────────────────────────────────────
        if isinstance(record.msg, dict):
            # Try format template first — produces a clean one-liner
            template_body = self._match_format(record.msg)
            if template_body is not None:
                # Apply regex color highlighting to the rendered template
                ansi = self._match_style(template_body)
                body = self._styled(template_body, ansi) if ansi else f"{e(self._VAL_FG)}{template_body}{e(self._RST)}"
            else:
                # Fall back to key=value rendering with per-key highlighting
                parts: list[str] = []
                any_match = False
                for k, v in record.msg.items():
                    val_str = _fmt_val(v)

                    # Try matching the value first (more specific / interesting)
                    val_ansi = self._match_style(val_str)
                    if val_ansi:
                        any_match = True
                        parts.append(f"{e(self._DIM)}{k}{e(self._RST)}={self._styled(val_str, val_ansi)}")
                        continue

                    # Try matching the key
                    key_ansi = self._match_style(k)
                    if key_ansi:
                        any_match = True
                        parts.append(f"{self._styled(k, key_ansi)}={e(self._VAL_FG)}{val_str}{e(self._RST)}")
                        continue

                    # No match — dim key, white value
                    parts.append(f"{e(self._DIM)}{k}{e(self._RST)}={e(self._VAL_FG)}{val_str}{e(self._RST)}")

                body = "  ".join(parts)

                # Dim entire line when show_mode=matches and nothing matched
                if self._show_mode == "matches" and not any_match:
                    body = f"{e(self._DIM)}{body}{e(self._RST)}"
        else:
            raw = record.msg % record.args if record.args else str(record.msg)
            ansi = self._match_style(raw)
            if ansi:
                body = self._styled(raw, ansi)
            elif self._show_mode == "matches":
                body = f"{e(self._DIM)}{raw}{e(self._RST)}"
            else:
                body = f"{e(self._VAL_FG)}{raw}{e(self._RST)}"

        # ── Assemble ──────────────────────────────────────────────────────
        bar = f"{e(self._BAR_FG)}│{e(self._RST)}"
        ts_part = f"{e(self._DIM)}{ts}{e(self._RST)}"
        mod_part = f"{e(self._MOD_FG)}{name.ljust(self._MODULE_WIDTH)}{e(self._RST)}"

        base = f"{badge} {ts_part} {bar} {mod_part}  {body}"

        if record.exc_info and record.exc_info[1]:
            base += f"\n{e(self._ERR_FG)}{e(self._BOLD)}  ╰─ {self.formatException(record.exc_info)}{e(self._RST)}"

        if hasattr(record, "request_id") and record.request_id and record.request_id != "-":
            base += f"  {e(self._DIM)}rid={record.request_id}{e(self._RST)}"

        return base


class ColumnarFormatter(logging.Formatter):
    """Compact columnar output — tight prefix, format templates, indented overflow.

    Main line format (capped at ``_LINE_WIDTH``, default 48 chars)::

        HH:MM ▣ MODULE  headline text

    The prefix is fixed-width: ``HH:MM`` (5) + space + level glyph (1) +
    space + module name (padded to ``_MODULE_WIDTH``) + 2 spaces = 17 chars.
    That leaves 31 chars for the headline at the default 48-char width.

    When a dict message overflows the main line, remaining key-value pairs
    are rendered as indented continuation lines::

                     · key: value  · key: value

    **Format templates** — known events get compact headlines via
    ``ZENTINULL_LOG_COMPACT_FORMATS`` (``@@``-separated ``pattern~template``
    pairs).  Template variables use ``{key}`` with optional ``:U`` / ``:B`` /
    ``:ms`` formatters.  Keys consumed by the template are excluded from
    the continuation lines.

    **Key abbreviations** — ``ZENTINULL_LOG_COLUMN_MAP`` (``key=ABBR``
    pairs) shortens common key names in both headlines and details.

    **Line width** — ``ZENTINULL_LOG_COMPACT_WIDTH`` (default 48, minimum 32).

    Colors auto-disable when stdout is not a TTY.
    """

    # ── Layout constants ──────────────────────────────────────────────────
    _TS_WIDTH = 5  # "HH:MM"
    _GLYPH_WIDTH = 1  # ■ ◆ ● ·
    _MODULE_WIDTH = 7  # padded module name
    # visible prefix width = _TS_WIDTH + 1 + _GLYPH_WIDTH + 1 + _MODULE_WIDTH + 2 = 17
    _PREFIX_VISIBLE = 17
    _DEFAULT_WIDTH = 48
    _MIN_WIDTH = 32

    # ── Palette ───────────────────────────────────────────────────────────
    _TS_FG = "38;2;153;153;153"  # dim gray timestamp
    _MOD_FG = "38;2;0;212;255"  # cyan module name
    _LABEL_FG = "38;2;255;215;0"  # bold gold KEY:
    _VAL_STR_FG = "38;2;255;255;255"  # white string values
    _VAL_NUM_FG = "38;2;0;255;159"  # matrix green numbers / bools
    _VAL_NULL_FG = "38;2;153;153;153"  # dim null
    _BULLET_FG = "38;2;68;68;68"  # dark bullet for detail lines
    _DETAIL_FG = "38;2;153;153;153"  # dim detail key=value text
    _ERR_FG = "38;2;255;62;62"  # red ERROR/CRITICAL
    _WRN_FG = "38;2;255;215;0"  # yellow WARNING
    _DIM = "2"
    _BOLD = "1"
    _RST = "0"

    # Level badge glyphs
    _GLYPH_ERR = "■"
    _GLYPH_WRN = "◆"
    _GLYPH_INF = "●"
    _GLYPH_DBG = "·"

    # ── Default key abbreviations ──────────────────────────────────────────
    _DEFAULT_COLUMN_MAP: dict[str, str] = {
        "source": "SRC",
        "error": "ERR",
        "elapsed_ms": "ELAPSED",
    }

    # ── Module name abbreviations ──────────────────────────────────────────
    _MODULE_ABBREV: dict[str, str] = {
        "cli.pipeline": "pipe",
        "cli.streaming": "stream",
        "cli.backup": "backup",
        "cli.db_mgmt": "db",
        "cli.status": "status",
        "ingest.sp": "sp",
        "ingest.me_ec": "me_ec",
        "ingest.me_mdm": "mdm",
        "ingest.fg": "fg",
        "ingest.zbx": "zbx",
        "ingest.ad": "ad",
        "ingest.sdp": "sdp",
        "ingest.auth": "auth",
        "export": "export",
        "api.router": "api",
        "api.server": "api",
        "api.db": "api",
    }

    # ── Default compact format templates ───────────────────────────────────
    _DEFAULT_COMPACT_FORMATS: tuple[tuple[str, str], ...] = (
        # ── Ingest ──
        (r"event=ingesting\b", "⏳ {source:U}"),
        (r"event=ingested\b", "✓ {source:U} +{rows}"),
        (r"event=ingest_failed\b", "✗ {source:U}"),
        (r"event=inserted\b", "+{rows} → {table}"),
        (r"event=fetching\b", "↓ {url:B}"),
        (r"event=empty\b", "∅ {source:U} {table}"),
        (r"event=fetch_failed\b", "✗ {source:U} {endpoint}"),
        (r"event=get_failed\b", "✗ {source:U} {path:B}"),
        # ── Auth ──
        (r"event=oauth_refresh_failed\b", "✗ oauth refresh"),
        (r"event=oauth_load_failed\b", "✗ oauth load"),
        (r"event=oauth_save_failed\b", "✗ oauth save"),
        (r"event=auth_failed\b", "✗ {source:U} auth"),
        (r"event=ldap_bind_failed\b", "✗ ldap bind"),
        # ── Export ──
        (r"event=exported\b", "⇢ {source:U} +{records}"),
        (r"event=export_complete\b", "✓ {total_records} records"),
        (r"event=export_done\b", "✓ export {records}"),
        (r"event=export_empty\b", "∅ no records"),
        (r"event=source_breakdown\b", "  {source:U} {records}"),
        (r"event=coverage\b", "  {field} {filled}/{total}"),
        (r"event=skip\b", "⊘ {source:U} {reason}"),
        # ── Pipeline ──
        (r"event=pipeline_stage\b", "── {stage:U} ──"),
        (r"event=pipeline_complete\b", "✓ done {devices}d"),
        (r"event=dry_run\b", "── DRY RUN ──"),
        (r"event=dry_run_step\b", "  step {step}"),
        # ── Mesh / Load ──
        (r"event=mesh_loaded\b", "✓ {devices}d {records}r"),
        (r"event=mesh_connected\b", "✓ mesh connected"),
        (r"event=mesh_not_found\b", "∅ no mesh"),
        # ── Backup ──
        (r"event=backup_started\b", "⏳ backup"),
        (r"event=copied\b", "✓ {file}"),
        (r"event=copy_failed\b", "✗ {file}"),
        (r"event=backup_complete\b", "✓ {files_copied}/{total_files}"),
        (r"event=wal_checkpoint\b", "{db} WAL ✓"),
        # ── DB management ──
        (r"event=list_dbs\b", "{files} dbs"),
        (r"event=no_db_files\b", "∅ no dbs"),
        (r"event=db_open_failed\b", "✗ {file}"),
        (r"event=vacuumed\b", "✓ {file}"),
        (r"event=vacuum_failed\b", "✗ {file}"),
        (r"event=vacuum_dbs_done\b", "✓ saved {total_saved}"),
        (r"event=check_dbs_done\b", "✓ {passed}/{files}"),
        (r"event=integrity_check_failed\b", "✗ {file}"),
        (r"event=integrity_check_error\b", "✗ {file}"),
        # ── Server / API ──
        (r"event=server_start\b", "↑ server"),
        (r"event=request\b", "← {endpoint}"),
        (r"event=lookup_hit\b", "✓ {query:B}"),
        (r"event=lookup_miss\b", "∅ {query:B}"),
        (r"event=resolve_miss\b", "∅ resolve"),
        (r"event=search_error\b", "✗ search"),
        (r"event=dashboard_error\b", "✗ dashboard"),
        (r"event=anomalies_error\b", "✗ anomalies"),
        (r"event=batch_lookup_error\b", "✗ batch lookup"),
        # ── Generic ──
        (r"event=done\b", "✓ {step}"),
        (r"event=skipped\b", "⊘ {file}"),
        (r"event=splink_skip_training\b", "⊘ no training"),
    )

    def __init__(self, use_colors: bool | None = None) -> None:
        super().__init__()
        self._color = use_colors if use_colors is not None else _stdout_is_tty()
        self._column_map = self._load_column_map()
        self._format_rules = self._load_compact_formats()
        raw_w = os.environ.get("ZENTINULL_LOG_COMPACT_WIDTH", _cfg_attr("LOG_COMPACT_WIDTH", ""))
        try:
            w = int(raw_w)
        except (ValueError, TypeError):
            w = self._DEFAULT_WIDTH
        self._line_width = max(w, self._MIN_WIDTH) if raw_w else self._DEFAULT_WIDTH
        self._headline_max = self._line_width - self._PREFIX_VISIBLE

    def _e(self, code: str) -> str:
        return f"\033[{code}m" if self._color else ""

    # ── Key abbreviation map ───────────────────────────────────────────────

    def _load_column_map(self) -> dict[str, str]:
        raw = os.environ.get("ZENTINULL_LOG_COLUMN_MAP", _cfg_attr("LOG_COLUMN_MAP", ""))
        mapping = dict(self._DEFAULT_COLUMN_MAP)
        if not raw:
            return mapping
        for chunk in raw.split("@@"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split("=", 1)
            if len(parts) != 2:
                continue
            k, v = parts[0].strip(), parts[1].strip()
            if k and v:
                mapping[k] = v
        return mapping

    def _header_for(self, key: str) -> str:
        return self._column_map.get(key, key.upper())

    # ── Module abbreviation ────────────────────────────────────────────────

    def _abbrev_module(self, name: str) -> str:
        """Abbreviate a logger name to at most _MODULE_WIDTH chars."""
        stripped = name.removeprefix("zig.")
        if stripped in self._MODULE_ABBREV:
            return self._MODULE_ABBREV[stripped]
        # Take the last component and truncate
        last = stripped.rsplit(".", 1)[-1]
        return last[: self._MODULE_WIDTH]

    # ── Value formatting ───────────────────────────────────────────────────

    def _fmt_col_val(self, v: object) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return str(v).lower()
        if isinstance(v, int | float):
            return str(v)
        s = str(v)
        if " " in s or not s:
            return json.dumps(s)
        return s

    # ── Compact format template engine ─────────────────────────────────────

    def _load_compact_formats(self) -> list[tuple[re.Pattern[str], str]]:
        raw = os.environ.get("ZENTINULL_LOG_COMPACT_FORMATS", _cfg_attr("LOG_COMPACT_FORMATS", ""))
        if not raw:
            return [(re.compile(p, re.I), t) for p, t in self._DEFAULT_COMPACT_FORMATS]

        rules: list[tuple[re.Pattern[str], str]] = []
        for chunk in raw.split("@@"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split("~", 1)
            if len(parts) != 2:
                continue
            pat_str, template = parts[0].strip(), parts[1].strip()
            if not pat_str or not template:
                continue
            with suppress(re.error):
                rules.append((re.compile(pat_str, re.I), template))
        return rules or [(re.compile(p, re.I), t) for p, t in self._DEFAULT_COMPACT_FORMATS]

    def _match_compact_format(self, data: dict[str, object]) -> tuple[str, set[str]] | None:
        """Return (rendered_headline, consumed_keys) if data matches a template, else None."""
        if not self._format_rules:
            return None
        search_str = " ".join(f"{k}={_fmt_val(v)}" for k, v in data.items())
        for pat, template in self._format_rules:
            if pat.search(search_str):
                headline = _render_template(template, data, fmt_fn=str)
                consumed = {m.group(1) for m in _TEMPLATE_VAR_RE.finditer(template)}
                consumed.add("event")  # always consumed — headline encodes the event
                return headline, consumed
        return None

    # ── Per-key value color ────────────────────────────────────────────────

    def _val_color(self, v: object) -> str:
        """Return the ANSI code for a value's colour."""
        if v is None:
            return self._VAL_NULL_FG
        if isinstance(v, int | float | bool):
            return self._VAL_NUM_FG
        return self._VAL_STR_FG

    # ── Detail line builder ────────────────────────────────────────────────

    def _build_detail(self, data: dict[str, object], consumed: set[str] | None = None) -> str | None:
        """Build a single indented continuation line from remaining key-value pairs.

        Returns None when there are no unused keys.
        """
        consumed = consumed or set()
        remaining = [(k, v) for k, v in data.items() if k not in consumed]
        if not remaining:
            return None

        e = self._e
        indent = " " * self._PREFIX_VISIBLE
        bullet = f"{e(self._BULLET_FG)}·{e(self._RST)} "

        parts: list[str] = []
        for k, v in remaining:
            header = self._header_for(k)
            val_str = self._fmt_col_val(v)
            parts.append(
                f"{e(self._BOLD)}{e(self._LABEL_FG)}{header}:{e(self._RST)} "
                f"{e(self._val_color(v))}{val_str}{e(self._RST)}"
            )
        return f"{indent}{bullet}{'  '.join(parts)}"

    # ── Main format method ─────────────────────────────────────────────────

    def format(self, record: logging.LogRecord) -> str:
        e = self._e

        ts = datetime.now(UTC).strftime("%H:%M")
        name = record.name
        lvl = record.levelname

        # ── Level glyph ───────────────────────────────────────────────────
        if lvl in ("ERROR", "CRITICAL"):
            glyph = self._GLYPH_ERR
            lvl_color = self._ERR_FG
        elif lvl == "WARNING":
            glyph = self._GLYPH_WRN
            lvl_color = self._WRN_FG
        elif lvl == "DEBUG":
            glyph = self._GLYPH_DBG
            lvl_color = self._DIM
        else:
            glyph = self._GLYPH_INF
            lvl_color = ""

        # ── Build prefix ──────────────────────────────────────────────────
        ts_str = f"{e(self._TS_FG)}{ts}{e(self._RST)}"
        mod_str = self._abbrev_module(name).ljust(self._MODULE_WIDTH)
        mod_part = f"{e(self._MOD_FG)}{mod_str}{e(self._RST)}"

        if lvl_color:
            glyph_str = f"{e(self._BOLD)}{e(lvl_color)}{glyph}{e(self._RST)}"
        else:
            glyph_str = f"{e(self._DIM)}{glyph}{e(self._RST)}"

        prefix = f"{ts_str} {glyph_str} {mod_part}  "

        # ── Body ──────────────────────────────────────────────────────────
        detail: str | None = None
        if isinstance(record.msg, dict):
            match = self._match_compact_format(record.msg)
            if match is not None:
                headline, consumed = match
                headline = f"{e(self._VAL_STR_FG)}{headline}{e(self._RST)}"
                detail = self._build_detail(record.msg, consumed)
            else:
                # No template match — render as key:value columns,
                # fitting what we can on the main line
                e2 = e
                parts: list[str] = []
                for k, v in record.msg.items():
                    header = self._header_for(k)
                    val_str = self._fmt_col_val(v)
                    parts.append(
                        f"{e2(self._BOLD)}{e2(self._LABEL_FG)}{header}:{e2(self._RST)} "
                        f"{e2(self._val_color(v))}{val_str}{e2(self._RST)}"
                    )
                sep = f" {e2(self._DIM)}|{e2(self._RST)} "
                # Build segments greedily up to headline_max visible chars
                headline_parts: list[str] = []
                detail_parts: list[str] = []
                visible = 0
                in_headline = True

                def _visible_len(s: str) -> int:
                    """Strip ANSI escapes then return len."""
                    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))

                for _i, p in enumerate(parts):
                    seg = sep + p if headline_parts else p
                    seg_vis = _visible_len(seg)
                    if in_headline:
                        if visible + seg_vis <= self._headline_max:
                            headline_parts.append(p)
                            visible += seg_vis
                        else:
                            in_headline = False
                            detail_parts.append(p)
                    else:
                        detail_parts.append(p)

                headline = sep.join(headline_parts) if headline_parts else ""
                if detail_parts:
                    indent = " " * self._PREFIX_VISIBLE
                    bullet = f"{e2(self._BULLET_FG)}·{e2(self._RST)} "
                    detail = f"{indent}{bullet}{sep.join(detail_parts)}"
        else:
            raw = record.msg % record.args if record.args else str(record.msg)
            # Truncate long string messages
            if len(raw) > self._headline_max:
                headline = f"{e(self._VAL_STR_FG)}{raw[: self._headline_max - 1]}…{e(self._RST)}"
                indent = " " * self._PREFIX_VISIBLE
                bullet = f"{e(self._BULLET_FG)}·{e(self._RST)} "
                detail = f"{indent}{bullet}{e(self._VAL_STR_FG)}{raw}{e(self._RST)}"
            else:
                headline = f"{e(self._VAL_STR_FG)}{raw}{e(self._RST)}"

        # ── Assemble ──────────────────────────────────────────────────────
        base = f"{prefix}{headline}"
        if detail:
            base += f"\n{detail}"

        if record.exc_info and record.exc_info[1]:
            base += f"\n{e(self._ERR_FG)}{e(self._BOLD)}  ╰─ {self.formatException(record.exc_info)}{e(self._RST)}"

        if hasattr(record, "request_id") and record.request_id and record.request_id != "-":
            base += f"  {e(self._DIM)}rid={record.request_id}{e(self._RST)}"

        return base


# Request correlation ID — set by API middleware, read by RequestIDFilter
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Get the current request ID from context, or empty string."""
    return request_id_var.get()


class RequestIDFilter(logging.Filter):
    """Inject request_id from contextvar into every log record.

    Falls back to \"-\" when no request context is active (e.g., CLI mode).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_var.get()
        record.request_id = rid if rid else "-"
        return True


# ── Logger factory ───────────────────────────────────────────────────────────

_loggers: dict[str, logging.Logger] = {}
_configured = False


def setup(*, level: str = "INFO", json_output: bool = False, log_file: Path | str | None = None) -> None:
    """Initialize logging globally. Call once at startup."""
    global _configured
    _configured = True

    root = logging.getLogger("zig")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.propagate = False

    from zentinull import config

    style_raw = os.environ.get("ZENTINULL_LOG_STYLE", getattr(config, "LOG_STYLE", "")).lower()
    if json_output:
        stdout_fmt: logging.Formatter = JsonFormatter()
        file_fmt: logging.Formatter = JsonFormatter()
    elif style_raw in ("brutalist",):
        stdout_fmt = BrutalistFormatter()
        file_fmt = StructuredFormatter()
    elif style_raw in ("regex", "regex-brutalist"):
        stdout_fmt = RegexBrutalistFormatter()
        file_fmt = StructuredFormatter()
    elif style_raw in ("columnar", "columns"):
        stdout_fmt = ColumnarFormatter()
        file_fmt = StructuredFormatter()
    else:
        pretty_raw = os.environ.get("ZENTINULL_LOG_PRETTY", getattr(config, "LOG_PRETTY", "auto")).lower()
        if pretty_raw in ("0", "false", "no", "off"):
            stdout_fmt = StructuredFormatter()
        elif pretty_raw in ("1", "true", "yes", "force", "always", "on"):
            stdout_fmt = PrettyFormatter(use_colors=True)
        else:
            stdout_fmt = PrettyFormatter()  # auto-detect TTY
        file_fmt = StructuredFormatter()  # always plain for log files
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setLevel(root.level)
    stdout.setFormatter(stdout_fmt)
    root.addHandler(stdout)
    root.addFilter(RequestIDFilter())

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(path), encoding="utf-8")
        fh.setLevel(root.level)
        fh.setFormatter(file_fmt)
        root.addHandler(fh)


def get_logger(name: str) -> logging.Logger:
    """Get a structured logger for a component.

    Names follow dotted hierarchy:
        ingest.sp, ingest.me, ingest.fg, ingest.zbx, ingest.ad, ingest.sdp
        pipeline, splink
        api.server, api.db, api.router
    """
    global _configured
    if not _configured:
        setup()

    full_name = f"zig.{name}"
    if full_name not in _loggers:
        _loggers[full_name] = logging.getLogger(full_name)
    return _loggers[full_name]


# ── Helpers ──────────────────────────────────────────────────────────────────


class StepTimer:
    """Context manager for timing a block with structured logging.

    with StepTimer(log, "splink.predict"):
        linker.predict()
    # → 2026-07-10T19:21:23.456 [zig.splink] INFO  step=splink.predict elapsed_ms=2140
    """

    def __init__(self, log: logging.Logger, step: str) -> None:
        self._log = log
        self._step = step
        self._t0: float = 0

    def __enter__(self) -> StepTimer:
        self._t0 = time.perf_counter()
        self._log.info({"step": self._step, "status": "started"})
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        status = "error" if args[0] else "done"
        self._log.info(
            {
                "step": self._step,
                "status": status,
                "elapsed_ms": elapsed_ms,
            }
        )
        return False  # type: ignore[return-value]
