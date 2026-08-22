# -*- coding: utf-8 -*-
"""OS / 微内核层（L4）——确定性超管理器。

职责：
- 沙箱供给（provision）：建目录、写 genome/manifest、spawn 进程、登记注册表
- ACL 判定：task/ 父写子读、status/ 子写父读、genome/manifest 仅 OS、
  HAA 的 task/ 仅持有对应基因者可写（基因即访问令牌）
- 文件化控制通道：处理 sandbox_root/os/requests/* 请求并回写 replies/*
- 任务结束 teardown：销毁整棵 Agent 树（HAAs 与界面层保持存活），重建 Root
"""
import os
import shutil
import time
from datetime import datetime

import multiprocessing

from .. import constants as C
from ..utils import jsonio, logger
from . import provisioner

OS_ID = "os"
INTERFACE_ID = "interface"
ROOT_ID = "root"
OS_DIR = os.path.join("os", "registry.json")
LAST_RESULT = os.path.join("os", "last_result.json")
REQ_DIR = os.path.join("os", "requests")
REPLY_DIR = os.path.join("os", "replies")


class Supervisor:
    def __init__(self, sandbox_root: str, poll_ms: int = 50):
        self.root = os.path.abspath(sandbox_root)
        self.poll = poll_ms / 1000.0
        self.registry_path = os.path.join(self.root, OS_DIR)
        self.req_dir = os.path.join(self.root, REQ_DIR)
        self.reply_dir = os.path.join(self.root, REPLY_DIR)
        self.registry: dict[str, dict] = {}
        self._processes: dict[str, multiprocessing.Process] = {}
        self._n_completed = 0
        self._haa_specs = [
            {"sandbox_id": C.HAA_MATH, "haa_name": C.HAA_MATH,
             "genes": [C.GENE_CPU_CALC]},
            {"sandbox_id": C.HAA_DISK, "haa_name": C.HAA_DISK,
             "genes": [C.GENE_DISK_WRITE]},
        ]

    # ================= 生命周期 =================
    def start(self) -> None:
        """清场（杀掉旧注册进程、清空沙箱根），再全新供给 HAAs 与 Root。"""
        if os.path.exists(self.root):
            self._load_registry()
            self._kill_registered_processes()
            shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.req_dir, exist_ok=True)
        os.makedirs(self.reply_dir, exist_ok=True)
        self.registry = {}
        self.registry[INTERFACE_ID] = {  # 虚拟条目：界面层（L1 伪 Agent）
            "sandbox_id": INTERFACE_ID, "path": None, "role": C.ROLE_INTERFACE,
            "parent_id": None, "lineage_tag": "lineage_interface",
            "haa_identifiers": [], "haa_name": None, "pid": None,
            "alive": True, "created_at": datetime.now().isoformat(),
        }
        for spec in self._haa_specs:
            self.provision(parent_id=OS_ID, role=C.ROLE_HAA,
                           genes=spec["genes"], haa_name=spec["haa_name"],
                           sandbox_id=spec["sandbox_id"])
        self.provision(parent_id=INTERFACE_ID, role=C.ROLE_ROOT,
                       genes=[], sandbox_id=ROOT_ID)
        logger.event(f"OS 启动完成：HAAs={[s['sandbox_id'] for s in self._haa_specs]} Root={ROOT_ID}")

    def run_loop(self, stop_after_tasks: int | None = None,
                 run_timeout_s: float | None = None) -> None:
        """主循环：消费控制请求。stop_after_tasks 供测试用；run_timeout_s 为总超时保护。"""
        import time as _t
        tasks_done = 0
        deadline = _t.monotonic() + run_timeout_s if run_timeout_s else None
        shutdown = os.path.join(self.root, "os", "shutdown.json")
        while True:
            if deadline and _t.monotonic() > deadline:
                logger.warn(f"OS 主循环超时退出 ({run_timeout_s}s)")
                return
            if os.path.exists(shutdown):
                try:
                    os.remove(shutdown)
                except OSError:
                    pass
                logger.info("OS 收到关闭信号，退出主循环")
                return
            handled = self._handle_requests_once()
            if handled and self._n_completed > tasks_done:
                tasks_done = self._n_completed
                if stop_after_tasks is not None and tasks_done >= stop_after_tasks:
                    logger.info(f"OS 主循环退出（已处理 {tasks_done} 个任务）")
                    return
            time.sleep(self.poll)

    def stop(self) -> None:
        """停止所有沙箱进程（用于测试收尾）。"""
        self._kill_registered_processes()

    # ================= 供给 =================
    def provision(self, parent_id: str, role: str, genes: list[str],
                  haa_name: str | None = None,
                  sandbox_id: str | None = None) -> dict:
        sid = sandbox_id or C.new_sandbox_id("sb")
        if sid in self.registry:
            raise ValueError(f"沙箱已存在: {sid}")
        virtual = parent_id in (OS_ID, INTERFACE_ID)
        if not virtual and parent_id not in self.registry:
            raise ValueError(f"父沙箱不存在: {parent_id}")

        # 基因 → HAA 标识符（genome 存 HAA 标识符与谱系标签）
        haa_ids = [C.GENE_HAA_MAP[g] for g in genes if g in C.GENE_HAA_MAP]
        lineage = C.new_lineage_tag() if sid != ROOT_ID else "lineage_root"

        # 路径：OS 托管（HAA/Root）平铺于沙箱根；Agent 嵌套于父 children/ 下
        if virtual:
            path = os.path.join(self.root, sid)
        else:
            parent_path = self.registry[parent_id]["path"]
            path = os.path.join(parent_path, C.CHILDREN_DIR, sid)

        path = provisioner.create_sandbox(
            path, sid, role, parent_id, lineage, haa_ids, haa_name=haa_name)

        entry = {
            "sandbox_id": sid, "path": path, "role": role,
            "parent_id": parent_id, "lineage_tag": lineage,
            "haa_identifiers": haa_ids, "haa_name": haa_name,
            "pid": None, "alive": False, "created_at": datetime.now().isoformat(),
        }
        self.registry[sid] = entry

        if role in (C.ROLE_AGENT, C.ROLE_ROOT, C.ROLE_HAA):
            self._spawn(sid)
        self._write_registry()
        logger.task(f"OS 供给沙箱 {sid} (role={role}, genes={genes}, lineage={lineage})")
        return {"sandbox_id": sid, "path": path, "lineage_tag": lineage,
                "genes": list(genes)}

    def _spawn(self, sandbox_id: str) -> None:
        entry = self.registry[sandbox_id]
        path = entry["path"]
        if entry["role"] == C.ROLE_HAA:
            from ..haa.haa_base import haa_main  # 延迟导入防循环
            target, args = haa_main, (path, entry["haa_name"])
        else:
            from ..agent.runtime import runtime_main  # 延迟导入防循环
            target, args = runtime_main, (path,)
        p = multiprocessing.Process(target=target, args=args, daemon=True,
                                    name=f"sb-{sandbox_id}")
        p.start()
        self._processes[sandbox_id] = p
        entry["pid"] = p.pid
        entry["alive"] = True
        self._write_registry()

    def _kill_registered_processes(self) -> None:
        for entry in self.registry.values():
            pid = entry.get("pid")
            if pid:
                try:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        for p in self._processes.values():
            if p.is_alive():
                p.terminate()
        for p in self._processes.values():
            p.join(timeout=2)
        self._processes.clear()

    # ================= ACL =================
    def acl_allowed(self, actor_id: str, target_sandbox_id: str, path: str) -> bool:
        if actor_id == OS_ID:
            return True
        actor = self.registry.get(actor_id)
        target = self.registry.get(target_sandbox_id)
        if not actor or not target:
            return False
        try:
            rel = os.path.relpath(path, target["path"]).replace("\\", "/")
        except ValueError:
            return False
        if rel in ("", "."):
            return False
        if rel.startswith("task/"):
            if target["parent_id"] == actor_id:
                return True
            # HAA：持有对应基因者可写其 task/
            if target["role"] == C.ROLE_HAA and target.get("haa_name"):
                haa_gene = C.HAA_GENE_MAP.get(target["haa_name"])
                if haa_gene and haa_gene in self._genes_of(actor_id):
                    return True
            return False
        if rel.startswith("status/"):
            return actor_id == target_sandbox_id
        if rel in ("genome", "manifest.json"):
            return False
        # skills/、children/、state.db → 沙箱自管
        return actor_id == target_sandbox_id

    def _genes_of(self, sandbox_id: str) -> set[str]:
        entry = self.registry.get(sandbox_id)
        if not entry:
            return set()
        genes = {C.HAA_GENE_MAP.get(h) for h in entry.get("haa_identifiers", [])}
        genes.discard(None)
        return genes

    # ================= 控制请求 =================
    def _handle_requests_once(self) -> bool:
        """处理一批请求，返回是否处理了 root 的 report_done。"""
        try:
            files = sorted(f for f in os.listdir(self.req_dir) if f.endswith(".json"))
        except OSError:
            return False
        root_done = False
        for name in files:
            req_path = os.path.join(self.req_dir, name)
            req = jsonio.read_json(req_path)
            if req is None:
                try:
                    os.remove(req_path)
                except OSError:
                    pass
                continue
            actor = req.get("actor_id")
            seq = req.get("seq")
            reply_name = f"{actor}_{seq}.json" if actor and seq else None
            ok, result = self._dispatch(req)
            if reply_name:
                try:
                    jsonio.write_json(os.path.join(self.reply_dir, reply_name),
                                      {"ok": ok, **(result or {})})
                except OSError:
                    pass
            try:
                os.remove(req_path)
            except OSError:
                pass
            if req.get("cmd") == "report_done" and req.get("sandbox_id") == ROOT_ID:
                root_done = True
                break  # teardown 已清空请求目录，终止本轮迭代
        return root_done

    def _dispatch(self, req: dict) -> tuple[bool, dict]:
        cmd = req.get("cmd")
        try:
            if cmd == "provision":
                r = self.provision(parent_id=req["parent_id"], role=req["role"],
                                   genes=req.get("genes", []))
                return True, r
            if cmd == "report_done":
                sid = req.get("sandbox_id")
                final = req.get("final_output") or {}
                jsonio.write_json(os.path.join(self.root, LAST_RESULT),
                                  {"sandbox_id": sid, "time": datetime.now().isoformat(),
                                   "output": final})
                self._n_completed += 1
                logger.task(f"任务完成上报 {sid} → 销毁 Agent 树")
                self.teardown_tree()
                return True, {"ok": True}
            if cmd == "acl_check":
                allowed = self.acl_allowed(req.get("actor_id"),
                                           req.get("target_sandbox_id"),
                                           req.get("path"))
                return True, {"allowed": allowed}
            return False, {"error": f"未知命令 {cmd}"}
        except Exception as e:
            logger.error(f"OS 处理请求 {cmd} 失败: {e}")
            return False, {"error": str(e)}

    # ================= teardown =================
    def teardown_tree(self) -> None:
        """销毁整棵 Agent 树：杀进程、删沙箱目录；HAAs 与界面层保持存活。"""
        keep = {e["sandbox_id"] for e in self.registry.values()
                if e["role"] == C.ROLE_HAA or e["role"] == C.ROLE_INTERFACE}
        for sid, entry in list(self.registry.items()):
            if sid in keep:
                continue
            p = self._processes.pop(sid, None)
            if p and p.is_alive():
                p.terminate()
                p.join(timeout=2)
            shutil.rmtree(entry["path"], ignore_errors=True)
            del self.registry[sid]
        # 清空控制区
        for d in (self.req_dir, self.reply_dir):
            for f in os.listdir(d):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
        # 重建 Root
        self.provision(parent_id=INTERFACE_ID, role=C.ROLE_ROOT,
                       genes=[], sandbox_id=ROOT_ID)
        logger.event("OS teardown 完成：Agent 树已销毁，Root 已重建")

    # ================= 注册表 =================
    def _write_registry(self) -> None:
        jsonio.write_json(self.registry_path, self.registry)

    def _load_registry(self) -> None:
        reg = jsonio.read_json(self.registry_path) or {}
        self.registry = reg
