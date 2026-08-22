# -*- coding: utf-8 -*-
"""JSON schema 校验：task / status / manifest / genome / skill。

对齐规格书 §3（沙箱契约）、§5（Skill Metadata Schema）。
所有校验函数返回 (ok: bool, errors: list[str])。
"""
from typing import Any

from . import constants as C

STATE_SET = set(C.STATES)


# ---------- 通用 ----------
def _is_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


# ---------- task ----------
# {"task_id": str, "description": str, "parameters": dict}
def validate_task(task: Any) -> tuple[bool, list[str]]:
    errs: list[str] = []
    if not _is_dict(task):
        return False, ["task 必须是 JSON 对象"]
    if not _is_str(task.get("task_id")):
        errs.append("task_id 必须是非空字符串")
    if not _is_str(task.get("description")):
        errs.append("description 必须是非空字符串")
    params = task.get("parameters", {})
    if not _is_dict(params):
        errs.append("parameters 必须是对象")
    return not errs, errs


def make_task(task_id: str, description: str, parameters: dict | None = None) -> dict:
    t = {"task_id": task_id, "description": description, "parameters": parameters or {}}
    ok, errs = validate_task(t)
    if not ok:
        raise ValueError(f"非法 task: {errs}")
    return t


# ---------- status ----------
def validate_status(status: Any) -> tuple[bool, list[str]]:
    errs: list[str] = []
    if not _is_dict(status):
        return False, ["status 必须是 JSON 对象"]
    state = status.get("state", C.IDLE)
    if state not in STATE_SET:
        errs.append(f"state 必须是 {C.STATES} 之一，got {state!r}")
    if "current_task" in status and not _is_str(status["current_task"]):
        errs.append("current_task 必须是非空字符串")
    if "output" in status and not _is_dict(status["output"]):
        errs.append("output 必须是 JSON 对象")
    if "help_requests" in status and not isinstance(status["help_requests"], list):
        errs.append("help_requests 必须是数组")
    if "skills" in status and not isinstance(status["skills"], list):
        errs.append("skills 必须是数组")
    if "resource_usage" in status and not _is_dict(status["resource_usage"]):
        errs.append("resource_usage 必须是对象")
    return not errs, errs


def make_status(state: str = C.IDLE, **fields) -> dict:
    s = {"state": state, **fields}
    ok, errs = validate_status(s)
    if not ok:
        raise ValueError(f"非法 status: {errs}")
    return s


# ---------- manifest ----------
def validate_manifest(m: Any) -> tuple[bool, list[str]]:
    errs: list[str] = []
    if not _is_dict(m):
        return False, ["manifest 必须是 JSON 对象"]
    for key in ("sandbox_id", "role"):
        if not _is_str(m.get(key)):
            errs.append(f"{key} 必须是非空字符串")
    return not errs, errs


def make_manifest(sandbox_id: str, role: str, parent_id: str | None = None,
                  ttl: int | None = None) -> dict:
    m = {"sandbox_id": sandbox_id, "role": role, "parent_id": parent_id,
         "created_at": None, "ttl": ttl}
    ok, errs = validate_manifest(m)
    if not ok:
        raise ValueError(f"非法 manifest: {errs}")
    return m


# ---------- genome ----------
def validate_genome(g: Any) -> tuple[bool, list[str]]:
    errs: list[str] = []
    if not _is_dict(g):
        return False, ["genome 必须是 JSON 对象"]
    haa = g.get("haa_identifiers")
    if not isinstance(haa, list) or not all(_is_str(h) for h in haa):
        errs.append("haa_identifiers 必须是非空字符串数组")
    if not _is_str(g.get("lineage_tag")):
        errs.append("lineage_tag 必须是非空字符串")
    return not errs, errs


def make_genome(haa_identifiers: list[str], lineage_tag: str) -> dict:
    g = {"haa_identifiers": list(haa_identifiers), "lineage_tag": lineage_tag}
    ok, errs = validate_genome(g)
    if not ok:
        raise ValueError(f"非法 genome: {errs}")
    return g


# ---------- skill（规格书 §5 Skill Metadata Schema） ----------
def validate_skill(s: Any) -> tuple[bool, list[str]]:
    errs: list[str] = []
    if not _is_dict(s):
        return False, ["skill 必须是 JSON 对象"]
    for key in ("skill_id", "description", "next_hop", "lineage_tag"):
        if not _is_str(s.get(key)):
            errs.append(f"{key} 必须是非空字符串")
    for key in ("input_schema", "output_schema"):
        if not _is_dict(s.get(key, {})):
            errs.append(f"{key} 必须是对象")
    genes = s.get("required_genes", [])
    if not isinstance(genes, list) or not all(_is_str(x) for x in genes):
        errs.append("required_genes 必须是非空字符串数组")
    return not errs, errs


def make_skill(skill_id: str, description: str, next_hop: str, lineage_tag: str,
               required_genes: list[str], input_schema: dict | None = None,
               output_schema: dict | None = None) -> dict:
    s = {
        "skill_id": skill_id,
        "description": description,
        "input_schema": input_schema or {},
        "output_schema": output_schema or {},
        "next_hop": next_hop,
        "lineage_tag": lineage_tag,
        "required_genes": list(required_genes),
    }
    ok, errs = validate_skill(s)
    if not ok:
        raise ValueError(f"非法 skill: {errs}")
    return s
