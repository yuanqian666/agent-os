# -*- coding: utf-8 -*-
"""S9-2 路由与任务派发（无进程：纯函数 + 模拟子沙箱）。"""
import os
import threading
import time

from agent_os import constants as C
from agent_os.agent import router
from agent_os.os_layer import provisioner


# ---------- 需求基因判定 ----------
def test_required_genes():
    assert router.required_genes({"expr": "5+7"}) == {C.GENE_CPU_CALC}
    assert router.required_genes({"save": True}) == {C.GENE_DISK_WRITE}
    assert router.required_genes({"content": "hi"}) == {C.GENE_DISK_WRITE}
    assert router.required_genes({"expr": "1", "save": True}) == {C.GENE_CPU_CALC, C.GENE_DISK_WRITE}
    assert router.required_genes({}) == set()
    assert router.required_genes(None) == set()


# ---------- 子任务构造 ----------
def test_build_subtask_suffix_and_leaf():
    parent = {"task_id": "t1", "description": "d", "parameters": {"expr": "5+7"}}
    sub = router.build_subtask(parent, C.GENE_CPU_CALC, {"expr": "5+7"}, leaf=False)
    assert sub["task_id"] == "t1-m" and sub["parameters"] == {"expr": "5+7"}
    sub2 = router.build_subtask(parent, C.GENE_CPU_CALC, {"expr": "5+7"}, leaf=True)
    assert sub2["task_id"] == "t1"  # 子→HAA 复用 task_id
    sub3 = router.build_subtask({"task_id": "t1", "description": "d", "parameters": {}},
                                C.GENE_DISK_WRITE, {"content": "12"}, leaf=False)
    assert sub3["task_id"] == "t1-d" and sub3["parameters"] == {"content": "12"}


# ---------- delegate：模拟子沙箱完成 ----------
def test_delegate_returns_output(tmp_path):
    path = provisioner.create_sandbox(str(tmp_path / "sb_child"), "sb_child",
                                      C.ROLE_AGENT, "root", "l1", [])
    task = {"task_id": "t1", "description": "do", "parameters": {"expr": "5+7"}}

    def _simulate():
        # 等任务写入 inbox → 模拟子处理 → 写状态
        deadline = time.monotonic() + 5
        inbox = os.path.join(path, C.TASK_INBOX)
        while time.monotonic() < deadline:
            if os.path.exists(inbox) and os.path.getsize(inbox) > 0:
                break
            time.sleep(0.02)
        provisioner.write_state(path, C.RUNNING)
        time.sleep(0.05)
        provisioner.write_output(path, {"task_id": "t1", "result": {"value": 12}})
        provisioner.write_state(path, C.COMPLETED)

    th = threading.Thread(target=_simulate, daemon=True)
    th.start()
    out = router.delegate(task, path, timeout=10)
    assert out["result"]["value"] == 12
    th.join(timeout=2)


def test_delegate_error_propagates(tmp_path):
    path = provisioner.create_sandbox(str(tmp_path / "sb_bad"), "sb_bad",
                                      C.ROLE_AGENT, "root", "l1", [])
    task = {"task_id": "t1", "description": "do", "parameters": {}}

    def _simulate():
        deadline = time.monotonic() + 5
        inbox = os.path.join(path, C.TASK_INBOX)
        while time.monotonic() < deadline:
            if os.path.exists(inbox) and os.path.getsize(inbox) > 0:
                break
            time.sleep(0.02)
        provisioner.write_output(path, {"task_id": "t1", "error": "boom"})
        provisioner.write_state(path, C.ERROR)

    th = threading.Thread(target=_simulate, daemon=True)
    th.start()
    import pytest
    with pytest.raises(RuntimeError, match="boom"):
        router.delegate(task, path, timeout=10)
    th.join(timeout=2)


def test_delegate_ignores_stale_completed(tmp_path):
    """回归：子方残留上一任务 completed/旧 output 时，不得提前返回。"""
    path = provisioner.create_sandbox(str(tmp_path / "sb_stale"), "sb_stale",
                                      C.ROLE_AGENT, "root", "l1", [])
    # 预置陈旧状态：上一任务的 completed + output
    provisioner.write_output(path, {"task_id": "old", "result": {"value": 999}})
    provisioner.write_state(path, C.COMPLETED)

    task = {"task_id": "new", "description": "do", "parameters": {"expr": "5+7"}}

    def _simulate():
        deadline = time.monotonic() + 5
        inbox = os.path.join(path, C.TASK_INBOX)
        while time.monotonic() < deadline:
            if os.path.exists(inbox) and os.path.getsize(inbox) > 0:
                break
            time.sleep(0.02)
        # 子方处理新任务：running → 新 output → completed
        provisioner.write_state(path, C.RUNNING)
        time.sleep(0.05)
        provisioner.write_output(path, {"task_id": "new", "result": {"value": 12}})
        provisioner.write_state(path, C.COMPLETED)

    th = threading.Thread(target=_simulate, daemon=True)
    th.start()
    out = router.delegate(task, path, timeout=10)
    assert out["result"]["value"] == 12, "不应返回陈旧结果"
    th.join(timeout=2)


def test_aggregate_by_gene():
    results = {"cpu_calc": {"task_id": "t1-m", "result": {"value": 12}}}
    assert router.aggregate_by_gene(results, C.GENE_CPU_CALC) == {"value": 12}
    assert router.aggregate_by_gene(results, C.GENE_DISK_WRITE) == {}
