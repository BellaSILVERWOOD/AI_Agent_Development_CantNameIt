"""把「随手敲出来的数学」转成 LaTeX（类似 AsciiMath / Word 的线性公式输入）。

支持的写法（举例）
------------------
* 分数：`a/b`、`(a+b)/2`、`frac(a)(b)`、`1/2`
* 上下标：`x^2`、`a_i`、`x_i^2`、`A^T`、`A^(-1)`
* 根号：`sqrt(2)`、`sqrt(x+1)`、`root(3)(8)`
* 大型运算符：`sum_(i=1)^n a_i`、`prod`、`int_0^1 f(x) dx`、`lim_(x->0)`
* 函数：`sin`、`cos`、`log`、`ln`、`exp`、`det`、`max`、`min`、`sup`、`inf`
* 括号：`(` `)`、`|x|`（绝对值）、`||x||`（范数）、`{a}`（分组）
* 矩阵：`[[1,2],[3,4]]` → pmatrix
* 希腊字母：`alpha`、`lambda`、`Gamma`，或直接敲 `λ`、`Ω`
* 符号：`<=` `>=` `!=` `->` `=>` `<->` `oo` `+-` `xx` `*` `...`，或直接敲 `≤` `≠` `→` `∞` `±` `×`
* 集合：`RR` `NN` `ZZ` `QQ` `CC`、`in`、`sub` `sube`、`uu` `nn`（并/交）、`AA` `EE`（∀ ∃）
* 其它：`vec(a)` `bar(x)` `hat(b)` `dot(d)` `tilde(t)`、`abs(x)`、`norm(x)`

如果输入里出现反斜杠（例如 `\\frac{1}{2}`），就认为已经是 LaTeX，原样返回。
"""
from __future__ import annotations

import re
from typing import List, Optional

# ------------------------------------------------------------------ 符号表
SYMBOLS = {
    "alpha": r"\alpha", "beta": r"\beta", "gamma": r"\gamma", "delta": r"\delta",
    "epsilon": r"\epsilon", "varepsilon": r"\varepsilon", "zeta": r"\zeta",
    "eta": r"\eta", "theta": r"\theta", "vartheta": r"\vartheta",
    "iota": r"\iota", "kappa": r"\kappa", "lambda": r"\lambda", "mu": r"\mu",
    "nu": r"\nu", "xi": r"\xi", "pi": r"\pi", "rho": r"\rho", "sigma": r"\sigma",
    "tau": r"\tau", "upsilon": r"\upsilon", "phi": r"\phi", "varphi": r"\varphi",
    "chi": r"\chi", "psi": r"\psi", "omega": r"\omega",
    "Gamma": r"\Gamma", "Delta": r"\Delta", "Theta": r"\Theta",
    "Lambda": r"\Lambda", "Xi": r"\Xi", "Pi": r"\Pi", "Sigma": r"\Sigma",
    "Phi": r"\Phi", "Psi": r"\Psi", "Omega": r"\Omega",
    "oo": r"\infty", "infty": r"\infty",
    "le": r"\leq", "leq": r"\leq", "ge": r"\geq", "geq": r"\geq",
    "ne": r"\neq", "neq": r"\neq", "lt": "<", "gt": ">",
    "to": r"\to", "rightarrow": r"\rightarrow", "leftarrow": r"\leftarrow",
    "Rightarrow": r"\Rightarrow", "times": r"\times", "div": r"\div",
    "pm": r"\pm", "mp": r"\mp", "cdot": r"\cdot", "cdots": r"\cdots",
    "dots": r"\dots", "ldots": r"\ldots",
    "in": r"\in", "notin": r"\notin", "sub": r"\subset", "sube": r"\subseteq",
    "supe": r"\supseteq", "cup": r"\cup", "cap": r"\cap", "uu": r"\cup",
    "nn": r"\cap", "emptyset": r"\varnothing",
    "AA": r"\forall", "EE": r"\exists",
    "RR": r"\mathbb{R}", "NN": r"\mathbb{N}", "ZZ": r"\mathbb{Z}",
    "QQ": r"\mathbb{Q}", "CC": r"\mathbb{C}", "PP": r"\mathbb{P}",
    "perp": r"\perp", "parallel": r"\parallel", "angle": r"\angle",
    "degree": r"^{\circ}", "xx": r"\times", "cdots2": r"\cdots",
}

# 直接敲的 Unicode 符号
UNICODE_MAP = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\varepsilon", "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta",
    "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu",
    "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau",
    "υ": r"\upsilon", "φ": r"\varphi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi",
    "Ω": r"\Omega", "∞": r"\infty", "≤": r"\leq", "≥": r"\geq", "≠": r"\neq",
    "≈": r"\approx", "→": r"\to", "←": r"\leftarrow", "⇒": r"\Rightarrow",
    "⇔": r"\Leftrightarrow", "±": r"\pm", "∓": r"\mp", "×": r"\times",
    "÷": r"\div", "·": r"\cdot", "∈": r"\in", "∉": r"\notin", "⊂": r"\subset",
    "⊆": r"\subseteq", "∪": r"\cup", "∩": r"\cap", "∅": r"\varnothing",
    "∀": r"\forall", "∃": r"\exists", "√": r"\sqrt", "∑": r"\sum",
    "∏": r"\prod", "∫": r"\int", "∂": r"\partial", "∇": r"\nabla",
    "°": r"^{\circ}", "′": "'", "⋅": r"\cdot", "∼": r"\sim", "≅": r"\cong",
}

COMMAND_WORDS = {"sqrt", "root", "abs", "norm", "frac"}

FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "log", "ln", "lg", "exp", "det", "dim", "ker",
    "max", "min", "sup", "inf", "lim", "gcd", "deg", "arg",
}

BIG_OPERATORS = {
    "sum": r"\sum", "prod": r"\prod", "int": r"\int", "iint": r"\iint",
    "oint": r"\oint", "bigcup": r"\bigcup", "bigcap": r"\bigcap",
}

ACCENTS = {
    "vec": r"\vec", "bar": r"\overline", "hat": r"\hat", "tilde": r"\tilde",
    "dot": r"\dot", "ddot": r"\ddot", "underline": r"\underline",
}

# 多字符运算符（先长后短，避免 `<=` 被拆成 `<` + `=`）
OPERATORS = ["<=>", "<->", "+-", "-+", "=>", "->", "<=", ">=", "!=", "==",
             ":=", "**", "+", "-", "*", "/", "=", "<", ">", ",", "|"]


class MathInputError(ValueError):
    pass


# ------------------------------------------------------------------ 词法
def _tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "()[]{}^_":
            tokens.append(char)
            index += 1
            continue
        matched = None
        for op in OPERATORS:
            if text.startswith(op, index):
                matched = op
                break
        if matched:
            tokens.append(matched)
            index += len(matched)
            continue
        if char.isdigit() or (char == "." and index + 1 < length
                              and text[index + 1].isdigit()):
            match = re.match(r"\d+(\.\d+)?", text[index:])
            if match:
                tokens.append(match.group(0))
                index += len(match.group(0))
                continue
        if char in UNICODE_MAP:
            tokens.append(UNICODE_MAP[char])
            index += 1
            continue
        if char.isalpha():
            match = re.match(r"[A-Za-z]+", text[index:])
            word = match.group(0) if match else char
            # 优先整体识别符号/函数名，否则按单字母切开（xy → x y）
            for name in sorted(list(SYMBOLS) + list(FUNCTIONS) + list(BIG_OPERATORS)
                               + list(ACCENTS) + list(COMMAND_WORDS),
                               key=len, reverse=True):
                if word.startswith(name):
                    tokens.append(word[:len(name)])
                    index += len(name)
                    break
            else:
                tokens.append(word[0])
                index += len(word) - (len(word) - 1)
            continue
        raise MathInputError(f"看不懂这个字符：{char}")
    return tokens


# ------------------------------------------------------------------ 语法
class _Parser:
    def __init__(self, text: str):
        self.tokens = _tokenize(text)
        self.pos = 0

    # --- 工具
    def peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> Optional[str]:
        token = self.peek()
        if token is not None:
            self.pos += 1
        return token

    def expect(self, token: str) -> None:
        got = self.next()
        if got != token:
            raise MathInputError(f"缺一个 {token}，拿到的是 {got}")

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    # --- 表达式
    def parse(self) -> str:
        result = self.expr()
        return result

    def expr(self) -> str:
        parts = [self.term()]
        while self.peek() in ("+", "-", "+-", "-+", "=", "<", ">", "<=", ">=", "!=",
                              "==", "->", "=>", "<=>"):
            op = self.next()
            parts.append(_operator_latex(op))
            parts.append(self.term())
        return " ".join(parts)

    def term(self) -> str:
        parts = [self.unary()]
        while True:
            token = self.peek()
            if token in ("*", "/", "**"):
                self.next()
                right = self.unary()
                if token == "/":
                    parts = [r"\frac{" + _join(parts) + "}{" + right + "}"]
                elif token == "**":
                    parts = [_attach_script(_join(parts), "^", right)]
                else:
                    parts.append(r"\cdot " + right)
                continue
            if token == "|" :
                break
            if token is not None and self._starts_atom(token):
                # 隐式乘法：2x、2(x+1)、(a+b)(c+d)
                parts.append(self.unary())
                continue
            break
        return _join(parts)

    def unary(self) -> str:
        token = self.peek()
        if token == "-":
            self.next()
            return "-" + self.unary()
        if token == "+":
            self.next()
            return self.unary()
        return self.postfix()

    def postfix(self) -> str:
        base = self.primary()
        while self.peek() in ("^", "_"):
            symbol = self.next()
            operand = self.script_operand()
            base = _attach_script(base, symbol, operand)
        return base

    def script_operand(self) -> str:
        token = self.peek()
        if token == "{":
            self.expect("{")
            parts: List[str] = []
            while self.peek() not in (None, "}"):
                parts.append(self.expr())
            self.expect("}")
            return _join(parts)
        if token == "(":
            self.expect("(")
            inner = self.expr()
            self.expect(")")
            return inner
        if token in ("-", "+"):
            self.next()
            return token + self.script_operand()
        return self.single_atom()

    def single_atom(self) -> str:
        token = self.next()
        if token is None:
            raise MathInputError("公式在这里断了，缺少内容")
        if token.isdigit() or re.match(r"^\d+(\.\d+)?$", token):
            return token
        if token.startswith("\\"):
            return token
        if token in SYMBOLS or token in BIG_OPERATORS or token in ACCENTS:
            return SYMBOLS.get(token) or BIG_OPERATORS.get(token) or token
        if token.isalpha():
            return token
        if token == "(":
            inner = self.expr()
            self.expect(")")
            return r"\left(" + inner + r"\right)"
        raise MathInputError(f"这里不该出现 {token}")

    def _starts_atom(self, token: str) -> bool:
        if token in ("+", "-", "+-", "-+", "*", "/", "=", "<", ">", "<=", ">=", "!=",
                     "==", "->", "=>", "<=>", "|", ")", "]", "}", ",", "**"):
            return False
        return True

    def primary(self) -> str:
        token = self.peek()
        if token is None:
            raise MathInputError("公式在这里断了")
        if token == "(":
            self.next()
            inner = self.expr()
            self.expect(")")
            return r"\left(" + inner + r"\right)"
        if token == "[":
            # 可能是矩阵 [[a,b],[c,d]]，也可能只是方括号
            if self._looks_like_matrix():
                return self.matrix()
            self.next()
            inner = self.expr()
            self.expect("]")
            return r"\left[" + inner + r"\right]"
        if token == "{":
            self.next()
            inner = self.expr()
            self.expect("}")
            return inner
        if token == "|":
            self.next()
            inner = self.expr()
            self.expect("|")
            return r"\left|" + inner + r"\right|"
        if token in ("sqrt", r"\sqrt"):
            self.next()
            return r"\sqrt{" + self._paren_or_atom() + "}"
        if token == "root":
            self.next()
            degree = self._paren_or_atom()
            return r"\sqrt[" + degree + "]{" + self._paren_or_atom() + "}"
        if token in ACCENTS:
            self.next()
            return ACCENTS[token] + "{" + self._paren_or_atom() + "}"
        if token == "abs":
            self.next()
            return r"\left|" + self._paren_or_atom() + r"\right|"
        if token == "norm":
            self.next()
            return r"\left\|" + self._paren_or_atom() + r"\right\|"
        if token in BIG_OPERATORS:
            self.next()
            return BIG_OPERATORS[token]
        if token in FUNCTIONS:
            self.next()
            return "\\" + token + " "
        if token in SYMBOLS:
            self.next()
            return SYMBOLS[token]
        if token.isdigit() or re.match(r"^\d+(\.\d+)?$", token):
            self.next()
            return token
        if token.startswith("\\"):
            self.next()
            return token
        if token.isalpha():
            self.next()
            return token
        if token == "[":
            self.next()
            inner = self.expr()
            self.expect("]")
            return r"\left[" + inner + r"\right]"
        raise MathInputError(f"这里不该出现 {token}")

    def _paren_or_atom(self) -> str:
        token = self.peek()
        if token == "(":
            self.next()
            inner = self.expr()
            self.expect(")")
            return inner
        if token == "{":
            self.next()
            inner = self.expr()
            self.expect("}")
            return inner
        return self.postfix()

    # --- 矩阵
    def _looks_like_matrix(self) -> bool:
        if self.peek() != "[":
            return False
        depth = 0
        for index in range(self.pos, len(self.tokens)):
            token = self.tokens[index]
            if token == "[":
                depth += 1
            elif token == "]":
                depth -= 1
            elif token == "," and depth >= 2:
                return True
        return False

    def matrix(self) -> str:
        rows: List[List[str]] = []
        self.expect("[")
        while True:
            self.expect("[")
            row: List[str] = []
            while True:
                row.append(self.expr())
                if self.peek() == ",":
                    self.next()
                    continue
                break
            self.expect("]")
            rows.append(row)
            if self.peek() == ",":
                self.next()
                continue
            break
        self.expect("]")
        body = " \\\\ ".join(" & ".join(row) for row in rows)
        return r"\begin{pmatrix}" + body + r"\end{pmatrix}"


def _operator_latex(op: str) -> str:
    return {
        "=": "=", "<": "<", ">": ">", "+": "+", "-": "-",
        "<=": r"\leq", ">=": r"\geq", "!=": r"\neq", "==": "=",
        "->": r"\to", "=>": r"\Rightarrow", "<=>": r"\Leftrightarrow",
        "+-": r"\pm", "-+": r"\mp",
    }.get(op, op)


_COMMAND_TAIL = re.compile(r"\\[A-Za-z]+$")


def _join(parts: List[str]) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if not out:
            out = part
            continue
        # \lambda 后面紧跟字母会粘成一个不存在的命令，补个空格
        if _COMMAND_TAIL.search(out) and part[:1].isalpha():
            out += " " + part
        else:
            out += part
    return out


def _attach_script(base: str, symbol: str, operand: str) -> str:
    operand = (operand or "").strip()
    if not operand:
        return base
    if operand.startswith("{") and operand.endswith("}") and operand.count("{") == 1:
        inner = operand[1:-1]
    else:
        inner = operand
    return base + symbol + "{" + inner + "}"


# ------------------------------------------------------------------ 对外接口
def looks_like_latex(text: str) -> bool:
    return "\\" in (text or "")


def to_latex(text: str) -> str:
    """把随手写的数学转成 LaTeX；已经是 LaTeX 的原样返回。"""
    text = (text or "").strip()
    if not text:
        return ""
    if looks_like_latex(text):
        return text
    parser = _Parser(text)
    result = parser.parse()
    if not parser.at_end():
        rest = " ".join(parser.tokens[parser.pos:])
        raise MathInputError(f"公式末尾没看懂：{rest}")
    return result.strip()
