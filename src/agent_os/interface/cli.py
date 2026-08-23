# -*- coding: utf-8 -*-
"""界面层（L1 伪 Agent）：用户终端。

- 演示句规则解析器：把 "Calculate 5+7 and save result to disk" 之类输入
  翻译成结构化任务（确定性规则，不接 LLM）
- 提交：追加写 Root task/inbox.jsonl（界面层是 Root 的"父"）
- 结果：轮询 os/last_result.json（Root report_done 后由 OS 落盘，跨 teardown 存活）
"""
import json
import os
import re
import threading
import time
import uuid

from .. import constants as C
from ..os_layer import supervisor as sup_mod
from ..os_layer.supervisor import Supervisor, LAST_RESULT
from ..utils import jsonio, logger

# 演示句：Calculate <expr> and save [result] to disk
_EXPR_RE = re.compile(
    r"calculate\s+(?P<expr>[0-9+\-*/().\s]+?)\s+and\s+save", re.IGNORECASE)
_CN_RE = re.compile(
    r"计算\s*(?P<expr>[0-9+\-*/().\s]+?)\s*(?:并|然后)?(?:保存|写入|存)?(?:到)?\s*磁盘", re.IGNORECASE)
# 多 HAA 演示句：读文件 + 计算 + 保存 + 记日志
# 语序兼容："read ... from expr.txt" / "read expr.txt" / "从 expr.txt 读取" / "读取 expr.txt"
_READ_FROM_RE = re.compile(r"(?:read|读取).*?(?:from|从)\s*(?P<file>[\w.\-]+\.\w+)",
                           re.IGNORECASE | re.DOTALL)
_READ_FROM_CN_RE = re.compile(r"从\s*(?P<file>[\w.\-]+\.\w+).*?(?:读取|读)",
                              re.IGNORECASE | re.DOTALL)
_READ_RE = re.compile(r"(?:read|读取)\s+(?P<file>[\w.\-]+\.\w+)", re.IGNORECASE)
_LOG_RE = re.compile(r"(?:log|日志|记录)", re.IGNORECASE)


def parse_demo_sentence(text: str) -> dict | None:
    """把演示句翻译为结构化任务；无法解析返回 None。

    复杂任务：显式声明复合技能 calc_and_save / read_calc_save_log
    （skill = 基因组合的编排说明）。
    """
    text = text.strip()
    for rx in (_EXPR_RE, _CN_RE):
        m = rx.search(text)
        if m:
            expr = m.group("expr").strip()
            return {
                "task_id": f"t{uuid.uuid4().hex[:6]}",
                "description": text,
                "parameters": {"expr": expr, "save": True,
                               "skills": ["calc_and_save"]},
            }
    # 多 HAA 链式：读取文件 → 计算 → 保存 →（可选）记日志
    m_read = (_READ_FROM_RE.search(text) or _READ_FROM_CN_RE.search(text)
              or _READ_RE.search(text))
    if m_read:
        params = {"read": m_read.group("file"), "save": True}
        skills = ["read_calc_save_log"]
        if _LOG_RE.search(text):
            params["log"] = True
        return {
            "task_id": f"t{uuid.uuid4().hex[:6]}",
            "description": text,
            "parameters": {**params, "skills": skills},
        }
    return None


def parse_input(text: str) -> dict | None:
    """输入 → 任务：JSON 直接解析；否则尝试演示句。"""
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            task = json.loads(text)
            return task
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return None
    return parse_demo_sentence(text)


def submit_task(sandbox_root: str, task: dict) -> None:
    """界面层把任务写入 Root 的 task/inbox.jsonl（先等 Root 进程就绪，防竞态）。"""
    wait_os_ready(sandbox_root, timeout=15.0)
    inbox = os.path.join(sandbox_root, "root", C.TASK_INBOX)
    jsonio.append_jsonl(inbox, task)
    logger.task(f"[界面层] 已提交任务 {task.get('task_id')} → Root task/inbox.jsonl")


def wait_os_ready(sandbox_root: str, timeout: float = 15.0) -> None:
    """等待 OS 完成供给（Root 进程已 spawn，pid 已登记）。"""
    registry_path = os.path.join(sandbox_root, sup_mod.OS_DIR)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reg = jsonio.read_json(registry_path) or {}
        root = reg.get("root")
        if root and root.get("pid"):
            return
        time.sleep(0.1)
    raise TimeoutError("等待 OS 就绪超时")


def wait_result(sandbox_root: str, task_id: str | None = None,
                timeout: float = 60.0) -> dict:
    """轮询 os/last_result.json 直到出现结果；指定 task_id 时需匹配。"""
    path = os.path.join(sandbox_root, LAST_RESULT)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = jsonio.read_json(path)
        if r:
            out = r.get("output") or {}
            if task_id is None or out.get("task_id") == task_id:
                return r
        time.sleep(0.2)
    raise TimeoutError("等待任务结果超时")


def render_workflow(result: dict) -> str:
    """从任务结果渲染结构化工作流摘要（Topology DAG 执行链路）。"""
    out = result.get("output") or {}
    lines = []
    lines.append(f"任务 {out.get('task_id')} 执行链路:")
    steps = out.get("steps") or []
    if not steps:
        lines.append("  （无派发步骤记录）")
    for s in steps:
        tag = "祖先编排" if s.get("origin") else "派发"
        lines.append(f"  步骤{s.get('step')}: [{tag}] {s.get('gene')} → {s.get('via')}")
    r = out.get("result") or {}
    if r.get("value") is not None:
        lines.append(f"  计算结果: {r.get('value')}")
    if r.get("file"):
        lines.append(f"  落盘文件: {r.get('file')}")
    return "\n".join(lines)


def _make_state_provider(sup, sandbox_root):
    """Web 面板的数据源：从 supervisor registry 组装沙箱树视图。"""
    from ..os_layer import provisioner

    def _prov() -> dict:
        nodes = []
        for sid, e in sup.registry.items():
            if e.get("path") is None:
                continue  # 虚拟条目（界面层）不作为沙箱节点
            nodes.append({
                "sandbox_id": sid,
                "role": e["role"],
                "parent_id": e.get("parent_id"),
                "haa_identifiers": e.get("haa_identifiers", []),
                "lineage_tag": e.get("lineage_tag"),
                "alive": e.get("alive", True),
                "state": provisioner.read_state(e["path"]),
            })
        return {"nodes": nodes}

    return _prov


def run_interactive(sandbox_root: str, enable_web: bool = True,
                    web_port: int = 8710) -> None:
    """交互式 CLI：supervisor 主循环在后台线程，用户输入驱动；
    enable_web 时内嵌 Web 可视化面板（浏览器实时查看沙箱树/日志/历史）。"""
    root = os.path.abspath(sandbox_root)
    sup = Supervisor(root)
    sup.start()

    ws = None
    if enable_web:
        from .web import WebServer, read_history
        ws = WebServer(state_provider=_make_state_provider(sup, root),
                       history_provider=lambda: read_history(sup.log_path),
                       port=web_port)
        ws.start()

    def _loop():
        sup.run_loop()

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info("Agent OS 交互终端就绪。输入 JSON 任务 / 演示句 / exit")
    try:
        while True:
            try:
                line = input("agent-os> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in ("exit", "quit"):
                break
            task = parse_input(line)
            if task is None:
                logger.warn("无法解析输入（示例：Calculate 5+7 and save result to disk）")
                continue
            submit_task(root, task)
            try:
                r = wait_result(root, task_id=task.get("task_id"))
                logger.task(f"[界面层] 最终结果: {json.dumps(r.get('output'), ensure_ascii=False)}")
                print()
                print(render_workflow(r))
                print()
            except TimeoutError as e:
                logger.error(str(e))
    finally:
        # 优雅关闭：写关闭信号，等待 supervisor 循环退出
        if ws:
            ws.stop()
        jsonio.write_json(os.path.join(root, "os", "shutdown.json"), {})
        t.join(timeout=5)
        sup.stop()
        logger.info("Agent OS 已关闭")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Agent OS 交互终端（L1 界面层）")
    ap.add_argument("--sandbox-root", default=None,
                    help="沙箱根目录（默认 <repo>/sandbox_root）")
    ap.add_argument("--verbose", action="store_true",
                    help="显示全部日志（含文件事件 EVENT 级，默认隐藏噪音）")
    ap.add_argument("--no-web", action="store_true",
                    help="不启动 Web 可视化面板")
    ap.add_argument("--web-port", type=int, default=8710,
                    help="Web 面板端口（默认 8710）")
    args = ap.parse_args()
    # 模块位于 src/agent_os/interface/ → 上溯 4 层到仓库根
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    _root = os.path.abspath(args.sandbox_root or os.path.join(_repo, "sandbox_root"))
    if not args.verbose:
        # 隐藏 EVENT 文件事件噪音，保留 TASK/STATUS/ERROR 工作流；
        # 环境变量传递给 spawn 的子进程（保持同样过滤）
        os.environ.setdefault("AGENT_OS_LOG_LEVEL", "TASK")
        logger.set_min_level("TASK")
    run_interactive(_root, enable_web=not args.no_web, web_port=args.web_port)
