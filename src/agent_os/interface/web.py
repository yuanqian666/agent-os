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
