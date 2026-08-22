# Agent OS (MVP)

递归沙箱式 Agent 操作系统的第一版 MVP：确定性实现、零 LLM 依赖，端到端验证"文件即一切 + 四层拓扑 + 基因路由"的核心设计假设。

依据规格书：`../Agent_OS架构说明.docx`（source of truth）。执行计划：`../PLAN.md`。

## 四层架构 → 代码模块映射

| 层 | 角色 | 代码模块 |
|---|---|---|
| L1 | 界面层（伪 Agent） | `src/agent_os/interface/cli.py` |
| L2 | Agent 树（递归中间层） | `src/agent_os/agent/`（runtime/task_loop/router/reproduction/skill_table） |
| L3 | HAA 层（伪 Agent 底层） | `src/agent_os/haa/`（math_haa/disk_haa） |
| L4 | OS / 微内核层 | `src/agent_os/os_layer/`（supervisor/provisioner） |

## 运行

```bash
pip install -r requirements.txt

# 一键演示（自动执行演示句 "Calculate 5+7 and save result to disk"）
python scripts/run_demo.py

# 交互式 CLI（可输入演示句或 JSON 任务）
python -m agent_os.interface.cli

# 测试
python -m pytest tests/ -v
```

## 演示成功标准（规格书 §8）

1. 用户提交复杂任务："Calculate 5+7 and save result to disk"
2. Root 发现缺少基因 → 分裂出 Child A（Math）与 Child B（Disk）
3. Child A 完成数学计算、结果上抛，Root 将结果路由下发至 Child B
4. 控制台日志明确显示文件读写事件触发状态流转
5. 任务完成后 OS 层物理清除所有沙箱

## 已知限制与升级路径（V1.1+）

- 沙箱隔离为"进程 + 目录 + OS 进程注册表 ACL"（Windows 上 FS 权限不可靠）；升级路径：真实 FS 权限 → Docker → Firecracker → seL4
- 免疫机制（拓扑蓝图保存、基因足迹审计、谱系隔离）未实现 → V1.1
- 有性繁殖（跨谱系基因合并）未实现 → V1.1
- 求助传播（help_requests）协议保留在运行时，无专项 UI/测试
