# SPDX-License-Identifier: BSD-2-Clause
import os
import sys
from typing import Any

# ANSI escape codes matching Homebrew
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
BOLD_RED = "\033[1;31m"
BOLD_GREEN = "\033[1;32m"
BOLD_YELLOW = "\033[1;33m"
BOLD_BLUE = "\033[1;34m"
BOLD_CYAN = "\033[1;36m"


def is_color_enabled(stream: Any = None) -> bool:
    if stream is None:
        stream = sys.stdout
    if os.environ.get("HOMEBREW_NO_COLOR") or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("HOMEBREW_COLOR") == "1":
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    if not hasattr(stream, "isatty"):
        return False
    try:
        return bool(stream.isatty())
    except (ValueError, OSError):
        return False


def style(text: str, color_code: str, stream: Any = None) -> str:
    if stream is None:
        stream = sys.stdout
    if not is_color_enabled(stream):
        return text
    return f"{color_code}{text}{RESET}"


def ohai(title: str, *lines: str, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stdout
    arrow = style("==>", BOLD_BLUE, stream)
    heading = style(title, BOLD, stream) if is_color_enabled(stream) else title
    print(f"{arrow} {heading}", file=stream)
    for line in lines:
        print(f"    {line}", file=stream)


def opoo(message: str, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stdout
    prefix = style("Warning:", BOLD_YELLOW, stream)
    print(f"{prefix} {message}", file=stream)


def onoe(message: str, stream: Any = None) -> None:
    if stream is None:
        stream = sys.stderr
    prefix = style("Error:", BOLD_RED, stream)
    print(f"{prefix} {message}", file=stream)
