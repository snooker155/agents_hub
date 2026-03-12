"""
Calculator tool – safe arithmetic expression evaluator.

Supports standard arithmetic (+, -, *, /, **, %), parentheses, and common
math functions (sqrt, abs, floor, ceil, round, log, log10, exp, sin, cos, tan).
Uses Python's ast module for safe evaluation — no exec/eval of arbitrary code.
"""
from __future__ import annotations

import ast
import math
import json
import operator
from typing import Any

from pydantic import BaseModel, Field
from langchain_core.tools import tool


# ── Safe evaluator ────────────────────────────────────────────────────────────

_OPERATORS = {
    ast.Add:      operator.add,
    ast.Sub:      operator.sub,
    ast.Mult:     operator.mul,
    ast.Div:      operator.truediv,
    ast.Mod:      operator.mod,
    ast.Pow:      operator.pow,
    ast.UAdd:     operator.pos,
    ast.USub:     operator.neg,
    ast.FloorDiv: operator.floordiv,
}

_FUNCTIONS = {
    "sqrt":  math.sqrt,
    "abs":   abs,
    "floor": math.floor,
    "ceil":  math.ceil,
    "round": round,
    "log":   math.log,
    "log10": math.log10,
    "log2":  math.log2,
    "exp":   math.exp,
    "sin":   math.sin,
    "cos":   math.cos,
    "tan":   math.tan,
    "asin":  math.asin,
    "acos":  math.acos,
    "atan":  math.atan,
    "atan2": math.atan2,
    "degrees": math.degrees,
    "radians": math.radians,
    "factorial": math.factorial,
    "gcd":   math.gcd,
}

_CONSTANTS = {
    "pi":  math.pi,
    "e":   math.e,
    "tau": math.tau,
    "inf": math.inf,
}


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported literal: {node.value!r}")
    if isinstance(node, ast.Name):
        name = node.id
        if name in _CONSTANTS:
            return _CONSTANTS[name]
        raise ValueError(f"Unknown name: {name!r}")
    if isinstance(node, ast.BinOp):
        op_fn = _OPERATORS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return op_fn(left, right)
    if isinstance(node, ast.UnaryOp):
        op_fn = _OPERATORS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_fn(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        fn_name = node.func.id
        fn = _FUNCTIONS.get(fn_name)
        if fn is None:
            raise ValueError(f"Unknown function: {fn_name!r}")
        args = [_eval_node(a) for a in node.args]
        return fn(*args)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def _safe_eval(expression: str) -> float:
    """Parse and evaluate a mathematical expression safely."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Syntax error in expression: {e}")
    return _eval_node(tree)


# ── LangChain tool ────────────────────────────────────────────────────────────

class CalculatorInput(BaseModel):
    expression: str = Field(
        ...,
        description=(
            "A mathematical expression to evaluate. "
            "Supports +, -, *, /, **, %, //, parentheses, and functions: "
            "sqrt, abs, floor, ceil, round, log, log10, log2, exp, "
            "sin, cos, tan, asin, acos, atan, degrees, radians, factorial, gcd. "
            "Constants: pi, e, tau. "
            "Examples: '2 + 2', 'sqrt(144)', '(3**2 + 4**2)**0.5', 'log(100, 10)'"
        ),
    )


@tool("calculator", args_schema=CalculatorInput)
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression and return the numeric result as JSON.

    Use this tool whenever you need to perform arithmetic or mathematical
    calculations instead of computing them mentally.
    """
    try:
        result = _safe_eval(expression)
        # Return int representation when the result is a whole number
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return json.dumps({"ok": True, "expression": expression, "result": result},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "expression": expression, "error": str(e)},
                          ensure_ascii=False)


__all__ = ["calculator"]
