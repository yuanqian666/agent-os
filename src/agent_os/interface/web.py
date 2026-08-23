# -*- coding: utf-8 -*-
"""Web 可视化面板（L1 界面层的图形化扩展）。

内嵌 HTTP 服务（标准库，零依赖），浏览器打开 http://127.0.0.1:<port> 即可
实时查看：沙箱树拓扑、工作流日志流、运行历史（runtime_log）。

API：
  GET /              → 面板页面
  GET /api/state     → 沙箱树状态（registry 视图）
  GET /api/logs?id=N → 增量日志（id 之后的新条目）
  GET /api/history   → 运行记录（tasks.jsonl）
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..utils import jsonio, logger

_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_ui.html")


class _Handler(BaseHTTPRequestHandler):
    state_provider = None   # () -> dict：沙箱树状态
    history_provider = None  # () -> list：运行记录
    log_provider = None     # (after_id) -> list：文件模式日志（独立面板）
    log_dir = None           # 运行记录目录（不存在时返回空）

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            # 注意：函数存类属性，经 self 访问会被描述符绑定为方法；
            # 必须用 type(self).xxx 取原始函数再调用
            state_prov = type(self).state_provider
            hist_prov = type(self).history_provider
            path = self.path.split("?")[0]
            if path == "/":
                self._serve_html()
            elif path == "/api/state":
                self._json(state_prov() if state_prov else {})
            elif path == "/api/logs":
                after = 0
                if "?" in self.path:
                    q = dict(kv.split("=") for kv in
                             self.path.split("?", 1)[1].split("&") if "=" in kv)
                    after = int(q.get("id", 0))
                log_prov = type(self).log_provider
                if log_prov:  # 独立面板（文件模式）优先
                    self._json(log_prov(after))
                else:
                    self._json(logger.get_since(after))
            elif path == "/api/history":
                self._json(hist_prov() if hist_prov else [])
            else:
                self._json({"error": "not found"}, code=404)
        except Exception as e:  # noqa: BLE001
            try:
                self._json({"error": str(e)}, code=500)
            except Exception:
                pass

    # ---------- 响应 ----------
    def _serve_html(self):
        try:
            with open(_HTML_PATH, "r", encoding="utf-8") as f:
                html = f.read()
        except OSError:
            html = "<h1>web_ui.html 缺失</h1>"
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默访问日志
        pass


class WebServer:
    def __init__(self, state_provider=None, history_provider=None,
                 port: int = 8710):
        self.state_provider = state_provider
        self.history_provider = history_provider
        self.port = port
        self._httpd = None
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> bool:
        if self._httpd:
            return True
        _Handler.state_provider = self.state_provider
        _Handler.history_provider = self.history_provider
        # 端口可能处于 TIME_WAIT（快速重启）：自动递增重试，失败如实报错
        for port in range(self.port, self.port + 20):
            try:
                self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
                self.port = port
                break
            except OSError:
                continue
        else:
            logger.error(f"Web 面板启动失败：端口 {self.port}-{self.port + 19} 均被占用")
            return False
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)
        self._thread.start()
        logger.info(f"Web 面板已启动: {self.url} (exit 退出后关闭)")
        return True

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def read_history(log_path: str) -> list[dict]:
    """读运行记录文件（不存在返回空列表）。"""
    return jsonio.read_jsonl(log_path) if log_path else []


# ================= 独立面板（文件模式） =================
# 不依赖系统进程内存：读 OS 持久化的 registry.json + runtime_log +
# 系统日志文件，可单独开一个进程/窗口查看运行中的系统。

def file_state_provider(sandbox_root: str):
    """从 os/registry.json（OS 每次供给/teardown 后持久化）组装沙箱树。"""
    from ..os_layer import provisioner
    root = os.path.abspath(sandbox_root)
    reg_path = os.path.join(root, "os", "registry.json")

    def _prov() -> dict:
        reg = jsonio.read_json(reg_path) or {}
        nodes = []
        for sid, e in reg.items():
            p = e.get("path")
            if not p or not os.path.isdir(p):
                continue
            nodes.append({
                "sandbox_id": sid,
                "role": e["role"],
                "parent_id": e.get("parent_id"),
                "haa_identifiers": e.get("haa_identifiers", []),
                "lineage_tag": e.get("lineage_tag"),
                "alive": e.get("alive", True),
                "state": provisioner.read_state(p),
            })
        return {"nodes": nodes}

    return _prov


def file_log_provider(log_path: str):
    """从系统日志文件增量读取（按行号 id）。"""
    def _prov(after_id: int = 0) -> list[dict]:
        if not log_path or not os.path.isfile(log_path):
            return []
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return []
        out = []
        for i, ln in enumerate(lines[after_id:], start=after_id + 1):
            ln = ln.strip()
            if not ln:
                continue
            # [HH:MM:SS.mmm] [LEVEL] msg
            level = "INFO"
            if "] [" in ln:
                level = ln.split("] [", 1)[1].split("]", 1)[0]
            msg = ln.split("] ", 2)[-1] if "] " in ln else ln
            out.append({"id": i, "ts": ln[1:20] if ln.startswith("[") else "",
                        "level": level, "msg": msg})
        return out

    return _prov


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Agent OS 独立可视化面板（文件模式）")
    ap.add_argument("--sandbox-root", default=None,
                    help="沙箱根目录（默认 <repo>/sandbox_root）")
    ap.add_argument("--port", type=int, default=8710, help="端口（默认 8710）")
    ap.add_argument("--log-file", default=None,
                    help="系统日志文件（默认 <repo>/runtime_log/system.log）")
    args = ap.parse_args()
    _repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    _root = os.path.abspath(args.sandbox_root or os.path.join(_repo, "sandbox_root"))
    _log = args.log_file or os.path.join(_repo, "runtime_log", "system.log")
    ws = WebServer(state_provider=file_state_provider(_root),
                   history_provider=lambda: read_history(
                       os.path.join(os.path.dirname(_root), "runtime_log",
                                    "tasks.jsonl")),
                   port=args.port)
    if not ws.start():
        raise SystemExit(1)
    # 日志流：文件模式用独立 HTTP 端点 /api/flogs
    _Handler.log_provider = file_log_provider(_log)
    try:
        import time as _t
        while True:
            _t.sleep(3600)
    except KeyboardInterrupt:
        ws.stop()
