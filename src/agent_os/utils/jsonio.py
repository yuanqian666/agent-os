# -*- coding: utf-8 -*-
"""原子 JSON 读写与 JSONL 追加。

写文件用 tmp + os.replace 保证半写文件不会出现（watchdog 只看到完整内容）。
"""
import json
import os
import tempfile
import time
from typing import Any


def _atomic_replace(tmp: str, path: str, retries: int = 6, delay: float = 0.08) -> None:
    """os.replace 封装：目标文件可能正被其他进程读取（Windows 无 FILE_SHARE_DELETE
    时 replace 到被打开文件会 WinError 32）→ 退避重试。"""
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(delay * (attempt + 1))
    os.replace(tmp, path)  # 最后重试一次，仍失败则抛出


def read_json(path: str) -> Any | None:
    """读 JSON；文件缺失或损坏返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_json(path: str, obj: Any) -> None:
    """原子写 JSON（先写同目录临时文件，再 os.replace）。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _atomic_replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_jsonl(path: str) -> list[dict]:
    """读 JSONL，逐行解析，跳过空行/坏行。"""
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


def append_jsonl(path: str, obj: dict) -> None:
    """追加一行 JSON 到 JSONL 文件（自动建目录）。"""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return default


def write_text(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _atomic_replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_jsonl_last(path: str) -> dict | None:
    """读 JSONL 最后一条有效任务（父追加、子取最新）。"""
    items = read_jsonl(path)
    return items[-1] if items else None
