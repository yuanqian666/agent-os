# -*- coding: utf-8 -*-
"""Math_HAA：Gene cpu_calc —— 基于 ast 的安全四则运算求值。

拒绝任意代码执行（不用 eval），仅支持数字常量、四则运算、
取模、整除、幂与一元正负号。
"""
import ast
import operator

from .. import constants as C
from .haa_base import HAA

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
}
_UNOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_eval_expr(expr: str) -> float:
    """安全求值四则运算表达式；非法表达式抛 ValueError。"""
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("表达式为空")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误: {e}")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
            return _UNOPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"不支持的表达式节点: {type(node).__name__}")

    return _eval(tree)


class MathHAA(HAA):
    haa_name = C.HAA_MATH
    gene = C.GENE_CPU_CALC

    def execute(self, task: dict) -> dict:
        params = task.get("parameters", {})
        expr = params.get("expr")
        if not expr:
            raise ValueError("缺少 parameters.expr")
        value = safe_eval_expr(str(expr))
        return {"expr": str(expr), "value": value}
