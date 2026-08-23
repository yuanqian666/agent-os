# -*- coding: utf-8 -*-
"""路由与任务派发：基因需求判定、子任务构造、下发与结果聚合。

确定性 mock 逻辑（规格书 §8：无 LLM，用规则证明路由成立）：
- 需求基因判定：parameters 含 expr → cpu_calc；含 content/save → disk_write
- 子任务构造：父→子带 -m/-d 后缀；子→HAA 复用同一 task_id（不同 inbox 无冲突）
- 下发：追加写子 task/inbox.jsonl，轮询子 status/state 直至 completed/error
"""
import os
import time

from .. import constants as C
from ..os_layer import provisioner
from ..schemas import make_task
from ..utils import jsonio, logger

_SUFFIX = {C.GENE_CPU_CALC: "m", C.GENE_DISK_WRITE: "d",
           C.GENE_FILE_READ: "r", C.GENE_LOG_WRITE: "l"}


def required_genes(parameters: dict, description: str = "") -> set[str]:
    """按任务参数推断所需基因（MVP 确定性 mock 逻辑）。"""
    genes: set[str] = set()
    if isinstance(parameters, dict):
        if parameters.get("_gene") in C.ALL_GENES:
            return {parameters["_gene"]}  # 子任务显式基因线索（消歧 content 等泛用参数）
        if "expr" in parameters:
            genes.add(C.GENE_CPU_CALC)
        if parameters.get("save") or "content" in parameters:
            genes.add(C.GENE_DISK_WRITE)
        if parameters.get("read") or parameters.get("path"):
            genes.add(C.GENE_FILE_READ)
        if parameters.get("log"):
            genes.add(C.GENE_LOG_WRITE)
        # 显式声明的复合技能（skill=基因组合）展开为其基因集合
        from .skill_table import COMPOSITE_SKILLS
        for sid in parameters.get("skills") or []:
            spec = COMPOSITE_SKILLS.get(sid)
            if spec:
                genes |= set(spec["required_genes"])
    return genes


# 依赖顺序：链式任务按此序执行（前序结果注入后续参数）
GENE_ORDER = (C.GENE_FILE_READ, C.GENE_CPU_CALC, C.GENE_DISK_WRITE, C.GENE_LOG_WRITE)


def build_subtask(parent_task: dict, gene: str, params: dict, leaf: bool = False) -> dict:
    """构造子任务：父→子带后缀；子→HAA 复用 task_id（leaf=True）。"""
    tid = parent_task.get("task_id", "?")
    if gene == C.GENE_CPU_CALC:
        return make_task(
            tid if leaf else f"{tid}-{_SUFFIX[gene]}",
            f"计算 {params.get('expr')}",
            {"_gene": gene, "expr": params.get("expr")})
    if gene == C.GENE_DISK_WRITE:
        return make_task(
            tid if leaf else f"{tid}-{_SUFFIX[gene]}",
            f"写盘 {params.get('content')}",
            {"_gene": gene, "content": params.get("content")})
    if gene == C.GENE_FILE_READ:
        rel = params.get("path") or params.get("read")
        return make_task(
            tid if leaf else f"{tid}-{_SUFFIX[gene]}",
            f"读取文件 {rel}",
            {"_gene": gene, "path": rel})
    if gene == C.GENE_LOG_WRITE:
        return make_task(
            tid if leaf else f"{tid}-{_SUFFIX[gene]}",
            f"记录日志 {params.get('content')}",
            {"_gene": gene, "content": params.get("content")})
    raise ValueError(f"未知基因 {gene}")


def delegate(task: dict, child_path: str, timeout: float = 30.0) -> dict:
    """下发任务到子沙箱并阻塞等待其完成，返回子 status/output。

    两相握手：仅当 output.task_id 与下发任务一致且 state==completed 才返回
    （防读取上一任务的陈旧 completed/output）。
    子 state==error 且 help_requests 非空 → 视为"沿族系上抛"，返回
    {"help_requests": [...], "task_id": ...} 由父（祖先节点）接管编排。
    """
    inbox = os.path.join(child_path, C.TASK_INBOX)
    jsonio.append_jsonl(inbox, task)
    tid = task.get("task_id")
    logger.task(f"下发任务 {tid} → {os.path.basename(child_path)}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = provisioner.read_state(child_path)
        out = provisioner.read_output(child_path)
        if state == C.COMPLETED and out.get("task_id") == tid:
            logger.task(f"子沙箱完成 {os.path.basename(child_path)}: {out}")
            return out
        if state == C.ERROR and out.get("task_id") == tid:
            reqs = provisioner.read_help_requests(child_path)
            if reqs:
                logger.task(f"子沙箱上抛帮助请求 {os.path.basename(child_path)}: "
                            f"{[r.get('gene') for r in reqs]} → 祖先接管")
                return {"help_requests": reqs, "task_id": tid,
                        "error": out.get("error")}
            raise RuntimeError(f"子沙箱执行失败: {out}")
        time.sleep(0.05)
    raise TimeoutError(f"等待子沙箱 {child_path} 完成超时")


def aggregate_by_gene(results: dict[str, dict], gene: str) -> dict:
    """从子输出中提取指定基因的结果对象。"""
    out = results.get(gene) or {}
    return out.get("result") or {}
