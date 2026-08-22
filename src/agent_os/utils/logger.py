# -*- coding: utf-8 -*-
"""分级控制台日志。

级别：EVENT（文件读写事件，演示时必须可见）/ TASK（任务派发与完成）/
      STATUS（状态流转）/ INFO / ERROR / WARN
支持测试期静默与日志捕获钩子。
"""
import os
import sys
import threading
from datetime import datetime

EVENT = "EVENT"
TASK = "TASK"
STATUS = "STATUS"
INFO = "INFO"
ERROR = "ERROR"
WARN = "WARN"

_quiet = False
_hooks: list = []
_lock = threading.Lock()
_LEVELS = {EVENT: 0, TASK: 1, STATUS: 2, INFO: 3, WARN: 4, ERROR: 5}


def set_quiet(q: bool) -> None:
    global _quiet
    _quiet = q


def add_hook(fn) -> None:
    """测试用：fn(level, message) 捕获每条日志。"""
    _hooks.append(fn)


def clear_hooks() -> None:
    _hooks.clear()


def _log_file() -> str | None:
    """跨进程日志文件（环境变量 AGENT_OS_LOG_FILE，测试/CLI 用）。"""
    return os.environ.get("AGENT_OS_LOG_FILE") or None


def log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] {msg}"
    with _lock:
        for fn in _hooks:
            try:
                fn(level, msg)
            except Exception:
                pass
        fpath = _log_file()
        if fpath:
            try:
                with open(fpath, "ab") as f:
                    f.write((line + "\n").encode("utf-8"))  # 单次 write，NTFS 原子性最好
            except OSError:
                pass
        if _quiet and level not in (ERROR, WARN):
            return
        print(line, flush=True)


def event(msg: str) -> None:
    log(EVENT, msg)


def task(msg: str) -> None:
    log(TASK, msg)


def status(msg: str) -> None:
    log(STATUS, msg)


def info(msg: str) -> None:
    log(INFO, msg)


def warn(msg: str) -> None:
    log(WARN, msg)


def error(msg: str) -> None:
    log(ERROR, msg)
