# Agent OS — MVP 制作计划（V1）

> 依据：《Agent_OS架构说明.docx》（权威主规格书，source of truth）+ `Desktop/agent-os/agent-os-构想.md`（设计笔记与可行性评估，辅助理解设计意图）。
> 目标：在本工作区产出可运行的 Agent OS 第一版 MVP（确定性实现、零 LLM 依赖），端到端跑通规格书 §8 定义的 5 条成功演示标准。
> 本计划供后续 agent 在此工作区内逐步执行直至 MVP 完成。

---

## 1. Context（背景与目标）

### 1.1 规格书核心世界观（必须先内化）
- **UNIT**：递归沙箱（Recursive Sandbox）是绝对原子单元，每个 Agent 住在独立沙箱里，沙箱可递归嵌套。
- **通信**：仅文件 I/O（无 HTTP/RPC/内存共享/P2P），由异步文件监听（watchdog）驱动。
- **认知盲区**：每个 Agent 不知道父是谁、子是真 Agent 还是 HAA，只知道"任务从上面来、向下委派"。
- **四层拓扑**：L1 界面层（伪 Agent）→ L2 Agent 树（纯逻辑递归）→ L3 HAA 层（伪 Agent 包装系统调用）→ L4 OS/微内核层（确定性超管理器）。
- **接口同构**：所有层对外接口完全一致 → 伪 Agent（界面层/HAA）可混入树中而不被发现。
- **安全姿态**：零信任隔离；任务结束 OS 销毁整棵 Agent 树（无状态残留）。

### 1.2 MVP 边界（规格书 §8 原话约束 + 用户拍板）
- 技术栈：**Python 3.10+**（本机 3.11.9 已验证）、**watchdog 6.0.0**（已装）、**multiprocessing**（沙箱即进程）。零 LLM。
- 需构建的 HAA 仅两个：`Math_HAA`（gene: `cpu_calc`）、`Disk_HAA`（gene: `disk_write`）。
- 演示成功标准（5 条，逐条验证）：
  1. 用户提交复杂任务："Calculate 5+7 and save result to disk"；
  2. Root 发现缺少基因 → 分裂出 Child A（Math）与 Child B（Disk）；
  3. Child A 完成数学计算、结果上抛，Root 将结果路由下发至 Child B；
  4. 控制台日志明确显示文件读写事件触发状态流转；
  5. 任务完成后 OS 层物理清除所有沙箱。
- **不在 MVP 范围（留作 V1.1）**：免疫机制（拓扑蓝图保存、基因足迹审计、谱系隔离）、有性繁殖（跨谱系合并）。运行时只保留无性繁殖（分裂）。
- 界面层以**结构化 JSON 命令**为协议标准，另附**规则解析器**将演示句翻译为 JSON（不接 LLM）。

### 1.3 环境事实（已验证）
- Python 3.11.9、Windows 10；watchdog 6.0.0 可用（`from watchdog.observers import Observer` 通过）；pytest 未装（S0 补装）。
- 工作区非 git 仓库；仅含 `Agent_OS架构说明.docx` 与 `PLAN.md`。
- 实现语言：**Python 3.10+**（规格书强制 + 零依赖摩擦 + 开发效率最优，本计划以此为准）。

---

## 2. Approach（推荐方案总览）

分层可测试架构：L4 OS 层提供沙箱生命周期与 ACL；L3 HAA 以独立进程持久存在；L2 用**同一个通用 Agent 运行时**递归实例化（进程 = 沙箱 = 树节点）；L1 CLI 作为界面层。沙箱即工作目录，通信即目录内 JSON 文件，事件驱动靠 watchdog。

### 2.1 目录结构（目标产物）

```
Agent_OS工作区/
├── PLAN.md                     # 本计划（执行依据）
├── Agent_OS架构说明.docx       # 规格书（只读，勿改）
└── agent_os/                   # ── 代码根（git 仓库）──
    ├── README.md               # 项目说明 + 运行方式
    ├── requirements.txt        # watchdog、pytest
    ├── .gitignore              # 忽略 sandbox_root/、out/、__pycache__/ 等
    ├── src/agent_os/
    │   ├── __init__.py
    │   ├── constants.py        # 沙箱契约常量：目录/字段名、状态枚举、基因常量、谱系规则
    │   ├── schemas.py          # task/status/manifest/genome 的 JSON schema 与校验函数
    │   ├── os_layer/
    │   │   ├── supervisor.py   # L4：沙箱创建/销毁、进程注册表、ACL 判定、任务结束 teardown
    │   │   └── provisioner.py  # 沙箱目录初始化（task/status/skills/children/manifest/genome）
    │   ├── haa/
    │   │   ├── haa_base.py     # HAA 通用运行时（读 task → 执行 → 写 status，对外与 Agent 同构）
    │   │   ├── math_haa.py     # Gene cpu_calc：安全四则运算（ast 解析，拒绝 eval）
    │   │   └── disk_haa.py     # Gene disk_write：写 out/<task_id>.txt
    │   ├── agent/
    │   │   ├── runtime.py      # L2 通用递归 Agent 运行时（全树复用同一实现）
    │   │   ├── task_loop.py    # watchdog 监听自身 task/ 与各子 status/ 的主循环
    │   │   ├── router.py       # 能力表匹配 / 子任务路由
    │   │   ├── reproduction.py # 无性繁殖：向 OS 请求新子沙箱（基因子集 + 新谱系标签）
    │   │   └── skill_table.py  # 能力表聚合：本地 ∪ 直接子能力表（自下而上）
    │   ├── interface/
    │   │   └── cli.py          # L1：演示句规则解析 + JSON 任务 → 写 Root task/；读 Root status/ → 展示
    │   ├── lifecycle/
    │   │   └── bootstrap.py    # 启动顺序：OS → HAAs → 界面层 → Root；任务结束后重建 Root
    │   └── utils/
    │       ├── watchers.py     # watchdog 封装（文件事件 → 回调 + 事件日志）
    │       ├── jsonio.py       # 原子 JSON 读写（tmp + rename，防半写）
    │       └── logger.py       # 分级控制台日志（文件读写事件必须可见）
    ├── scripts/
    │   ├── run_demo.py         # 一键演示入口
    │   └── demo_input.json     # 演示任务样例
    ├── tests/
    │   ├── test_sandbox_contract.py  # 沙箱目录/ACL 契约
    │   ├── test_routing.py           # 路由与结果聚合
    │   ├── test_reproduction.py      # 无性繁殖 + 基因分配 + 谱系标签
    │   ├── test_aggregation.py       # 能力表自下而上聚合
    │   └── test_demo_e2e.py          # 端到端演示（5 条成功标准全自动断言）
    ├── sandbox_root/           # 运行时沙箱根（任务结束清空；gitignore）
    └── out/                    # Disk_HAA 落盘目录（gitignore）
```

### 2.2 沙箱文件系统契约（规格书 §3 落地映射）

每个沙箱（= 一个目录 = 一个进程）：

| 路径 | ACL | 内容 |
|---|---|---|
| `task/inbox.jsonl` | 父写-追加 / 子只读 | 下游任务，每行一个 JSON：`{"task_id","description","parameters"}` |
| `status/state` | 子写 / 父只读 | `idle \| running \| completed \| error` |
| `status/current_task` | 子写 | 当前子任务描述 |
| `status/output` | 子写 | 执行结果 JSON：`{"task_id","result","details"}` |
| `status/help_requests` | 子写 | 未满足需求数组（含 `path` 字段防路由回环；MVP 保留协议，不做专项 UI） |
| `status/skills` | 子写 | 本地能力表（skill metadata 数组，见 §2.3 schema） |
| `status/resource_usage` | 仅 OS 写 | 占位：进程 pid + 存活标志（MVP 最简） |
| `genome` | 父创建时写 / 子只读 | `{"haa_identifiers":[...], "lineage_tag": "..."}` |
| `manifest.json` | OS 管理 | 元数据：sandbox_id、parent_id、created_at、TTL |
| `skills/` | 子读写，**绝不向上传播** | 能力 payload（MVP 为元数据 JSON 文件） |
| `children/<child_id>/` | 子管理 | 嵌套子沙箱挂载点 |
| `state.db` | 子读写 | 内部持久记忆（MVP 可选，JSON 即可） |

**ACL 落地（最简版，Windows 10 上 FS 权限不可靠）**：OS 层维护**进程注册表**（sandbox_id → 父进程/子进程 pid），OS 提供唯一的沙箱写入 API；越权写入（如子进程写自身 task/、非父进程写子 task/）由 OS 判定拒绝并记日志；agent/HAA 运行时做防御性自查。README 注明：真实 FS 权限 / Docker / Firecracker / seL4 为后续升级路径。

### 2.3 能力表（Skill Table）与路由

**Skill Metadata Schema**（规格书 §5，落地为 `schemas.py`）：
```json
{
  "skill_id": "math_eval",
  "description": "Evaluate a basic arithmetic expression",
  "input_schema": {"expr": "string"},
  "output_schema": {"result": "number"},
  "next_hop": "child_<id>",
  "lineage_tag": "lineage_<id>",
  "required_genes": ["cpu_calc"]
}
```
- **聚合规则**：Agent 能力表 = 本地声明 ∪ 所有直接子能力表并集（子通过 `status/skills` 上报，父监听变化后重算）。Root 即全局能力索引。
- **路由**：父解析任务 → 判断所需基因 → 查能力表 → 找到匹配 skill → 写子任务到对应子 `task/inbox.jsonl`。
- **无性繁殖**（规格书 §6 分裂）：所需基因不在本地/子树时，向 OS 提交"新子沙箱请求"（含基因子集），OS 建沙箱、写 genome（新谱系标签）、spawn 通用 Agent 运行时进程、挂入 `children/`。MVP 只允许 Root 繁殖一层（Child A/B），但实现须支持任意层递归。

### 2.4 Agent 通用循环（递归复用，状态机）

```
监听自身 task/inbox.jsonl 追加 + 各子 status/ 变化
 ├─ 收到任务 → state=running → 更新 current_task
 ├─ 解析任务 → 确定所需基因 → 查能力表
 │   ├─ 有匹配 → 向下路由（写子 task/）或叶子（HAA）执行
 │   └─ 无匹配 → 无性繁殖（见 §2.3）
 ├─ 子 completed → 读子 status/output → 聚合/透传
 ├─ 子 error → 记日志，向上报 error
 └─ 全部子任务完成 → 写自身 status/output → state=completed
      （Root 完成 → 通知 OS teardown）
```

### 2.5 演示流程（规格书 §8 成功标准 → 端到端时序）

1. CLI 启动 bootstrap：OS → 创建持久 HAA（Math/Disk）→ 界面层 → 创建 Root（HAA 挂入 Root 的 children/）。
2. 用户输入演示句 `Calculate 5+7 and save result to disk` → 规则解析器转 JSON：
   `{"task_id":"t1","description":"Calculate 5+7 and save result to disk","parameters":{"expr":"5+7","save":true}}` → 写入 Root `task/inbox.jsonl`。
3. Root 分析需求基因 `{cpu_calc, disk_write}`，能力表缺失 → 繁殖：OS 创建 **Child_A**（genome: cpu_calc，谱系 L1）与 **Child_B**（genome: disk_write，谱系 L2），Root 聚合能力表。
4. Root 将"计算"子任务路由给 Child_A → Child_A 路由给其子 Math_HAA → 算得 12 → `status/output` 逐级上抛。
5. Root 读到 12，将"写盘"子任务（参数 result=12）路由给 Child_B → Child_B → Disk_HAA 写 `out/t1.txt`（内容 `12`）→ completed 逐级上抛。
6. Root 聚合最终结果 → `state=completed` → 界面层轮询展示："Result: 12 (saved to out/t1.txt)"。
7. OS 收到 Root 完成 → **销毁整棵 Agent 树**（kill 进程 + 清空 `sandbox_root/`）→ 重建 Root 等待下一任务。HAAs 与界面层保持存活。

---

## 3. Files to modify（本工作区将产生的文件）

全部为**新文件**（见 §2.1 目录树）。实现顺序依赖：
`constants/schemas` → `utils/*` → `os_layer/*` → `haa/*` → `agent/*` → `interface/cli` → `lifecycle/bootstrap` → `scripts/run_demo` → `tests/*`。

---

## 4. Reuse（可复用资源）

- 规格书 §5 Skill Metadata Schema 与 §3 沙箱契约 → 直接作为 `schemas.py` 蓝本；
- 构想笔记 §9 验证清单（递归创建/状态监听/求助传播/能力聚合）→ 映射为 tests/ 用例（求助传播降级为协议内保留，不单列专项测试）；
- 本机已装 watchdog 6.0.0（仅需补 pytest）；Python 标准库 `multiprocessing`、`ast`（安全求值）、`json`。

---

## 5. Steps（实施清单）

- [ ] **S0 初始化**：`git init`（agent_os/ 内）；写 `requirements.txt`（watchdog、pytest）、`.gitignore`（sandbox_root/、out/、__pycache__/、*.pyc）、README 骨架；`pip install -r requirements.txt`
- [ ] **S1 契约层**：`constants.py`（状态枚举 idle/running/completed/error、目录与字段名常量、基因常量 `cpu_calc`/`disk_write`、谱系标签生成规则）+ `schemas.py`（task/status/manifest/genome/skill 校验函数，含 §2.3 schema）
- [ ] **S2 工具层**：`utils/jsonio.py`（原子写 tmp+rename、JSONL 追加）、`utils/watchers.py`（watchdog 封装：目录事件 → 回调，事件必经日志）、`utils/logger.py`（分级日志：EVENT/TASK/STATUS/ERROR，文件读写事件必须可见）
- [ ] **S3 OS 层**：`provisioner.py`（按 §2.2 建沙箱目录 + 写 genome/manifest + 进程注册表登记父子关系 + 越权判定）、`supervisor.py`（spawn/kill 沙箱进程、ACL 写入 API、任务结束 teardown 整树、重建 Root）
- [ ] **S4 HAA 层**：`haa_base.py`（通用 HAA 循环：watch task/inbox → 执行 → 写 status，对外接口与 Agent 同构）+ `math_haa.py`（ast 安全求值，支持 + - * /，拒绝任意代码）+ `disk_haa.py`（写 `out/<task_id>.txt`，内容为参数中的字符串）
- [ ] **S5 Agent 层**：`skill_table.py`（本地表 + 子表并集聚合，监听子 status/skills 变化重算）、`router.py`（基因→skill 匹配、子任务下发、结果聚合透传）、`reproduction.py`（无性繁殖：向 OS 请求新子沙箱，返回 child_id）、`task_loop.py`（watchdog 主循环：task/inbox + children status 双监听）、`runtime.py`（§2.4 状态机组装 + Root/叶子角色判断）
- [ ] **S6 界面层**：`cli.py`（交互输入：演示句或 JSON；演示句规则解析器；轮询 Root status 展示结果；透出事件日志流）
- [ ] **S7 生命周期**：`bootstrap.py`（启动顺序：OS → HAAs → 界面层 → Root；任务完成 → teardown → 重建 Root 循环）
- [ ] **S8 演示脚本**：`scripts/run_demo.py`（一键跑演示，自动喂演示句）+ `demo_input.json`（结构化任务样例）
- [ ] **S9 测试**：`test_sandbox_contract.py`（目录结构/ACL 越权拒绝）、`test_routing.py`（路由下发 + 结果聚合）、`test_reproduction.py`（分裂 + 基因分配 + 谱系标签唯一）、`test_aggregation.py`（能力表并集聚合）、`test_demo_e2e.py`（自动断言 5 条成功标准 + teardown 后沙箱清空）
- [ ] **S10 文档**：README（四层架构 → 代码模块映射图、运行命令、已知限制与升级路径：Docker/Firecracker/seL4、蓝图免疫、有性繁殖 = V1.1）

---

## 6. Verification（验证方式）

1. **自动化测试**：`python -m pytest tests/ -v` 全绿；
2. **端到端演示**：`python scripts/run_demo.py` 或 `python -m agent_os.interface.cli`，人工核对 5 条成功标准：
   - ① 演示句被解析并下发；② 控制台可见 Root 分裂出 Child_A/Child_B（基因/谱系正确）；③ 结果 12 沿 Child_A → Root → Child_B → Disk_HAA 落盘 `out/t1.txt`；④ 日志逐条显示文件读写事件驱动状态流转（idle→running→completed）；⑤ 任务结束后 `sandbox_root/` 被清空、Root 重建；
3. **ACL 抽查**：构造越权写（子进程写自身 task/）→ OS 拒绝并记日志；
4. **残留检查**：任务完成后无残留 agent 进程（`ps`/任务管理器核对）。

---

## 7. 已确认决策（用户拍板记录）

| # | 决策 | 结论 |
|---|---|---|
| Q1 | 语言 | 计划文档中文（保留英文术语）；实现语言 Python 3.10+（规格书强制，开发效率最优） |
| Q2 | MVP 范围 | 仅演示全链路；免疫机制（蓝图/基因审计）与有性繁殖 → V1.1 |
| Q3 | 界面输入 | 结构化 JSON 为准 + 演示句规则解析器（不接 LLM） |
| Q4 | 沙箱隔离 | 最简：multiprocessing + OS 进程注册表 ACL |
| Q5 | 代码位置 | 先建子目录 `agent_os/` 承载全部代码，PLAN.md 留在工作区根 |

---

## 8. 执行状态（2026-08-23 完成）

- [x] S0 初始化（git init / requirements / .gitignore / README 骨架）
- [x] S1 契约层（constants + schemas）
- [x] S2 工具层（jsonio / logger / watchers）
- [x] S3 OS 层（provisioner / supervisor / client）
- [x] S4 HAA 层（math_haa / disk_haa）
- [x] S5 Agent 层（skill_table / router / reproduction / task_loop / runtime）
- [x] S6 界面层（cli：演示句解析 + JSON）
- [x] S7 生命周期（bootstrap）
- [x] S8 演示脚本（run_demo / demo_input.json）
- [x] S9 测试（19 用例全绿，e2e 自动断言 5 条成功标准；连续 3 轮稳定）
- [x] S10 文档（README：架构映射/运行方式/设计决策/升级路径）

验证结果：`python scripts/run_demo.py` 全链路通过（5+7=12 → 落盘 → teardown）；
`python -m agent_os.interface.cli` 交互可用（多任务、结果正确）；
`python -m pytest tests/ -v` 19/19 通过。

已知问题与后续：见 `agent_os/README.md`「已知限制与升级路径」（V1.1：蓝图免疫、有性繁殖、help_requests 路由、资源配额）。
