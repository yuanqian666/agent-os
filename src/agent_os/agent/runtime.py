# -*- coding: utf-8 -*-
"""通用递归 Agent 运行时（L2）——全树复用同一实现。

状态机（§2.4）：
  监听自身 task/inbox.jsonl 与各子 status/
  ├─ 收到任务 → running → 解析需求基因 → 路由/繁殖 → 聚合 → completed
  ├─ 子 skills 变更 → 刷新能力表（自下而上聚合）
  └─ Root 完成 → report_done → OS teardown 整树
"""
import os
import threading
import time

from .. import constants as C
from ..os_layer import provisioner
from ..os_layer.client import OSClient
from ..utils import logger
from . import router
from .reproduction import Reproducer
from .skill_table import SkillTable, declare_local_skills
from .task_loop import TaskLoop

DEFAULT_TIMEOUT = 30.0


def find_sandbox_root(sandbox_path: str) -> str:
    """从沙箱路径向上找包含 os/ 控制区的沙箱根。"""
    p = os.path.abspath(sandbox_path)
    while True:
        if os.path.isdir(os.path.join(p, "os")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            raise RuntimeError(f"找不到沙箱根（缺少 os/ 控制区）: {sandbox_path}")
        p = parent


class AgentRuntime:
    def __init__(self, sandbox_path: str, timeout: float = DEFAULT_TIMEOUT):
        self.path = os.path.abspath(sandbox_path)
        self.manifest = provisioner.read_manifest(self.path)
        self.sandbox_id = self.manifest["sandbox_id"]
        self.role = self.manifest["role"]
        self.parent_id = self.manifest.get("parent_id")
        self.genome = provisioner.read_genome(self.path)
        self.lineage = self.genome.get("lineage_tag", "?")
        self.sandbox_root = find_sandbox_root(self.path)
        self.timeout = timeout

        self.genes = {C.HAA_GENE_MAP.get(h)
                      for h in self.genome.get("haa_identifiers", [])}
        self.genes.discard(None)

        self.os = OSClient(self.sandbox_root, self.sandbox_id, timeout_s=timeout)
        self.children: dict[str, str] = {}
        # Root 拥有全部基因但**不声明本地技能**（纯协调者，执行下放）；
        # 中间 Agent 按 genome 基因声明技能（规格书 §5：能力自下而上聚合）
        declared = set() if self.role == C.ROLE_ROOT else self.genes
        self.table = SkillTable(declare_local_skills(declared, self.lineage))
        self.reproducer = Reproducer(self.sandbox_id, self.path,
                                     self.sandbox_root, self.os,
                                     self.table, self.children)

        self._processed: set[str] = set()
        self._loop = TaskLoop(self._on_event)
        self._stop = threading.Event()

    # ================= 事件 =================
    def _on_event(self, path: str, etype: str) -> None:
        logger.event(f"{self.sandbox_id}: 文件事件 {os.path.relpath(path, self.path)} [{etype}]")
        self._loop.push(path, etype)

    # ================= 主循环 =================
    def run(self) -> None:
        # 声明本地能力（status/skills 供父聚合）
        provisioner.write_skills(self.path, self.table.local)
        self._loop.watch_inbox(self.path)
        self._loop.watch_children(self.path)
        for cid, cpath in self._scan_children():
            self.table.add_child(cid, cpath)
            self._loop.watch_child_status(cpath)
        self._loop.start()
        provisioner.write_state(self.path, C.IDLE)
        logger.status(f"{self.sandbox_id} ({self.role}, lineage={self.lineage}, genes={sorted(self.genes)}): 启动")
        try:
            self._drain_inbox()  # 排空启动前已写入的任务（防漏事件）
            while not self._stop.is_set():
                events = self._loop.pop_all(timeout=0.2)
                for path, etype in events:
                    self._handle_event(path)
                self._drain_inbox()  # 周期性兜底排空（watchdog 事件可能丢失）
        finally:
            self._loop.stop()

    def stop(self) -> None:
        self._stop.set()

    def _scan_children(self) -> list[tuple[str, str]]:
        """发现已存在的子沙箱（重连场景）。"""
        out = []
        cdir = os.path.join(self.path, C.CHILDREN_DIR)
        if not os.path.isdir(cdir):
            return out
        for name in os.listdir(cdir):
            cpath = os.path.join(cdir, name)
            if os.path.isdir(cpath):
                out.append((name, cpath))
        return out

    def _handle_event(self, path: str) -> None:
        # 自身 inbox 有新任务
        if os.path.basename(path) == "inbox.jsonl" and \
                os.path.dirname(path) == os.path.join(self.path, C.TASK_DIR):
            self._drain_inbox()
            return
        # children/ 下出现新子沙箱 → 登记并监听其 status/
        cdir = os.path.join(self.path, C.CHILDREN_DIR)
        if os.path.dirname(path) == cdir and os.path.isdir(path):
            cid = os.path.basename(path)
            if cid not in self.children:
                self.children[cid] = path
                self.table.add_child(cid, path)
                self._loop.watch_child_status(path)
            return
        # 子 status/skills 变更 → 刷新能力表（自下而上聚合）
        for cid, cpath in list(self.children.items()):
            if os.path.dirname(path) == os.path.join(cpath, C.STATUS_DIR):
                if os.path.basename(path) == "skills":
                    self.table.refresh_child(cid, cpath)
                    logger.task(f"{self.sandbox_id}: 聚合子能力表 ← {cid} "
                                f"({len(self.table._children.get(cid, []))} 项)")
                return

    # ================= 任务处理 =================
    def _drain_inbox(self) -> None:
        for task in provisioner_jsonl(self.path):
            tid = task.get("task_id")
            if tid in self._processed:
                continue
            self._processed.add(tid)
            self.handle_task(task)

    def handle_task(self, task: dict) -> None:
        tid = task.get("task_id", "?")
        desc = task.get("description", "")
        logger.task(f"{self.sandbox_id}: 收到任务 {tid} - {desc}")
        provisioner.write_state(self.path, C.RUNNING)
        provisioner.write_current_task(self.path, desc)
        logger.status(f"{self.sandbox_id}: idle → running")
        try:
            result = self._resolve(task)
            provisioner.write_output(self.path, {"task_id": tid, "result": result})
            provisioner.write_state(self.path, C.COMPLETED)
            logger.status(f"{self.sandbox_id}: running → completed")
            if self.role == C.ROLE_ROOT:
                logger.task(f"{self.sandbox_id}: 任务完成 → 上报 OS 销毁 Agent 树")
                self.os.report_done(self.sandbox_id,
                                    final_output={"task_id": tid, "result": result})
        except Exception as e:
            logger.error(f"{self.sandbox_id}: 任务 {tid} 失败: {e}")
            provisioner.write_output(self.path, {"task_id": tid, "error": str(e)})
            provisioner.write_state(self.path, C.ERROR)

    # ================= 解析与路由 =================
    def _resolve(self, task: dict) -> dict:
        params = dict(task.get("parameters", {}))
        genes = router.required_genes(params, task.get("description", ""))
        if not genes:
            raise ValueError(f"无法从参数推断需求基因: {params}")
        results: dict[str, dict] = {}
        # 按基因顺序处理：Math 结果注入 Disk 子任务（Root 路由结果给 Child B）
        for gene in sorted(genes):
            via, path, is_local = self.reproducer.ensure_gene(gene)
            # Math 结果注入 Disk 子任务（Root 路由结果给 Child B）
            if gene == C.GENE_DISK_WRITE and "content" not in params:
                math_val = router.aggregate_by_gene(results, C.GENE_CPU_CALC).get("value")
                if math_val is not None:
                    params["content"] = str(math_val)
            subtask = router.build_subtask(task, gene, params, leaf=is_local)
            out = router.delegate(subtask, path, timeout=self.timeout)
            results[gene] = out
        return self._aggregate(results)

    def _aggregate(self, results: dict[str, dict]) -> dict:
        """聚合子结果（MVP 演示口径）。"""
        math = router.aggregate_by_gene(results, C.GENE_CPU_CALC)
        disk = router.aggregate_by_gene(results, C.GENE_DISK_WRITE)
        return {
            "value": math.get("value"),
            "file": disk.get("file"),
            "content": disk.get("content"),
        }


def provisioner_jsonl(sandbox_path: str) -> list[dict]:
    from ..utils import jsonio
    return jsonio.read_jsonl(os.path.join(sandbox_path, C.TASK_INBOX))


def runtime_main(sandbox_path: str) -> None:
    """OS spawn 的 Agent/Root 进程入口。"""
    rt = AgentRuntime(sandbox_path)
    rt.run()
