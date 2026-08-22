# -*- coding: utf-8 -*-
"""沙箱契约常量：目录/字段名、状态枚举、基因常量、谱系规则。

对齐《Agent_OS架构说明.docx》§3 沙箱文件系统标准 与 §5 基因/能力表。
"""
import uuid

# ---------- 沙箱目录与文件 ----------
TASK_DIR = "task"                  # 父写-追加 / 子只读
TASK_INBOX = "task/inbox.jsonl"    # 下游任务（JSONL，逐行一个任务）
STATUS_DIR = "status"              # 子写 / 父只读
STATE_FILE = "status/state"        # idle | running | completed | error
CURRENT_TASK_FILE = "status/current_task"
OUTPUT_FILE = "status/output"
HELP_REQUESTS_FILE = "status/help_requests"
SKILLS_FILE = "status/skills"
RESOURCE_USAGE_FILE = "status/resource_usage"
GENOME_FILE = "genome"             # 父创建时写 / 子只读
MANIFEST_FILE = "manifest.json"    # OS 管理
SKILLS_DIR = "skills"              # 子读写，绝不向上传播 payload
CHILDREN_DIR = "children"          # 子管理
STATE_DB = "state.db"              # 子读写（可选内部记忆）

SANDBOX_DIRS = [TASK_DIR, STATUS_DIR, SKILLS_DIR, CHILDREN_DIR]

# ---------- 状态枚举 ----------
IDLE = "idle"
RUNNING = "running"
COMPLETED = "completed"
ERROR = "error"
STATES = (IDLE, RUNNING, COMPLETED, ERROR)

# ---------- 基因（HAA 标识符 ↔ 能力基因） ----------
GENE_CPU_CALC = "cpu_calc"
GENE_DISK_WRITE = "disk_write"
ALL_GENES = (GENE_CPU_CALC, GENE_DISK_WRITE)

HAA_MATH = "math_haa"              # 提供 cpu_calc
HAA_DISK = "disk_haa"              # 提供 disk_write
HAA_GENE_MAP = {HAA_MATH: GENE_CPU_CALC, HAA_DISK: GENE_DISK_WRITE}
GENE_HAA_MAP = {v: k for k, v in HAA_GENE_MAP.items()}

# ---------- 角色 ----------
ROLE_AGENT = "agent"
ROLE_HAA = "haa"
ROLE_INTERFACE = "interface"
ROLE_ROOT = "root"

# ---------- 谱系标签规则 ----------
def new_lineage_tag() -> str:
    """生成唯一谱系标签：lineage_<hex8>"""
    return f"lineage_{uuid.uuid4().hex[:8]}"

def new_sandbox_id(prefix: str = "sb") -> str:
    """生成唯一沙箱 id：<prefix>_<hex8>"""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
