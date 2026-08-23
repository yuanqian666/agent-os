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
        # HAA 不是技能而是权限令牌（机制与策略分离）：声明基因-物理机制映射，
        # 供上层识别"经此可达的物理能力"；策略/技能属于 LLM Agent 侧
        token_meta = {"math_haa": "算术求值机制", "disk_haa": "磁盘写入机制"}
        genome = provisioner.read_genome(self.path)
        lineage = genome.get("lineage_tag", f"lineage_{self.haa_name}")
        provisioner.write_skills(self.path, [{
            "skill_id": f"token_{self.gene}",
            "description": f"物理机制：{token_meta.get(self.haa_name, self.haa_name)}",
            "input_schema": {}, "output_schema": {},
            "next_hop": self.haa_name, "lineage_tag": lineage,
            "required_genes": [self.gene],
            "token": True,  # 权限令牌声明（非可编排技能）
        }])
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
    """OS spawn 的 HAA 进程入口（全局异常防护，绝不静默退出）。"""
    try:
        from .math_haa import MathHAA
        from .disk_haa import DiskHAA
        from .file_read_haa import FileReadHAA
        from .log_haa import LogHAA
        cls = {"math_haa": MathHAA, "disk_haa": DiskHAA,
               "file_read_haa": FileReadHAA, "log_haa": LogHAA}.get(haa_name)
        if cls is None:
            logger.error(f"未知 HAA: {haa_name}")
            return
        haa = cls(sandbox_path)
        haa.run()
    except Exception as e:
        logger.error(f"haa_main 异常退出 {haa_name}: {e}")
        try:
            provisioner.write_output(sandbox_path, {"task_id": "?", "error": str(e)})
            provisioner.write_state(sandbox_path, C.ERROR)
        except Exception:
            pass
