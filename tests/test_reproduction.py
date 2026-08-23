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


def test_composite_skill_orchestration(tmp_path):
    """复合技能（skill=基因组合）：子树基因覆盖完整组合时才编排声明。"""
    table = SkillTable([])
    # 只有 math 能力 → 无法编排 calc_and_save
    m_path = provisioner.create_sandbox(str(tmp_path / "sb_m"), "sb_m",
                                        C.ROLE_AGENT, "root", "l1", ["math_haa"])
    provisioner.write_skills(m_path, declare_local_skills({C.GENE_CPU_CALC}, "l1"))
    table.add_child("sb_m", m_path)
    assert table.compose_composites("me", "l0") == []
    assert table.find_skill("calc_and_save") is None

    # 补上 disk 能力 → 基因组合覆盖 → 编排出复合技能
    d_path = provisioner.create_sandbox(str(tmp_path / "sb_d"), "sb_d",
                                        C.ROLE_AGENT, "root", "l2", ["disk_haa"])
    provisioner.write_skills(d_path, declare_local_skills({C.GENE_DISK_WRITE}, "l2"))
    table.add_child("sb_d", d_path)
    comp = table.compose_composites("me", "l0")
    assert len(comp) == 1 and comp[0]["skill_id"] == "calc_and_save"
    assert comp[0]["composite"] is True
    assert set(comp[0]["required_genes"]) == {C.GENE_CPU_CALC, C.GENE_DISK_WRITE}
    assert comp[0]["next_hop"] == "me"  # 由本路由器分解执行
    assert table.find_skill("calc_and_save")["local"] is True
    # 按 skill_id 查询子提供的基础技能
    hit = table.find_skill("disk_write")
    assert hit["via"] == "sb_d" and hit["local"] is False


# ---------- 无性繁殖（真实 OS + 子进程） ----------
def test_gene_not_owned_raises_instead_of_reproducing(tmp_path):
    """公理 1 + 控制流第一定律：跨域缺基因（不可达）不繁殖，抛 GeneNotOwned 上抛。"""
    import pytest
    from agent_os.agent.reproduction import GeneNotOwned, Reproducer
    from agent_os.agent.skill_table import SkillTable
    from agent_os.os_layer.client import OSClient
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "os"), exist_ok=True)
    os.makedirs(os.path.join(root, "requests"), exist_ok=True)
    os.makedirs(os.path.join(root, "replies"), exist_ok=True)
    table = SkillTable(declare_local_skills({C.GENE_CPU_CALC}, "l1"))
    repro = Reproducer("sb_cpu", str(tmp_path / "sb_cpu"), root,
                       OSClient(root, "sb_cpu"), table, {},
                       genes={C.GENE_CPU_CALC})
    # 自身基因集内缺技能 → 繁殖
    # （无 OS 主循环时 provision 会超时，这里只验证跨域分支：不繁殖直接抛）
    with pytest.raises(GeneNotOwned) as ei:
        repro.ensure_gene(C.GENE_DISK_WRITE)
    assert ei.value.gene == C.GENE_DISK_WRITE


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
