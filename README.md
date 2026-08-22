# Agent OS (MVP)

递归沙箱式 Agent 操作系统的第一版 MVP：确定性实现、零 LLM 依赖，端到端验证"文件即一切 + 四层拓扑 + 基因路由"的核心设计假设。

依据规格书：`../Agent_OS架构说明.docx`（source of truth）。执行计划：`../PLAN.md`。

## 四层架构 → 代码模块映射

| 层 | 角色 | 代码模块 |
|---|---|---|
| L1 | 界面层（伪 Agent） | `src/agent_os/interface/cli.py`（演示句规则解析 + JSON 任务） |
| L2 | Agent 树（递归中间层） | `src/agent_os/agent/`（runtime / task_loop / router / reproduction / skill_table） |
| L3 | HAA 层（伪 Agent 底层） | `src/agent_os/haa/`（math_haa=Gene cpu_calc / disk_haa=Gene disk_write） |
| L4 | OS / 微内核层 | `src/agent_os/os_layer/`（supervisor / provisioner / client） |

## 安装与运行

```bash
pip install -e .            # 可编辑安装（python -m 方式可用）

# 一键演示（自动执行演示句 "Calculate 5+7 and save result to disk"）
python scripts/run_demo.py

# 交互式 CLI（演示句 或 JSON 任务，exit 退出）
python -m agent_os.interface.cli --sandbox-root sandbox_root

# 测试（19 用例：契约/ACL/路由/繁殖/聚合/端到端）
python -m pytest tests/ -v
```

环境变量（可选）：`AGENT_OS_OUT_ROOT`（Disk_HAA 落盘目录）、`AGENT_OS_LOG_FILE`（跨进程日志文件）。

## 演示成功标准（规格书 §8，已全部自动化断言于 test_demo_e2e.py）

1. 用户提交复杂任务："Calculate 5+7 and save result to disk"
2. Root 发现缺少基因 → 分裂出 Child A（cpu_calc，谱系 L1）与 Child B（disk_write，谱系 L2）
3. Child A 经 Math_HAA 算出 12 → 结果上抛 → Root 将 content="12" 路由下发 Child B → Disk_HAA 写盘
4. 控制台日志显示文件读写事件驱动状态流转（idle→running→completed）
5. 任务完成后 OS 层物理清除整棵 Agent 树（仅存 os/ + 持久 HAA + 重建的 Root）

## 关键设计决策（与规格书的对齐与偏差）

- **HAA 持久托管**：Math/Disk HAA 由 OS 层创建并跨任务存活（规格书 §7"HAAs 持久"）；Agent 通过 genome 中的基因获得向对应 HAA 写任务的 ACL 权限（基因即访问令牌）。偏差说明：HAAs 不挂在 Root children/ 下（否则 Root 天生具备能力，破坏"缺基因→繁殖"演示语义）。
- **嵌套沙箱**：Agent 子沙箱嵌套于父 `children/` 下（规格书 §3 挂载点语义）；HAA/Root 平铺于沙箱根。
- **文件化 OS 控制通道**：`sandbox_root/os/requests|replies`（L3↔L4 syscall 边界，符合"一切皆文件"）。
- **两相握手**：任务下发等待 output.task_id 与 state==completed 双条件，防陈旧状态竞态。

## 已知限制与升级路径（V1.1+）

- 沙箱隔离为"进程 + 目录 + OS 进程注册表 ACL"（Windows 上 FS 权限不可靠，ACL 为 OS 判定型）；升级：真实 FS 权限 → Docker → Firecracker → seL4
- 免疫机制（拓扑蓝图保存、基因足迹审计、谱系隔离）未实现 → V1.1
- 有性繁殖（跨谱系基因合并）未实现 → V1.1
- 求助传播（help_requests）协议保留字段，无专项路由/UI → V1.1
- 资源统计（resource_usage）为占位（进程存活信息），未做强制配额 → V1.1
