# -*- coding: utf-8 -*-
"""S9-5 端到端演示：自动断言规格书 §8 五条成功标准 + teardown 清场。

流程与 scripts/run_demo.py 一致（真实子进程：Root/子 Agent/两个 HAA）。
"""
import os
import threading
import time

from agent_os import constants as C
from agent_os.interface.cli import submit_task
from agent_os.os_layer.supervisor import Supervisor, ROOT_ID
from agent_os.utils import jsonio, logger

TASK = {
    "task_id": "e2e1",
    "description": "Calculate 5+7 and save result to disk",
    "parameters": {"expr": "5+7", "save": True},
}


def _run_demo(sandbox_root: str, task: dict, timeout: float = 120.0):
    """启动 OS、提交任务、跑主循环至首个任务完成；返回 (sup, result)。"""
    sup = Supervisor(sandbox_root)
    sup.start()

    def _submit():
        time.sleep(2.0)  # 等 Root 就绪
        submit_task(sandbox_root, task)

    t = threading.Timer(0.0, _submit)
    t.start()
    try:
        sup.run_loop(stop_after_tasks=1, run_timeout_s=timeout)
    finally:
        t.cancel()
    result = jsonio.read_json(os.path.join(sandbox_root, "os", "last_result.json"))
    return sup, result


def test_demo_e2e(sandbox_root, tmp_path):
    out_root = str(tmp_path / "out")
    log_file = str(tmp_path / "os.log")
    os.environ["AGENT_OS_OUT_ROOT"] = out_root
    os.environ["AGENT_OS_LOG_FILE"] = log_file
    sup = None
    try:
        sup, result = _run_demo(sandbox_root, TASK)

        # ---- 成功标准 1：任务被接收并完成 ----
        assert result is not None, "未获得任务结果"
        out = result["output"]
        assert out["task_id"] == "e2e1"
        # 成功标准 3 的结果：数学值 + 落盘文件
        assert out["result"]["value"] == 12
        assert out["result"]["file"] == "e2e1-d.txt"

        # ---- 跨进程日志（含子进程）----
        time.sleep(0.5)  # 等子进程在途写盘落定
        msgs = jsonio.read_text(log_file).splitlines()
        msgs = [m.split("] ", 2)[-1] for m in msgs if m.strip()]

        # ---- 成功标准 2：Root 缺基因 → 分裂出 Math 与 Disk 子 Agent ----
        assert any("缺少基因 cpu_calc" in m for m in msgs), "未见 cpu_calc 繁殖"
        assert any("缺少基因 disk_write" in m for m in msgs), "未见 disk_write 繁殖"
        assert sum("无性繁殖" in m for m in msgs) >= 2
        # 谱系唯一（两个子 Agent 不同谱系）
        lineages = {m.split("lineage=")[1].strip() for m in msgs if "繁殖完成 child=" in m}
        assert len(lineages) == 2

        # ---- 成功标准 3：Root 路由结果给 Disk 分支 ----
        assert any("→ math_haa" in m for m in msgs), "未见 math_haa 路由"
        assert any("→ disk_haa" in m for m in msgs), "未见 disk_haa 路由"
        assert any("写盘 12" in m for m in msgs), "数学结果未注入写盘子任务"

        # ---- 成功标准 4：文件事件驱动状态流转 ----
        assert any("文件事件" in m for m in msgs), "无文件事件日志"
        assert any("idle → running" in m for m in msgs)
        assert sum("running → completed" in m for m in msgs) >= 3  # HAA×2 + Root（或子）

        # ---- 成功标准 5：任务完成后 OS 物理清除所有沙箱 ----
        top = set(os.listdir(sandbox_root))
        assert top == {"os", C.HAA_MATH, C.HAA_DISK, ROOT_ID}, f"残留: {top}"
        root_children = os.listdir(os.path.join(sandbox_root, ROOT_ID, C.CHILDREN_DIR))
        assert root_children == [], f"Root 残留子沙箱: {root_children}"

        # ---- 磁盘落盘验证 ----
        disk_file = os.path.join(out_root, "e2e1-d.txt")
        assert os.path.isfile(disk_file)
        assert jsonio.read_text(disk_file) == "12"
    finally:
        if sup:
            sup.stop()
        os.environ.pop("AGENT_OS_OUT_ROOT", None)
        os.environ.pop("AGENT_OS_LOG_FILE", None)


def test_demo_two_tasks_in_a_row(sandbox_root, tmp_path, logs):
    """连续两个任务：teardown 重建 Root 后系统仍可工作（HAA 持久）。"""
    out_root = str(tmp_path / "out")
    os.environ["AGENT_OS_OUT_ROOT"] = out_root
    sup = None
    try:
        sup, r1 = _run_demo(sandbox_root, TASK)
        assert r1["output"]["result"]["value"] == 12

        # 第二个任务（不同 task_id）
        t2 = {**TASK, "task_id": "e2e2", "parameters": {"expr": "2+3", "save": True}}
        sup2, r2 = _run_demo(sandbox_root, t2)
        assert r2 is not None
        assert r2["output"]["result"]["value"] == 5
        assert jsonio.read_text(os.path.join(out_root, "e2e2-d.txt")) == "5"
        sup = sup2  # 收尾只停最新 supervisor
    finally:
        if sup:
            sup.stop()
        os.environ.pop("AGENT_OS_OUT_ROOT", None)
