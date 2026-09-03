"""Calculator tool: arithmetic the model should not do in its head.

`calc(expression)` evaluates + - * / // % **, unary minus, parentheses, the functions sqrt,
abs, round, floor, ceil, sin, cos, tan, log, log10, exp, min, max, pow and the constants pi
and e. It is an AST walk over a whitelist — never eval(). Anything else is refused with the
reason; division by zero and overflow are reported, not hidden.
"""
from __future__ import annotations

import ast
import math
import operator as op

_BIN = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
        ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow}
_UN = {ast.UAdd: op.pos, ast.USub: op.neg}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
          "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log, "log10": math.log10,
          "exp": math.exp, "min": min, "max": max, "pow": pow}
_CONSTS = {"pi": math.pi, "e": math.e}
MAX_POW_EXPONENT = 10_000       # refuse absurd powers before Python tries to allocate them


def _ev(node):
    if isinstance(node, ast.Expression):
        return _ev(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        a, b = _ev(node.left), _ev(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > MAX_POW_EXPONENT:
            raise ValueError(f"exponent {b} is too large")
        return _BIN[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_ev(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_ev(a) for a in node.args])
    raise ValueError(f"not arithmetic: {ast.dump(node)[:60]}")


def calc(expression: str) -> dict:
    expr = (expression or "").strip()
    if not expr:
        return {"ok": False, "message": "empty expression"}
    try:
        tree = ast.parse(expr, mode="eval")
        value = _ev(tree)
    except ZeroDivisionError:
        return {"ok": False, "message": "division by zero"}
    except OverflowError:
        return {"ok": False, "message": "result overflows"}
    except (ValueError, SyntaxError, TypeError) as e:
        return {"ok": False, "message": f"refused: {e}"}
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        shown = str(int(value))
    else:
        shown = repr(value)
    return {"ok": True, "message": f"{expr} = {shown}", "data": {"expression": expr, "value": value}}


TOOLS = [{
    "name": "calc",
    "description": ("Evaluate an arithmetic expression exactly: + - * / // % ** with parentheses, "
                    "sqrt, abs, round, floor, ceil, sin, cos, tan, log, log10, exp, min, max, pow, "
                    "and the constants pi and e. Use it whenever a number matters — do not do "
                    "multi-digit arithmetic in your head. It refuses anything that is not arithmetic."),
    "parameters": {"type": "object",
                   "properties": {"expression": {"type": "string", "description": "e.g. 17*23 or sqrt(2)"}},
                   "required": ["expression"]},
}]


def call(name: str, args: dict) -> dict:
    if name != "calc":
        return {"ok": False, "message": f"no tool named {name!r}"}
    return calc(str(args.get("expression", "")))
