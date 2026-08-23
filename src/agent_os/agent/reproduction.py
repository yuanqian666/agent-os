# -*- coding: utf-8 -*-
"""繁殖引擎（§6 无性繁殖）：Agent 缺基因时向 OS 请求新子沙箱。

- 分裂：父请求 OS 以基因子集 + 新谱系标签供给子沙箱，挂入 children/
- 已具备能力（本地或子树）时直接路由，不繁殖
"""
import os

from .. import constants as C
from ..os_layer.client import OSClient
from ..utils import logger


class Reproducer:
    def __init__(self, sandbox_id: str, sandbox_path: str, sandbox_root: str,
                 os_client: OSClient, skill_table, children: dict[str, str],
                 is_root: bool = False):
        self.sandbox_id = sandbox_id
        self.path = sandbox_path
        self.root = sandbox_root
        self.os = os_client
        self.table = skill_table
        self.children = children  # child_id → child_path
        self.is_root = is_root

    # ---------- 路径解析 ----------
    def path_of(self, via: str) -> str:
        """把路由目标解析为沙箱绝对路径（本地/HAA/子）。"""
        if via == self.sandbox_id:
            return self.path
        if via in self.children:
            return self.children[via]
        if via in (C.HAA_MATH, C.HAA_DISK):
            return os.path.join(self.root, via)
        raise KeyError(f"未知路由目标 {via}")

    # ---------- 能力保证 ----------
    def ensure_gene(self, gene: str, wait_ready: float = 15.0) -> tuple[str, str, bool]:
        """确保具备某基因的执行能力，返回 (via, path, is_local)。

        路由器语义（规格书 §8）：Root 直连 HAA 拥有全部基因与基础技能索引，
        但**不亲自执行**——命中 HAA 时也繁殖族系子（子按所连 HAA/基因划分族系），
        由族系子路由到 HAA 执行。普通 Agent 命中 HAA 时直接路由。
        """
        entry = self.table.find_for_gene(gene)
        if entry is not None:
            via = entry["via"]
            if via in (C.HAA_MATH, C.HAA_DISK):
                if self.is_root:
                    logger.task(f"{self.sandbox_id} [路由器]: 基因 {gene} 直连 "
                                f"{via}，但 Root 不执行 → 繁殖族系子")
                    return self._reproduce(gene, wait_ready)
                return via, self.path_of(via), True
            return via, self.path_of(via), entry["local"]
        logger.task(f"{self.sandbox_id}: 缺少 {gene} 执行技能 → 无性繁殖（复制基因子集 [{gene}]）")
        return self._reproduce(gene, wait_ready)

    def _reproduce(self, gene: str, wait_ready: float) -> tuple[str, str, bool]:
        """无性繁殖（规格书 §6）：父把自己的基因子集复制给新子沙箱，OS 校验子集关系。"""
        rep = self.os.provision(parent_id=self.sandbox_id, role=C.ROLE_AGENT,
                                genes=[gene])
        cid = rep["sandbox_id"]
        cpath = rep["path"]
        self.children[cid] = cpath
        self.table.add_child(cid, cpath, wait_ready=wait_ready)
        logger.task(f"{self.sandbox_id}: 繁殖完成 child={cid}（族系按基因 {gene} 划分）"
                    f" lineage={rep.get('lineage_tag')}")
        return cid, cpath, False
