# -*- coding: utf-8 -*-
"""Log_HAA：Gene log_write —— 追加一条带时间戳的日志到 out/system.log。"""
import os
from datetime import datetime

from .. import constants as C
from ..utils import jsonio
from .haa_base import HAA


class LogHAA(HAA):
    haa_name = C.HAA_LOG
    gene = C.GENE_LOG_WRITE

    def __init__(self, sandbox_path: str, out_root: str | None = None):
        super().__init__(sandbox_path)
        self.out_root = (out_root or os.environ.get("AGENT_OS_OUT_ROOT")
                         or os.path.join(os.path.dirname(os.path.abspath(sandbox_path)),
                                         "..", "out"))

    def execute(self, task: dict) -> dict:
        params = task.get("parameters", {})
        content = params.get("content")
        if content is None:
            raise ValueError("缺少 parameters.content")
        out_dir = os.path.abspath(self.out_root)
        os.makedirs(out_dir, exist_ok=True)
        log_path = os.path.join(out_dir, "system.log")
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {content}"
        # 追加（原子写：读旧内容+写回，防并发交错）
        old = jsonio.read_text(log_path, "")
        jsonio.write_text(log_path, (old + "\n" + line).strip() + "\n")
        return {"log": "system.log", "content": content}
