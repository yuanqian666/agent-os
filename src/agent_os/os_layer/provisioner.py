# -*- coding: utf-8 -*-
"""沙箱供给：按 §2.2 契约创建沙箱目录并初始化全部状态文件。"""
import os
from datetime import datetime

from .. import constants as C
from ..utils import jsonio


def create_sandbox(sandbox_path: str, sandbox_id: str, role: str,
                   parent_id: str | None, lineage_tag: str,
                   haa_identifiers: list[str], haa_name: str | None = None) -> str:
    """在给定绝对路径创建沙箱目录与初始文件，返回沙箱绝对路径。

    - task/            （父写 / 子只读）空目录
    - status/          （子写 / 父只读）初始状态文件
    - skills/          （子读写）空目录
    - children/        （子管理）空目录
    - genome           （父创建时写 / 子只读）
    - manifest.json    （OS 管理）
    """
    path = os.path.abspath(sandbox_path)
    os.makedirs(path, exist_ok=True)
    for d in C.SANDBOX_DIRS:
        os.makedirs(os.path.join(path, d), exist_ok=True)

    # genome —— 父创建时写入
    jsonio.write_json(os.path.join(path, C.GENOME_FILE),
                      {"haa_identifiers": list(haa_identifiers),
                       "lineage_tag": lineage_tag})

    # manifest —— OS 管理
    manifest = {
        "sandbox_id": sandbox_id,
        "role": role,
        "parent_id": parent_id,
        "haa_name": haa_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ttl": None,
    }
    jsonio.write_json(os.path.join(path, C.MANIFEST_FILE), manifest)

    # status 初始文件 —— 子写，OS 初始化
    _write_state_file(path, C.IDLE)
    jsonio.write_text(os.path.join(path, C.CURRENT_TASK_FILE), "")
    jsonio.write_json(os.path.join(path, C.OUTPUT_FILE), {})
    jsonio.write_json(os.path.join(path, C.HELP_REQUESTS_FILE), [])
    jsonio.write_json(os.path.join(path, C.SKILLS_FILE), [])
    jsonio.write_json(os.path.join(path, C.RESOURCE_USAGE_FILE),
                      {"pid": None, "status": "provisioned"})

    return path


def _write_state_file(sandbox_path: str, state: str) -> None:
    """status/state 为纯文本枚举。"""
    jsonio.write_text(os.path.join(sandbox_path, C.STATE_FILE), state)


# ---- 状态读写辅助（沙箱运行时用） ----
def read_state(sandbox_path: str) -> str:
    return jsonio.read_text(os.path.join(sandbox_path, C.STATE_FILE), C.IDLE)


def write_state(sandbox_path: str, state: str) -> None:
    assert state in C.STATES, f"非法状态 {state}"
    _write_state_file(sandbox_path, state)


def write_current_task(sandbox_path: str, desc: str) -> None:
    jsonio.write_text(os.path.join(sandbox_path, C.CURRENT_TASK_FILE), desc)


def write_output(sandbox_path: str, output: dict) -> None:
    jsonio.write_json(os.path.join(sandbox_path, C.OUTPUT_FILE), output)


def write_skills(sandbox_path: str, skills: list[dict]) -> None:
    jsonio.write_json(os.path.join(sandbox_path, C.SKILLS_FILE), skills)


def write_help_requests(sandbox_path: str, reqs: list[dict]) -> None:
    jsonio.write_json(os.path.join(sandbox_path, C.HELP_REQUESTS_FILE), reqs)


def read_skills(sandbox_path: str) -> list[dict]:
    return jsonio.read_json(os.path.join(sandbox_path, C.SKILLS_FILE)) or []


def read_output(sandbox_path: str) -> dict:
    return jsonio.read_json(os.path.join(sandbox_path, C.OUTPUT_FILE)) or {}


def read_genome(sandbox_path: str) -> dict:
    return jsonio.read_json(os.path.join(sandbox_path, C.GENOME_FILE)) or {}


def read_manifest(sandbox_path: str) -> dict:
    return jsonio.read_json(os.path.join(sandbox_path, C.MANIFEST_FILE)) or {}
