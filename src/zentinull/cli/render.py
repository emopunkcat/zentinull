"""Brutalist log renderer — ``rich``-powered terminal output for pipeline streams.

Renders structured log lines with block-char severity indicators, vivid
truecolor key=value highlighting, stage rules, and a startup banner.

Gated behind ``ZENTINULL_LOG_STYLE=brutalist``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from rich.box import HEAVY
from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

# ── Brutalist color palette ────────────────────────────────────────────────────
# High-contrast, cyber-inspired — designed for dark terminal backgrounds.
#
# Each entry is a (Style, markup_string) tuple:
#   Style  — for ``Text.stylize()`` calls
#   markup — for rich ``[markup]text[/]`` tags

_ERR_STYLE = Style(color="#FF3E3E", bold=True)
_ERR_MARKUP = "bold #FF3E3E"
_WRN_STYLE = Style(color="#FFD700", bold=True)
_WRN_MARKUP = "bold #FFD700"
_INF_STYLE = Style(color="#00D4AA")
_INF_MARKUP = "#00D4AA"
_KEY_STYLE = Style(color="#00FF9F")
_KEY_MARKUP = "#00FF9F"
_VAL_STYLE = Style(color="#FFFFFF")
_VAL_MARKUP = "#FFFFFF"
_DIM_STYLE = Style(color="#666666")
_DIM_MARKUP = "#666666"
_RULE_STYLE = Style(color="#444444")
_RULE_MARKUP = "#444444"
_TAG_STYLE = Style(color="#888888")
_TAG_MARKUP = "#888888"
_STAGE_STYLE = Style(color="#FFFFFF", bold=True, bgcolor="#00D4AA")
_STAGE_MARKUP = "bold #FFFFFF on #00D4AA"
_BANNER_BORDER = Style(color="#00D4AA")
_BANNER_TEXT = Style(color="#FFFFFF", bold=True)
_BANNER_ACCENT = Style(color="#00FF9F", bold=True)


# Regex: capture key=value where value is not quoted or bracketed
_KEYVAL_RE = re.compile(r"(\w+)=([^\s\"\[\]]+)")


def _color_keyval(matched_text: str) -> str:
    """Rich markup for a single key=value match.

    ``Text.highlight_regex`` passes the matched string, not a ``re.Match``.
    """
    m = _KEYVAL_RE.match(matched_text)
    assert m is not None, f"_color_keyval called with non-matching text: {matched_text!r}"
    key = m.group(1)
    val = m.group(2)
    return f"[{_KEY_MARKUP}]{key}[/]=[{_VAL_MARKUP}]{val}[/]"


def _render_line(line: str, *, with_tag: bool = False) -> Text | None:
    """Render a single raw log line as a ``rich.Text`` with block-char severity indicator.

    Args:
        line: The raw line (may already have a ``[tag]`` prefix from streaming).
        with_tag: If True, treat a leading ``[tag]`` as a subprocess source tag
                  and dim it.

    Returns:
        A styled ``rich.Text``, or ``None`` for empty lines.
    """
    line = line.strip()
    if not line:
        return None

    # Detect severity from the line content
    is_err = "ERR" in line
    is_wrn = not is_err and "WRN" in line

    # If the line already has a [tag] prefix from streaming, tease it apart
    if with_tag:
        tag_end = 0
        if line.startswith("[") and "]" in line:
            tag_end = line.index("]") + 1
            tag_part = line[:tag_end]
            body = line[tag_end:].lstrip()
        else:
            tag_part = ""
            body = line
    else:
        tag_part = ""
        body = line

    # ── Build output with block-char indicator ────────────────────────────
    if is_err:
        glyph = Text(" ■ ", style="bold #FFFFFF on #FF3E3E")
        body_style = _ERR_STYLE
    elif is_wrn:
        glyph = Text(" ◆ ", style="bold #000000 on #FFD700")
        body_style = _WRN_STYLE
    else:
        glyph = Text(" ● ", style="bold #000000 on #00D4AA")
        body_style = _INF_STYLE

    # Build rich Text from the body, highlighting key=value pairs
    t = Text()
    t.append(body)
    t.highlight_regex(_KEYVAL_RE, _color_keyval)

    # Apply severity style to the whole body (vivid cyan for info, not dim)
    t.stylize(body_style)

    # Assemble: [tag] glyph body
    out = Text()
    if tag_part:
        out.append(tag_part, style=_TAG_STYLE)
        out.append(" ")
    out.append_text(glyph)
    out.append(" ")
    out.append_text(t)

    return out


def render_stage(stage: str) -> None:
    """Print a brutalist horizontal rule labelled with a pipeline stage name."""
    console = _get_console()
    console.rule(f"[{_STAGE_MARKUP}] {stage} [/]", style=_RULE_STYLE, characters="━")


def render_banner() -> None:
    """Print the Zentinull brutalist startup banner."""
    console = _get_console()
    title = Text()
    title.append("ZENTINULL", style="bold #FFFFFF")
    title.append("  ◆  ", style="#00FF9F")
    title.append("DEVICE ENTITY RESOLUTION PIPELINE", style="bold #00D4AA")
    panel = Panel(
        title,
        border_style=_BANNER_BORDER,
        box=HEAVY,
        padding=(1, 3),
        expand=False,
    )
    console.print(panel)
    console.print()


def render_separator() -> None:
    """Print a thin brutalist separator line."""
    console = _get_console()
    width = min(console.width, 100)
    console.print(Text("─" * width, style=_RULE_STYLE))


def render_lines(lines: Iterable[str], *, with_tag: bool = False) -> None:
    """Render an iterable of raw log lines to the console.

    Args:
        lines: Log lines (may be a generator from subprocess output).
        with_tag: If True, treat ``[tag]`` prefixes as subprocess source tags.
    """
    console = _get_console()
    for raw in lines:
        rendered = _render_line(raw, with_tag=with_tag)
        if rendered is not None:
            console.print(rendered)


def render_line(line: str, *, with_tag: bool = False) -> None:
    """Render a single raw log line to the console."""
    rendered = _render_line(line, with_tag=with_tag)
    if rendered is not None:
        _get_console().print(rendered)


# ── Shared console ─────────────────────────────────────────────────────────────

_console: Console | None = None


def _get_console() -> Console:
    """Return a thread-safe ``Console``, created lazily on first access.

    Auto-detects TTY for color output; sets ``color_system='truecolor'``
    when on a terminal so ANSI codes are included in the rendered output.
    """
    global _console
    if _console is None:
        import sys as _sys

        _is_tty = _sys.stdout.isatty()
        _console = Console(
            stderr=False,
            force_terminal=_is_tty,
            color_system="truecolor" if _is_tty else None,
            highlight=False,
            markup=True,
        )
    return _console


def is_brutalist_enabled() -> bool:
    """Check whether brutalist rendering should be active.

    Reads the ``ZENTINULL_LOG_STYLE`` env var.
    ``brutalist``, ``regex``, or ``regex-brutalist`` → True; anything else → False.
    """
    import os

    return os.environ.get("ZENTINULL_LOG_STYLE", "").lower() in ("brutalist", "regex", "regex-brutalist")
