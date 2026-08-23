# -*- coding: utf-8 -*-
"""能力表（Skill Table）：本地声明 ∪ 所有直接子能力表并集（自下而上聚合）。

规格书 §5：能力表(agent) = 本地技能 ∪ (∪ 每个直接子 Agent 的能力表)。
聚合时把技能 next_hop 重指向声明它的子沙箱（父路由到子，子内部再路由）。
"""
import time

from .. import constants as C
from ..os_layer import provisioner
from ..schemas import make_skill
from ..utils import logger

# 基因 → 本地技能声明（叶子 Agent 从 genome 推导）
_GENE_SKILLS = {
    C.GENE_CPU_CALC: lambda lineage: make_skill(
        "math_eval", "Evaluate a basic arithmetic expression",
        next_hop=C.HAA_MATH, lineage_tag=lineage,
        required_genes=[C.GENE_CPU_CALC],
        input_schema={"expr": "string"}, output_schema={"value": "number"}),
    C.GENE_DISK_WRITE: lambda lineage: make_skill(
        "disk_write", "Write a string to a disk file",
        next_hop=C.HAA_DISK, lineage_tag=lineage,
        required_genes=[C.GENE_DISK_WRITE],
        input_schema={"content": "string"}, output_schema={"file": "string"}),
}


def declare_local_skills(genes: set[str], lineage_tag: str) -> list[dict]:
    """Agent 启动时按 genome 基因声明本地技能（写入自身 status/skills）。"""
    skills = []
    for gene in sorted(genes):
        if gene in _GENE_SKILLS:
            skills.append(_GENE_SKILLS[gene](lineage_tag))
    return skills


# ---------- 复合技能（规格书 §5：中间 Agent 把多个子技能编排为高级技能） ----------
COMPOSITE_SKILLS = {
    "calc_and_save": {
        "description": "Evaluate an arithmetic expression and save the result to disk "
                       "(orchestrates math_eval + disk_write)",
        "required_genes": [C.GENE_CPU_CALC, C.GENE_DISK_WRITE],
        "input_schema": {"expr": "string", "save": "bool"},
        "output_schema": {"value": "number", "file": "string"},
        "sub_skills": ["math_eval", "disk_write"],
    },
}


class SkillTable:
    def __init__(self, local_skills: list[dict]):
        self.local = list(local_skills)
        self._children: dict[str, list[dict]] = {}   # child_id → 该子声明的技能

    # ---------- 子能力表 ----------
    def add_child(self, child_id: str, child_path: str, wait_ready: float = 0.0) -> None:
        """登记子沙箱能力表；wait_ready>0 时轮询直到子声明就绪（防初始化竞态）。"""
        if wait_ready > 0:
            deadline = time.monotonic() + wait_ready
            while time.monotonic() < deadline:
                skills = provisioner.read_skills(child_path)
                if skills:
                    break
                time.sleep(0.05)
        self.refresh_child(child_id, child_path)

    def refresh_child(self, child_id: str, child_path: str) -> None:
        """重读子沙箱 status/skills（父监听子状态变化后调用）。"""
        skills = provisioner.read_skills(child_path)
        # 聚合：next_hop 重指向声明它的子沙箱
        self._children[child_id] = [
            {**s, "next_hop": child_id} for s in skills
        ]

    def remove_child(self, child_id: str) -> None:
        self._children.pop(child_id, None)

    # ---------- 能力覆盖（子树基因并集） ----------
    def covered_genes(self) -> set[str]:
        """本地 + 全部子树技能覆盖的基因集合（能力表聚合口径）。"""
        genes: set[str] = set()
        for s in self.local:
            genes |= set(s.get("required_genes", []))
        for skills in self._children.values():
            for s in skills:
                genes |= set(s.get("required_genes", []))
        return genes

    def compose_composites(self, sandbox_id: str, lineage_tag: str) -> list[dict]:
        """能力表聚合后编排复合技能：子树基因组合能覆盖该技能所需基因时才声明。

        复合技能是"编排说明"（next_hop=自己），代表本子树能完成该组合任务；
        路由命中复合技能后由本 Agent 分解为基因子任务执行。
        """
        have = self.covered_genes()
        out = []
        for name, spec in COMPOSITE_SKILLS.items():
            if set(spec["required_genes"]) <= have:
                out.append({
                    "skill_id": name,
                    "description": spec["description"],
                    "input_schema": spec["input_schema"],
                    "output_schema": spec["output_schema"],
                    "next_hop": sandbox_id,  # 由本 Agent（路由器）分解执行
                    "lineage_tag": lineage_tag,
                    "required_genes": list(spec["required_genes"]),
                    "composite": True,
                    "sub_skills": list(spec["sub_skills"]),
                })
        return out

    # ---------- 查询 ----------
    def find_skill(self, skill_id: str, sandbox_id: str = "", lineage_tag: str = "") -> dict | None:
        """按 skill_id 查能力表（本地优先，含动态编排的复合技能）。

        返回 {"skill":..., "via":..., "local": bool}；复合技能由本 Agent 分解执行。
        """
        for s in self.local:
            if s.get("skill_id") == skill_id:
                return {"skill": s, "via": s["next_hop"], "local": True}
        for s in self.compose_composites(sandbox_id, lineage_tag):
            if s.get("skill_id") == skill_id:
                return {"skill": s, "via": s["next_hop"], "local": True}
        for child_id, skills in self._children.items():
            for s in skills:
                if s.get("skill_id") == skill_id:
                    return {"skill": s, "via": child_id, "local": False}
        return None
    def find_for_gene(self, gene: str) -> dict | None:
        """返回提供该基因的技能条目 {"skill":..., "via":..., "local": bool}；本地优先。"""
        for s in self.local:
            if gene in s.get("required_genes", []):
                return {"skill": s, "via": s["next_hop"], "local": True}
        for child_id, skills in self._children.items():
            for s in skills:
                if gene in s.get("required_genes", []):
                    return {"skill": s, "via": child_id, "local": False}
        return None

    def all_skills(self) -> list[dict]:
        return [*self.local, *[s for sk in self._children.values() for s in sk]]

    def child_ids(self) -> list[str]:
        return list(self._children.keys())
