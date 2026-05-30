#!/usr/bin/env python3
"""
主要作用:
- 提供项目共享的轻量日志输出函数
- 统一主链脚本和核心模块的日志风格
"""

import sys
import threading
import time
from datetime import datetime


_LIVE_PROGRESS_ACTIVE = False
_LIVE_PROGRESS_WIDTH = 0
_SPINNER_THREAD = None
_SPINNER_STOP = None
_SPINNER_MESSAGE = ""
_SPINNER_LOCK = threading.Lock()
_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def _prefix_for(level="INFO"):
    prefix = {
        "INFO": "INFO",
        "SUCCESS": "SUCCESS",
        "WARNING": "WARNING",
        "ERROR": "ERROR",
        "DEBUG": "DEBUG",
    }
    return prefix.get(level, level)


def _render_line(message, level="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    return f"[{now}] {_prefix_for(level)}: {message}"


def _is_tty():
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def clear_live_progress():
    global _LIVE_PROGRESS_ACTIVE, _LIVE_PROGRESS_WIDTH
    if _LIVE_PROGRESS_ACTIVE and _is_tty():
        sys.stdout.write("\n")
        sys.stdout.flush()
    _LIVE_PROGRESS_ACTIVE = False
    _LIVE_PROGRESS_WIDTH = 0


def _spinner_loop():
    frame_idx = 0
    while _SPINNER_STOP is not None and not _SPINNER_STOP.is_set():
        with _SPINNER_LOCK:
            message = _SPINNER_MESSAGE
        live_progress(f"{_SPINNER_FRAMES[frame_idx % len(_SPINNER_FRAMES)]} {message}")
        frame_idx += 1
        time.sleep(0.12)


def live_progress(message, level="INFO"):
    """Render a same-line progress message in TTY, fall back to normal log otherwise."""
    global _LIVE_PROGRESS_ACTIVE, _LIVE_PROGRESS_WIDTH
    line = message
    if not _is_tty():
        log(message, level)
        return
    pad_width = max(_LIVE_PROGRESS_WIDTH - len(line), 0)
    sys.stdout.write("\033[2K\r" + line + (" " * pad_width))
    sys.stdout.flush()
    _LIVE_PROGRESS_ACTIVE = True
    _LIVE_PROGRESS_WIDTH = max(_LIVE_PROGRESS_WIDTH, len(line))


def start_live_spinner(message):
    global _SPINNER_THREAD, _SPINNER_STOP, _SPINNER_MESSAGE
    if not _is_tty():
        live_progress(message)
        return
    if _SPINNER_THREAD is not None and _SPINNER_STOP is not None and not _SPINNER_STOP.is_set():
        update_live_spinner(message)
        return
    _SPINNER_MESSAGE = message
    _SPINNER_STOP = threading.Event()
    _SPINNER_THREAD = threading.Thread(target=_spinner_loop, daemon=True)
    _SPINNER_THREAD.start()


def update_live_spinner(message):
    global _SPINNER_MESSAGE
    if _SPINNER_THREAD is None:
        start_live_spinner(message)
        return
    with _SPINNER_LOCK:
        _SPINNER_MESSAGE = message


def stop_live_spinner(final_message=None, level="INFO"):
    global _SPINNER_THREAD, _SPINNER_STOP
    if _SPINNER_STOP is not None:
        _SPINNER_STOP.set()
    if _SPINNER_THREAD is not None:
        _SPINNER_THREAD.join(timeout=0.3)
    _SPINNER_THREAD = None
    _SPINNER_STOP = None
    if final_message is None:
        finish_live_progress()
    else:
        finish_live_progress(final_message, level)


def finish_live_progress(message=None, level="INFO"):
    """Finish the active same-line progress message, optionally with a final line."""
    if message is None:
        clear_live_progress()
        return
    if _is_tty():
        line = _render_line(message, level)
        sys.stdout.write("\033[2K\r" + line + "\n")
        sys.stdout.flush()
        global _LIVE_PROGRESS_ACTIVE, _LIVE_PROGRESS_WIDTH
        _LIVE_PROGRESS_ACTIVE = False
        _LIVE_PROGRESS_WIDTH = 0
        return
    log(message, level)


def log(message, level="INFO"):
    """Print a timestamped log line."""
    if _LIVE_PROGRESS_ACTIVE:
        clear_live_progress()
    print(_render_line(message, level))
