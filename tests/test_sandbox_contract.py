# -*- coding: utf-8 -*-
"""S9-1 沙箱契约与 ACL（无进程，纯逻辑 + 目录结构）。"""
import os

from agent_os import constants as C
from agent_os import schemas as S
from agent_os.os_layer import provisioner
from agent_os.os_layer.supervisor import Supervisor, ROOT_ID, OS_ID, INTERFACE_ID


# ---------- 目录契约 ----------
def test_sandbox_directory_contract(tmp_path):
    path = provisioner.create_sandbox(
        str(tmp_path / "sb_x"), "sb_x", C.ROLE_AGENT, "root",
        "lineage_l1", ["math_haa"])
    for d in C.SANDBOX_DIRS:
        assert os.path.isdir(os.path.join(path, d)), f"缺少 {d}/"
    # genome：父创建时写
    genome = provisioner.read_genome(path)
    assert genome["haa_identifiers"] == ["math_haa"]
    assert genome["lineage_tag"] == "lineage_l1"
    # manifest
    m = provisioner.read_manifest(path)
    assert m["sandbox_id"] == "sb_x" and m["role"] == C.ROLE_AGENT
    assert m["parent_id"] == "root"
    # status 初值
    assert provisioner.read_state(path) == C.IDLE
    assert provisioner.read_skills(path) == []
    assert provisioner.read_output(path) == {}


# ---------- ACL ----------
def _make_supervisor(sandbox_root) -> Supervisor:
    sup = Supervisor(sandbox_root)
    # 手动注入注册表（不 spawn 进程）
    def _entry(sid, role, parent, haa_ids=(), haa_name=None):
        return {"sandbox_id": sid, "path": os.path.join(sandbox_root, sid),
                "role": role, "parent_id": parent, "haa_name": haa_name,
                "haa_identifiers": list(haa_ids)}
    sup.registry = {
        INTERFACE_ID: _entry(INTERFACE_ID, C.ROLE_INTERFACE, None),
        ROOT_ID: _entry(ROOT_ID, C.ROLE_ROOT, INTERFACE_ID,
                        [C.HAA_MATH, C.HAA_DISK]),  # Root 拥有全部基因
        "child_a": _entry("child_a", C.ROLE_AGENT, ROOT_ID, ["math_haa"]),
        "child_b": _entry("child_b", C.ROLE_AGENT, ROOT_ID, ["disk_haa"]),
        "evil": _entry("evil", C.ROLE_AGENT, ROOT_ID, []),
        C.HAA_MATH: _entry(C.HAA_MATH, C.ROLE_HAA, OS_ID, haa_name=C.HAA_MATH),
    }
    return sup


def test_acl_task_parent_write_child_read_only(sandbox_root):
    sup = _make_supervisor(sandbox_root)
    task_path = os.path.join(sandbox_root, "child_a", "task", "inbox.jsonl")
    # 父（root）写子 task/ → 允许
    assert sup.acl_allowed(ROOT_ID, "child_a", task_path)
    # 子自己写自身 task/ → 拒绝
    assert not sup.acl_allowed("child_a", "child_a", task_path)
    # 无关第三方写 → 拒绝
    assert not sup.acl_allowed("evil", "child_a", task_path)


def test_acl_status_child_write_parent_read_only(sandbox_root):
    sup = _make_supervisor(sandbox_root)
    state = os.path.join(sandbox_root, "child_a", "status", "state")
    # 子写自身 status/ → 允许
    assert sup.acl_allowed("child_a", "child_a", state)
    # 父（root）写子 status/ → 拒绝（父只能读）
    assert not sup.acl_allowed(ROOT_ID, "child_a", state)


def test_acl_genome_and_manifest_os_only(sandbox_root):
    sup = _make_supervisor(sandbox_root)
    for f in ("genome", "manifest.json"):
        p = os.path.join(sandbox_root, "child_a", f)
        assert not sup.acl_allowed("root", "child_a", p)
        assert not sup.acl_allowed("child_a", "child_a", p)
        assert sup.acl_allowed(OS_ID, "child_a", p)


def test_acl_haa_gene_is_access_token(sandbox_root):
    sup = _make_supervisor(sandbox_root)
    haa_task = os.path.join(sandbox_root, C.HAA_MATH, "task", "inbox.jsonl")
    # 持有 cpu_calc 基因的 child_a 可写 math_haa 的 task/
    assert sup.acl_allowed("child_a", C.HAA_MATH, haa_task)
    # 无该基因的 evil 拒绝
    assert not sup.acl_allowed("evil", C.HAA_MATH, haa_task)
    # 持有 disk_write 的 child_b 拒绝写 math_haa（基因不匹配）
    assert not sup.acl_allowed("child_b", C.HAA_MATH, haa_task)


def test_acl_unregistered_actor_denied(sandbox_root):
    sup = _make_supervisor(sandbox_root)
    assert not sup.acl_allowed("ghost", ROOT_ID, os.path.join(sandbox_root, ROOT_ID, "task", "inbox.jsonl"))


def test_root_holds_all_genes(sandbox_root):
    """规格书 §5/§6：Root 拥有全部基因；繁殖 = 父复制基因子集，越权繁殖被拒。"""
    import pytest
    sup = _make_supervisor(sandbox_root)
    assert sup._genes_of(ROOT_ID) == {C.GENE_CPU_CALC, C.GENE_DISK_WRITE}
    # 子 Agent 只能请求父基因的子集：child_b（仅 disk_write）请求 cpu_calc → 拒绝
    with pytest.raises(ValueError, match="超出父基因集"):
        sup.provision(parent_id="child_b", role=C.ROLE_AGENT,
                      genes=[C.GENE_CPU_CALC])
    # 子集内请求通过基因校验（后续 spawn 由真实进程测试覆盖）
    assert sup._genes_of("child_b") == {C.GENE_DISK_WRITE}


# ---------- schema 契约 ----------
def test_schema_contracts():
    assert S.validate_task(S.make_task("t1", "d", {"a": 1})) == (True, [])
    assert not S.validate_task({"task_id": "", "description": "x"})[0]
    assert S.validate_status(S.make_status(C.RUNNING, current_task="x"))[0]
    for bad in ("weird", "", None):
        import pytest
        with pytest.raises(ValueError):
            S.make_status(bad)  # 非法状态在构造时即拒绝
    assert not S.validate_status({"state": "weird"})[0]
    g = S.make_genome(["math_haa"], "lineage_1")
    assert S.validate_genome(g)[0]
    assert not S.validate_genome({"haa_identifiers": "x", "lineage_tag": "l"})[0]
    sk = S.make_skill("s1", "desc", "hop", "l1", [C.GENE_CPU_CALC])
    assert S.validate_skill(sk)[0]
    assert not S.validate_skill({**sk, "required_genes": "cpu_calc"})[0]
