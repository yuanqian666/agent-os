# -*- coding: utf-8 -*-
"""task_loop：watchdog 事件循环装配。

监听自身 task/inbox.jsonl 与各子 status/（含 skills 变更），
把事件压入线程安全队列，主循环消费。
"""
import os
import threading

from .. import constants as C
from ..utils import watchers


class TaskLoop:
    def __init__(self, on_event, debounce_ms: int = 150):
        self._w = watchers.FileWatcher(on_event, debounce_ms=debounce_ms)
        self._queue: list[tuple[str, str]] = []
        self._ev = threading.Event()

    # ---- 注册监听 ----
    def watch_inbox(self, sandbox_path: str) -> None:
        self._w.watch_file(os.path.join(sandbox_path, C.TASK_INBOX))

    def watch_children(self, sandbox_path: str) -> None:
        """监听 children/ 目录（发现新子沙箱）与已登记子的 status/。"""
        children_dir = os.path.join(sandbox_path, C.CHILDREN_DIR)
        self._w.watch_dir(children_dir)
        for child in os.listdir(children_dir):
            cpath = os.path.join(children_dir, child)
            if os.path.isdir(cpath):
                self._w.watch_dir(os.path.join(cpath, C.STATUS_DIR))

    def watch_child_status(self, child_path: str) -> None:
        self._w.watch_dir(os.path.join(child_path, C.STATUS_DIR))

    # ---- 事件 ----
    def push(self, path: str, etype: str) -> None:
        self._queue.append((path, etype))
        self._ev.set()

    def pop_all(self, timeout: float = 0.2) -> list[tuple[str, str]]:
        if not self._queue:
            self._ev.wait(timeout=timeout)
        self._ev.clear()
        out, self._queue = self._queue, []
        return out

    # ---- 生命周期 ----
    def start(self) -> None:
        self._w.start()

    def stop(self) -> None:
        self._w.stop()
