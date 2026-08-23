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

演示任务（复杂任务）：**"Calculate (5+7)*3 and save result to disk"** → `parameters: {expr, save, skills:["calc_and_save"]}`

1. 界面层提交复杂任务，显式声明复合技能需求 `calc_and_save`
2. Root（路由器）查能力表未命中 → 按基因分解 → 无性繁殖 Child A（cpu_calc，谱系 L1）与 Child B（disk_write，谱系 L2）
3. 子 Agent 上传技能说明 → Root 聚合 → 声明复合技能 `calc_and_save`（能力向上传播）
4. Child A 经 Math_HAA 算出 36 → 结果上抛 → Root 路由给 Child B → Disk_HAA 写盘
5. 控制台日志显示文件读写事件驱动状态流转 + 路由器决策链
6. 任务完成后 OS 层物理清除整棵 Agent 树（仅存 os/ + 持久 HAA + 重建的 Root）

## 架构概念（对齐《严格定义与扩充表述.txt》三大公理与控制流第一定律）

- **公理 1 基因 = 向下可达性**：拥有基因 = 能仅通过下放 task 文件最终触达底层 HAA（ACL 判定可达）；不是静态标签
- **公理 2 公链 = 自下而上的物理权限专线**：每个 HAA（唯一单例叶子）向上由父子关系连到 Root 的唯一链条
- **公理 3 族系 = 任务拆解的嵌套逻辑树**：每个 Agent 的子代与后代构成其族系，族系内再繁殖形成嵌套子族系
- **控制流第一定律：上报顺族系、下放顺公链**：叶子完成计算/遇不可达子任务 → status 沿族系层层上报至拥有所需基因的祖先节点；祖先通过公链将执行指令下发至 HAA；祖先节点天然充当并发排队机
- **跨域缺基因 → help_request 沿族系上抛**（不横向杂交）：由同时拥有多基因的祖先作为 MapReduce 编排者，按任务步骤向各族系分支/公链派发（`GeneNotOwned` 机制 + `[祖先编排]` 日志）
- **HAA 是权限令牌而非技能**（机制与策略分离）：HAA 声明 `token_<gene>`（物理机制说明）；策略/技能属于 LLM Agent 侧
- **保留能力 = 保留祖先调度蓝图**：runtime_log 记录每次派发步骤（Topology DAG），随任务记录持久化
- **skill = 基因组合的编排说明**：子 Agent 把技能（含 required_genes/编排说明）写入 status/skills 向上传播，父聚合后编排复合技能（如 calc_and_save）
- **认知盲区**：每个 Agent 只知"任务从上面来、向下委派"，不知父是否为 Root、子是否为 HAA。

## 关键设计决策（与规格书的对齐与偏差）

- **Root 持有全部基因**（规格书 §5 全局能力索引 / §6 父复制基因子集给子）：Root 的 genome = [math_haa, disk_haa]，但**不声明本地技能**（纯协调者，执行下放）。Root "缺少执行技能"时触发无性繁殖：把自己的基因**子集**复制给新子沙箱（OS 校验子请求基因 ⊆ 父基因集，越权繁殖被拒——防提权）。
- **HAA 持久托管**：Math/Disk HAA 由 OS 层创建并跨任务存活（规格书 §7"HAAs 持久"）；Agent 通过 genome 中的基因获得向对应 HAA 写任务的 ACL 权限（基因即访问令牌）。偏差说明：HAAs 不挂在 Root children/ 下（否则 Root 天生具备能力，破坏"缺技能→繁殖"演示语义）。
- **嵌套沙箱**：Agent 子沙箱嵌套于父 `children/` 下（规格书 §3 挂载点语义）；HAA/Root 平铺于沙箱根。
- **文件化 OS 控制通道**：`sandbox_root/os/requests|replies`（L3↔L4 syscall 边界，符合"一切皆文件"）。
- **两相握手**：任务下发等待 output.task_id 与 state==completed 双条件，防陈旧状态竞态。

## 已知限制与升级路径（V1.1+）

- 沙箱隔离为"进程 + 目录 + OS 进程注册表 ACL"（Windows 上 FS 权限不可靠，ACL 为 OS 判定型）；升级：真实 FS 权限 → Docker → Firecracker → seL4
- 免疫机制（拓扑蓝图保存、基因足迹审计、谱系隔离）未实现 → V1.1
- 有性繁殖（跨谱系基因合并）未实现 → V1.1
- 求助传播（help_requests）协议保留字段，无专项路由/UI → V1.1
- 资源统计（resource_usage）为占位（进程存活信息），未做强制配额 → V1.1
