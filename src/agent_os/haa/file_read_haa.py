# -*- coding: utf-8 -*-
"""FileRead_HAA：Gene file_read —— 从数据目录读取文件内容（文本）。"""
import os

from .. import constants as C
from ..utils import jsonio
from .haa_base import HAA


class FileReadHAA(HAA):
    haa_name = C.HAA_FILE_READ
    gene = C.GENE_FILE_READ

    def __init__(self, sandbox_path: str, data_root: str | None = None):
        super().__init__(sandbox_path)
        # 数据根：优先环境变量（测试注入），默认取仓库根 data/
        self.data_root = (data_root or os.environ.get("AGENT_OS_DATA_ROOT")
                          or os.path.join(os.path.dirname(
                              os.path.dirname(os.path.abspath(sandbox_path))),
                              "data"))

    def execute(self, task: dict) -> dict:
        params = task.get("parameters", {})
        rel = params.get("path") or params.get("read")
        if not rel:
            raise ValueError("缺少 parameters.path")
        path = os.path.abspath(os.path.join(self.data_root, rel))
        # 禁止越出数据根
        if not path.startswith(os.path.abspath(self.data_root) + os.sep):
            raise ValueError(f"路径越界: {rel}")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"数据文件不存在: {rel}")
        return {"file": rel, "content": jsonio.read_text(path)}
