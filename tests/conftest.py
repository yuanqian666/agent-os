# -*- coding: utf-8 -*-
"""pytest 公共配置：sys.path、静默日志、公共 fixtures。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pytest  # noqa: E402

from agent_os.utils import logger  # noqa: E402


@pytest.fixture
def sandbox_root(tmp_path):
    """每个测试独立沙箱根。"""
    return str(tmp_path / "sandbox_root")


@pytest.fixture
def out_root(tmp_path):
    """独立落盘目录（Disk_HAA 输出）。"""
    return str(tmp_path / "out")


@pytest.fixture(autouse=True)
def quiet_logs():
    """默认静默控制台，日志经 hook 捕获。"""
    logger.set_quiet(True)
    yield
    logger.set_quiet(False)
    logger.clear_hooks()


@pytest.fixture
def logs():
    """捕获全部日志：logs.items == [(level, msg), ...]"""
    items: list[tuple[str, str]] = []
    logger.add_hook(lambda level, msg: items.append((level, msg)))
    return items
