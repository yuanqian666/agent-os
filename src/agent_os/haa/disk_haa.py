# -*- coding: utf-8 -*-
"""Disk_HAA：Gene disk_write —— 把字符串写入 out/<task_id>.txt。"""
import os

from .. import constants as C
from .haa_base import HAA


class DiskHAA(HAA):
    haa_name = C.HAA_DISK
    gene = C.GENE_DISK_WRITE

    def __init__(self, sandbox_path: str, out_root: str | None = None):
        super().__init__(sandbox_path)
        # 输出根：优先环境变量（测试注入），默认取沙箱根同级 out/
        self.out_root = (out_root or os.environ.get("AGENT_OS_OUT_ROOT")
                         or os.path.join(os.path.dirname(os.path.abspath(sandbox_path)),
                                         "..", "out"))

    def execute(self, task: dict) -> dict:
        params = task.get("parameters", {})
        content = params.get("content")
        if content is None:
            raise ValueError("缺少 parameters.content")
        tid = task.get("task_id", "unknown")
        out_dir = os.path.abspath(self.out_root)
        os.makedirs(out_dir, exist_ok=True)
        rel = f"{tid}.txt"
        file_path = os.path.join(out_dir, rel)
        from ..utils import jsonio
        jsonio.write_text(file_path, str(content))
        return {"file": rel, "content": str(content)}
