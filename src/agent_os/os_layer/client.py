# -*- coding: utf-8 -*-
"""OS 控制通道客户端（沙箱/界面层 → OS 层的文件化 syscall）。

请求：写入 sandbox_root/os/requests/<actor_id>_<seq>.json
回复：轮询 sandbox_root/os/replies/<actor_id>_<seq>.json
这是 L3/L2 与 L4 之间的特权通道（等价于真实 OS 的 syscall 边界）。
"""
import os
import time
import uuid

from ..utils import jsonio, logger

REQ_DIR = os.path.join("os", "requests")
REPLY_DIR = os.path.join("os", "replies")


class OSClient:
    def __init__(self, sandbox_root: str, actor_id: str, poll_ms: int = 50,
                 timeout_s: float = 10.0):
        self.root = os.path.abspath(sandbox_root)
        self.actor = actor_id
        self.req_dir = os.path.join(self.root, REQ_DIR)
        self.reply_dir = os.path.join(self.root, REPLY_DIR)
        self.poll = poll_ms / 1000.0
        self.timeout = timeout_s
        self._seq = 0

    # ---- 底层请求/回复 ----
    def request(self, cmd: str, **kwargs) -> dict:
        """发请求并阻塞等待回复。超时抛 TimeoutError。"""
        self._seq += 1
        seq = self._seq
        # 文件名带随机后缀：不同进程即使同 actor/seq 也不撞名（防 WinError 32）
        name = f"{self.actor}_{seq}_{uuid.uuid4().hex[:4]}"
        req = {"seq": seq, "req_id": uuid.uuid4().hex,
               "actor_id": self.actor, "cmd": cmd, **kwargs}
        jsonio.write_json(os.path.join(self.req_dir, f"{name}.json"), req)
        reply_path = os.path.join(self.reply_dir, f"{name}.json")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            rep = jsonio.read_json(reply_path)
            if rep is not None:
                try:
                    os.remove(reply_path)
                except OSError:
                    pass
                if rep.get("ok") is False:
                    raise OSError(rep.get("error", "OS 拒绝"))
                return rep
            time.sleep(self.poll)
        raise TimeoutError(f"OS 无响应: {cmd}")

    # ---- 高层 API ----
    def provision(self, parent_id: str, role: str, genes: list[str]) -> dict:
        """请求 OS 繁殖新沙箱。返回 {sandbox_id, lineage_tag, path}。"""
        return self.request("provision", parent_id=parent_id, role=role,
                            genes=list(genes))

    def report_done(self, sandbox_id: str, final_output: dict | None = None) -> dict:
        """任务完成上报（Root 触发 OS teardown）。final_output 由 OS 落盘供界面层读取。"""
        return self.request("report_done", sandbox_id=sandbox_id,
                            final_output=final_output or {})

    def acl_check(self, actor_id: str, target_sandbox_id: str, path: str) -> bool:
        """越权判定（供防御性校验与测试）。"""
        rep = self.request("acl_check", actor_id=actor_id,
                           target_sandbox_id=target_sandbox_id, path=path)
        return bool(rep.get("allowed"))
