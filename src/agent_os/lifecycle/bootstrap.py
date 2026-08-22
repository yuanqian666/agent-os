# -*- coding: utf-8 -*-
"""生命周期（启动顺序，构想笔记 §7）：

启动：OS 层 → 持久 HAA（math/disk）→ Root（挂载就绪，children 初始为空）
任务结束：Root report_done → OS teardown 销毁整棵 Agent 树 → 重建 Root
关闭：写入 os/shutdown.json → OS 主循环退出 → 停止全部沙箱进程
"""
from ..os_layer.supervisor import Supervisor


def bootstrap(sandbox_root: str, stop_after_tasks: int | None = None,
              run_timeout_s: float | None = None) -> Supervisor:
    """启动 OS 并运行主循环，返回 Supervisor（供测试/脚本收尾）。"""
    sup = Supervisor(sandbox_root)
    sup.start()
    sup.run_loop(stop_after_tasks=stop_after_tasks, run_timeout_s=run_timeout_s)
    sup.stop()
    return sup
