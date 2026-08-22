# -*- coding: utf-8 -*-
"""watchdog 封装：把文件系统事件转成回调（带去抖）。

- watch_file(path)：监听单个文件的 创建/修改/移动（监听其父目录并按文件名过滤）
- watch_dir(path)：监听目录内的直接文件变更（非递归）
- 回调签名：callback(path: str, event_type: str)
   event_type ∈ {"created","modified","deleted","moved"}
- 去抖：同一路径在 debounce_ms 内的重复事件合并为一次（Windows 上
  ReadDirectoryChangesW 常对一次写入触发 created+modified 多个事件）
"""
import os
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import logger


class _Handler(FileSystemEventHandler):
    def __init__(self, paths, callback, debounce_ms):
        # paths: {abspath: {"target": "file"|"dir", "dir": abspath_of_parent_or_self}}
        self._paths = paths
        self._cb = callback
        self._deb = debounce_ms / 1000.0
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _matches(self, path: str) -> bool:
        """文件目标精确匹配；目录目标前缀匹配（含目录下所有文件）。"""
        if path in self._paths:
            return True
        for p, spec in self._paths.items():
            if spec["target"] == "dir" and (path.startswith(p + os.sep) or path == p):
                return True
        return False

    def _maybe_fire(self, path: str, event_type: str):
        if not self._matches(path):
            return
        now = time.monotonic()
        with self._lock:
            last = self._last.get(path, 0.0)
            if now - last < self._deb:
                return
            self._last[path] = now
        try:
            self._cb(path, event_type)
        except Exception as e:  # 回调异常不得杀死 watcher 线程
            logger.error(f"watcher 回调异常 {path}: {e}")

    def on_created(self, ev):
        self._maybe_fire(os.path.abspath(ev.src_path), "created")

    def on_modified(self, ev):
        self._maybe_fire(os.path.abspath(ev.src_path), "modified")

    def on_deleted(self, ev):
        self._maybe_fire(os.path.abspath(ev.src_path), "deleted")

    def on_moved(self, ev):
        self._maybe_fire(os.path.abspath(ev.dest_path), "moved")


class FileWatcher:
    def __init__(self, callback, debounce_ms: int = 200):
        self._cb = callback
        self._deb = debounce_ms
        self._observer = None
        self._paths: dict[str, dict] = {}

    # ---- 注册 ----
    def watch_file(self, file_path: str):
        """监听单个文件：注册其父目录，事件按文件名过滤。"""
        ap = os.path.abspath(file_path)
        parent = os.path.dirname(ap)
        self._paths[ap] = {"target": "file", "dir": parent}

    def watch_dir(self, dir_path: str):
        """递归监听目录（含未来新建子目录），用于动态发现子沙箱。"""
        ap = os.path.abspath(dir_path)
        self._paths[ap] = {"target": "dir", "dir": ap, "recursive": True}

    # ---- 生命周期 ----
    def start(self):
        if self._observer is not None:
            return
        handler = _Handler(self._paths, self._cb, self._deb)
        self._observer = Observer()
        # 按父目录分组注册，避免重复 watch
        seen: set[str] = set()
        for spec in self._paths.values():
            d = spec["dir"]
            if d not in seen:
                os.makedirs(d, exist_ok=True)
                self._observer.schedule(handler, d, recursive=spec.get("recursive", False))
                seen.add(d)
        self._observer.daemon = True
        self._observer.start()

    def stop(self):
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=3)
            self._observer = None
