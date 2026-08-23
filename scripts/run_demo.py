# -*- coding: utf-8 -*-
"""一键演示：Agent OS MVP 端到端跑通规格书 §8 成功演示。

用法：python scripts/run_demo.py [--sandbox-root PATH] [--timeout N]
演示任务：Calculate 5+7 and save result to disk
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from agent_os.interface.cli import parse_demo_sentence, submit_task, wait_result
from agent_os.lifecycle.bootstrap import bootstrap
from agent_os.utils import jsonio, logger

DEMO_SENTENCE = "Calculate (5+7)*3 and save result to disk"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sandbox-root", default=None,
                    help="沙箱根目录（默认 <repo>/sandbox_root）")
    ap.add_argument("--timeout", type=float, default=90.0, help="总超时（秒）")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sandbox_root = os.path.abspath(args.sandbox_root or os.path.join(repo, "sandbox_root"))

    task = parse_demo_sentence(DEMO_SENTENCE)
    assert task, "演示句解析失败"

    print("=" * 70)
    print(f"Agent OS MVP 演示启动  |  任务: {DEMO_SENTENCE}")
    print(f"沙箱根: {sandbox_root}")
    print("=" * 70)

    def _submit():
        time.sleep(2.0)  # 等待 Root 就绪
        submit_task(sandbox_root, task)

    t = threading.Timer(0.0, _submit)
    t.start()
    try:
        bootstrap(sandbox_root, stop_after_tasks=1, run_timeout_s=args.timeout)
    finally:
        t.cancel()

    result = jsonio.read_json(os.path.join(sandbox_root, "os", "last_result.json"))
    print("=" * 70)
    if result:
        out = result.get("output", {})
        print(f"✅ 任务完成: {out.get('task_id')}")
        print(f"   计算结果: {out.get('result', {}).get('value')}")
        print(f"   落盘文件: {out.get('result', {}).get('file')}")
        # 校验磁盘输出
        disk = out.get("result", {}).get("file")
        if disk:
            fpath = os.path.join(repo, "out", disk)
            content = jsonio.read_text(fpath, "<缺失>")
            print(f"   磁盘内容: {content!r} ({fpath})")
    else:
        print("❌ 未获得任务结果")
        sys.exit(1)
    print("=" * 70)


if __name__ == "__main__":
    main()
