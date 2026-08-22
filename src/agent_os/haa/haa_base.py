# -*- coding: utf-8 -*-
"""HAA 通用运行时（L3 伪 Agent）。

对外与 Agent 完全同构：读 task/inbox.jsonl → 执行 → 写 status/。
HAA 是持久进程（跨任务存在），由 OS 层供给与守护。
"""
import os
import threading
import time

from .. import constants as C
from ..os_layer import provisioner
from ..utils import jsonio, logger, watchers


class HAA:
    """子类实现 execute(task) -> dict 即可。"""

    haa_name: str = "haa"
    gene: str = ""

    def __init__(self, sandbox_path: str):
        self.path = os.path.abspath(sandbox_path)
        self.inbox = os.path.join(self.path, C.TASK_INBOX)
        self._done: set[str] = set()      # 已处理 task_id
        self._events: list = []
        self._ev = threading.Event()
        self._stop = threading.Event()

    # ---------- 事件 ----------
    def _on_file_event(self, path: str, etype: str):
        logger.event(f"{self.haa_name}: 文件事件 {os.path.basename(path)} [{etype}]")
        self._events.append((path, etype))
        self._ev.set()

    # ---------- 主循环 ----------
    def run(self) -> None:
        w = watchers.FileWatcher(self._on_file_event, debounce_ms=150)
        w.watch_file(self.inbox)
        w.start()
        provisioner.write_state(self.path, C.IDLE)
        logger.status(f"{self.haa_name}: 启动，等待任务 (gene={self.gene})")
        try:
            while not self._stop.is_set():
                self._ev.wait(timeout=0.2)
                self._ev.clear()
                self._drain_tasks()
        finally:
            w.stop()

    def stop(self) -> None:
        self._stop.set()
        self._ev.set()

    def _drain_tasks(self) -> None:
        for task in jsonio.read_jsonl(self.inbox):
            tid = task.get("task_id")
            if tid in self._done:
                continue
            self._done.add(tid)
            self._process(task)

    # ---------- 执行 ----------
    def _process(self, task: dict) -> None:
        tid = task.get("task_id", "?")
        desc = task.get("description", "")
        logger.task(f"{self.haa_name}: 收到任务 {tid} - {desc}")
        provisioner.write_state(self.path, C.RUNNING)
        provisioner.write_current_task(self.path, desc)
        logger.status(f"{self.haa_name}: idle → running")
        try:
            result = self.execute(task)
            provisioner.write_output(self.path, {"task_id": tid, "result": result})
            provisioner.write_state(self.path, C.COMPLETED)
            logger.status(f"{self.haa_name}: running → completed (result={result})")
        except Exception as e:
            provisioner.write_output(self.path, {"task_id": tid, "error": str(e)})
            provisioner.write_state(self.path, C.ERROR)
            logger.error(f"{self.haa_name}: 执行失败 {e}")

    def execute(self, task: dict) -> dict:
        raise NotImplementedError


def haa_main(sandbox_path: str, haa_name: str) -> None:
    """OS spawn 的 HAA 进程入口。"""
    from .math_haa import MathHAA
    from .disk_haa import DiskHAA
    cls = {"math_haa": MathHAA, "disk_haa": DiskHAA}.get(haa_name)
    if cls is None:
        logger.error(f"未知 HAA: {haa_name}")
        return
    haa = cls(sandbox_path)
    haa.run()
