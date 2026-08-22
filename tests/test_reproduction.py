# -*- coding: utf-8 -*-
"""S9-3 能力表聚合（纯逻辑）与 S9-4 无性繁殖（真实子进程）。"""
import os
import time

from agent_os import constants as C
from agent_os.agent.skill_table import SkillTable, declare_local_skills
from agent_os.os_layer import provisioner
from agent_os.os_layer.supervisor import Supervisor, ROOT_ID


# ---------- 能力表聚合 ----------
def test_local_skills_from_genome():
    skills = declare_local_skills({C.GENE_CPU_CALC}, "lineage_l1")
    assert len(skills) == 1
    assert skills[0]["skill_id"] == "math_eval"
    assert skills[0]["next_hop"] == C.HAA_MATH
    assert skills[0]["required_genes"] == [C.GENE_CPU_CALC]
    skills2 = declare_local_skills({C.GENE_CPU_CALC, C.GENE_DISK_WRITE}, "l2")
    assert {s["skill_id"] for s in skills2} == {"math_eval", "disk_write"}
    assert declare_local_skills(set(), "l3") == []


def test_aggregation_local_union_children(tmp_path):
    table = SkillTable(declare_local_skills({C.GENE_CPU_CALC}, "l1"))
    # 子沙箱声明 disk_write 能力
    child_path = provisioner.create_sandbox(str(tmp_path / "sb_c"), "sb_c",
                                            C.ROLE_AGENT, "root", "l2",
                                            ["disk_haa"])
    provisioner.write_skills(child_path, declare_local_skills({C.GENE_DISK_WRITE}, "l2"))
    table.add_child("sb_c", child_path)

    # 本地优先：cpu_calc 走本地
    hit = table.find_for_gene(C.GENE_CPU_CALC)
    assert hit["local"] is True and hit["via"] == C.HAA_MATH
    # 子树能力：disk_write 走子
    hit2 = table.find_for_gene(C.GENE_DISK_WRITE)
    assert hit2["local"] is False and hit2["via"] == "sb_c"
    # 并集可见
    ids = {s["skill_id"] for s in table.all_skills()}
    assert ids == {"math_eval", "disk_write"}


def test_aggregation_refresh_and_remove(tmp_path):
    table = SkillTable([])
    child_path = provisioner.create_sandbox(str(tmp_path / "sb_c"), "sb_c",
                                            C.ROLE_AGENT, "root", "l2", ["disk_haa"])
    provisioner.write_skills(child_path, declare_local_skills({C.GENE_DISK_WRITE}, "l2"))
    table.add_child("sb_c", child_path)
    assert table.find_for_gene(C.GENE_DISK_WRITE) is not None
    # 子能力消失 → refresh 后聚合结果同步消失
    provisioner.write_skills(child_path, [])
    table.refresh_child("sb_c", child_path)
    assert table.find_for_gene(C.GENE_DISK_WRITE) is None
    # remove
    table.add_child("sb_c", child_path)
    table.remove_child("sb_c")
    assert table.find_for_gene(C.GENE_DISK_WRITE) is None


# ---------- 无性繁殖（真实 OS + 子进程） ----------
def test_reproduction_provisions_child_with_genes(sandbox_root, logs):
    sup = Supervisor(sandbox_root)
    sup.start()
    try:
        # 直接调 OS 供给 API（文件通道由 e2e 覆盖；这里验证繁殖语义）
        rep = sup.provision(parent_id=ROOT_ID, role=C.ROLE_AGENT,
                            genes=[C.GENE_CPU_CALC])
        cid = rep["sandbox_id"]
        # 等子进程就绪并声明能力
        entry = sup.registry[cid]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if provisioner.read_skills(entry["path"]):
                break
            time.sleep(0.1)
        skills = provisioner.read_skills(entry["path"])
        assert len(skills) == 1 and skills[0]["skill_id"] == "math_eval"

        # 基因/谱系契约
        genome = provisioner.read_genome(entry["path"])
        assert genome["haa_identifiers"] == ["math_haa"]
        assert genome["lineage_tag"].startswith("lineage_")
        # 路径嵌套在父 children/ 下
        assert os.path.basename(os.path.dirname(entry["path"])) == C.CHILDREN_DIR

        # 注册表含 interface + 2 HAA + root + child
        assert set(sup.registry) >= {ROOT_ID, C.HAA_MATH, C.HAA_DISK, cid}
        # ACL：root 可写 child 的 task/，child 不可写自身 task/
        task_path = os.path.join(entry["path"], C.TASK_INBOX)
        assert sup.acl_allowed(ROOT_ID, cid, task_path)
        assert not sup.acl_allowed(cid, cid, task_path)
    finally:
        sup.stop()
