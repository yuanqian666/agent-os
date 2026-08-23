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
from .reproduction import GeneNotOwned, Reproducer
from .skill_table import COMPOSITE_SKILLS, SkillTable, declare_local_skills
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
        # 技能 = 基因组合的编排说明，只能由基因持有者声明后向上聚合。
        # Root 持有全部基因（来源）但**不声明技能**：系统启动时不带任何技能，
        # 必须由 Root 分析任务 → 调配基因（繁殖族系子）→ 子声明 → 聚合建立。
        declared = set() if self.role == C.ROLE_ROOT else self.genes
        self.table = SkillTable(declare_local_skills(declared, self.lineage))
        self.reproducer = Reproducer(self.sandbox_id, self.path,
                                     self.sandbox_root, self.os,
                                     self.table, self.children, self.genes)

        self._processed: set[str] = set()
        self._steps: list[dict] = []      # 调度蓝图 DAG：本次任务的派发步骤
        self._help_path: list[str] = []   # help_request 上抛路径（防回环）
        self._loop = TaskLoop(self._on_event)
        self._stop = threading.Event()

    # ================= 事件 =================
    def _on_event(self, path: str, etype: str) -> None:
        logger.event(f"{self.sandbox_id}: 文件事件 {os.path.relpath(path, self.path)} [{etype}]")
        self._loop.push(path, etype)

    # ================= 主循环 =================
    def run(self) -> None:
        # 声明本地能力（status/skills 供父聚合）
        self._publish_skills()
        self._loop.watch_inbox(self.path)
        self._loop.watch_children(self.path)
        for cid, cpath in self._scan_children():
            self.table.add_child(cid, cpath)
            self._loop.watch_child_status(cpath)
        self._publish_skills()
        try:
            self._loop.start()
        except Exception as e:
            logger.error(f"{self.sandbox_id}: watcher 启动失败 → 进程退出 {e}")
            provisioner.write_output(self.path, {"task_id": "?",
                                                 "error": f"watcher 启动失败: {e}"})
            provisioner.write_state(self.path, C.ERROR)
            return
        provisioner.write_state(self.path, C.IDLE)
        logger.status(f"{self.sandbox_id} ({self.role}, lineage={self.lineage}, genes={sorted(self.genes)}): 启动")
        try:
            self._drain_inbox()  # 启动排空：防 watcher 启动前已写入的任务漏处理
            while not self._stop.is_set():
                events = self._loop.pop_all(timeout=0.2)
                for path, etype in events:
                    self._handle_event(path)
                # 注意：不做无条件周期 drain——残留进程（watcher 句柄已失效）
                # 会因此抢读新任务导致并发；事件丢失由启动排空 + 事件驱动覆盖
        except Exception as e:
            logger.error(f"{self.sandbox_id}: 主循环异常退出 {e}")
            provisioner.write_state(self.path, C.ERROR)
        finally:
            self._loop.stop()

    def _publish_skills(self) -> None:
        """发布能力说明（status/skills 供父聚合）：本地技能 + 聚合后编排的复合技能。"""
        skills = [*self.table.local,
                  *self.table.compose_composites(self.sandbox_id, self.lineage)]
        provisioner.write_skills(self.path, skills)

    def _on_skills_changed(self, cid: str) -> None:
        """子能力表更新后：刷新聚合、重新编排复合技能、重新发布（技能说明向上传播）。"""
        n = len(self.table._children.get(cid, []))
        logger.task(f"{self.sandbox_id} [路由器]: 聚合子能力表 ← {cid} ({n} 项技能说明)")
        self._publish_skills()
        composites = self.table.compose_composites(self.sandbox_id, self.lineage)
        if composites:
            logger.task(f"{self.sandbox_id} [路由器]: 能力覆盖 {sorted(self.table.covered_genes())} "
                        f"→ 声明复合技能 {[c['skill_id'] for c in composites]}")

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
                    self._on_skills_changed(cid)
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
                                    final_output={"task_id": tid, "result": result,
                                                  "steps": self._steps})
        except GeneNotOwned as e:
            # 控制流第一定律：跨域缺基因 → help_request 沿族系向上抛回，
            # 由同时拥有所需基因的祖先节点接管编排（不横向杂交）
            logger.task(f"{self.sandbox_id}: 基因 {e.gene} 不可达 → help_request 沿族系上抛")
            reqs = provisioner.read_help_requests(self.path)
            reqs.append({"task_id": tid, "gene": e.gene,
                         "parameters": task.get("parameters", {}),
                         "path": [*self._help_path, self.sandbox_id]})
            provisioner.write_help_requests(self.path, reqs)
            provisioner.write_output(self.path, {"task_id": tid,
                                                 "error": f"缺基因 {e.gene} 已上抛"})
            provisioner.write_state(self.path, C.ERROR)
        except Exception as e:
            logger.error(f"{self.sandbox_id}: 任务 {tid} 失败: {e}")
            provisioner.write_output(self.path, {"task_id": tid, "error": str(e)})
            provisioner.write_state(self.path, C.ERROR)

    # ================= 解析与路由 =================
    def _resolve(self, task: dict) -> dict:
        params = dict(task.get("parameters", {}))
        desc = task.get("description", "")

        # 分析任务（规格书 §8）：复杂任务声明技能需求（skill=基因组合的编排说明）
        requested = params.get("skills") or []
        for sid in requested:
            entry = self.table.find_skill(sid, self.sandbox_id, self.lineage)
            if entry:
                logger.task(f"{self.sandbox_id} [路由器]: 命中已建立能力 {sid} "
                            f"→ 路由/分解执行")
            else:
                genes_hint = COMPOSITE_SKILLS.get(sid, {}).get("required_genes")
                logger.task(f"{self.sandbox_id} [路由器]: 分析任务：需要复合技能 {sid} "
                            f"(能力未建立，基因组合需求 {genes_hint}) → 调配基因")

        genes = router.required_genes(params, desc)
        if not genes:
            raise ValueError(f"无法从参数推断需求基因: {params}")
        logger.task(f"{self.sandbox_id} [路由器]: 分析完成 → 需求基因 {sorted(genes)}")
        if not genes:
            raise ValueError(f"无法从参数推断需求基因: {params}")
        results: dict[str, dict] = {}
        # 按基因顺序处理：Math 结果注入 Disk 子任务（Root 路由结果给 Child B）
        for gene in sorted(genes):
            via, path, is_local = self.reproducer.ensure_gene(gene)
            self._refresh_capabilities()  # 能力表变化后重新编排/发布复合技能
            # Math 结果注入 Disk 子任务（Root 路由结果给 Child B）
            if gene == C.GENE_DISK_WRITE and "content" not in params:
                math_val = router.aggregate_by_gene(results, C.GENE_CPU_CALC).get("value")
                if math_val is not None:
                    params["content"] = str(math_val)
            subtask = router.build_subtask(task, gene, params, leaf=is_local)
            self._steps.append({"task": subtask["task_id"], "gene": gene,
                                "via": os.path.basename(path),
                                "step": len(self._steps) + 1})
            out = router.delegate(subtask, path, timeout=self.timeout)
            if "help_requests" in out:
                # 祖先节点接管（MapReduce 编排）：对上抛的每个基因，
                # 本祖先（拥有该基因）调配分支并派发，结果并入汇总
                logger.task(f"{self.sandbox_id} [祖先编排]: 接管 help_requests "
                            f"{len(out['help_requests'])} 项，按基因调配派发")
                for hr in out["help_requests"]:
                    g = hr["gene"]
                    v2, p2, local2 = self.reproducer.ensure_gene(g)
                    self._refresh_capabilities()
                    sub2 = router.build_subtask(
                        {**task, "task_id": hr.get("task_id") or tid},
                        g, hr.get("parameters") or params, leaf=local2)
                    self._steps.append({"task": sub2["task_id"], "gene": g,
                                        "via": os.path.basename(p2),
                                        "step": len(self._steps) + 1,
                                        "origin": "ancestor-orchestrated"})
                    res2 = router.delegate(sub2, p2, timeout=self.timeout)
                    if "help_requests" in res2:
                        raise RuntimeError("上抛未被祖先解决（基因缺失链）")
                    results[g] = res2
            else:
                results[gene] = out
        return self._aggregate(results)

    def _refresh_capabilities(self) -> None:
        """能力表变化后：重新编排复合技能并发布（技能说明自下而上聚合传播）。"""
        self._publish_skills()
        composites = self.table.compose_composites(self.sandbox_id, self.lineage)
        if composites:
            logger.task(f"{self.sandbox_id} [路由器]: 能力覆盖 "
                        f"{sorted(self.table.covered_genes())} "
                        f"→ 声明复合技能 {[c['skill_id'] for c in composites]}")

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
    """OS spawn 的 Agent/Root 进程入口（全局异常防护，绝不静默退出）。"""
    try:
        rt = AgentRuntime(sandbox_path)
        rt.run()
    except Exception as e:
        logger.error(f"runtime_main 异常退出 {os.path.basename(sandbox_path)}: {e}")
        try:
            provisioner.write_output(sandbox_path, {"task_id": "?", "error": str(e)})
            provisioner.write_state(sandbox_path, C.ERROR)
        except Exception:
            pass
