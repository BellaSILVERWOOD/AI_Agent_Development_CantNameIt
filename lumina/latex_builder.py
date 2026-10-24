"""LaTeX 代码生成：宏包、导言区、以及各种图形化操作对应的片段。

这里只放纯函数，方便单元测试；界面在 page_latex.py / latex_matrix.py。
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence

# ------------------------------------------------------------------ 宏包
# id, 显示的说明, 生成的导言区命令, 是否默认启用
PACKAGES: List[Dict[str, object]] = [
    {"id": "amsmath", "name": "amsmath · 数学公式排版",
     "line": r"\usepackage{amsmath}", "default": True},
    {"id": "amssymb", "name": "amssymb · 数学符号（ℝ ℕ ⇒ 等）",
     "line": r"\usepackage{amssymb}", "default": True},
    {"id": "mathtools", "name": "mathtools · amsmath 增强",
     "line": r"\usepackage{mathtools}", "default": False},
    {"id": "bm", "name": "bm · 数学粗体 \\bm{}",
     "line": r"\usepackage{bm}", "default": True},
    {"id": "graphicx", "name": "graphicx · 插图",
     "line": r"\usepackage{graphicx}", "default": True},
    {"id": "booktabs", "name": "booktabs · 三线表",
     "line": r"\usepackage{booktabs}", "default": True},
    {"id": "array", "name": "array · 表格列格式",
     "line": r"\usepackage{array}", "default": False},
    {"id": "enumitem", "name": "enumitem · 列表间距",
     "line": r"\usepackage{enumitem}", "default": True},
    {"id": "geometry", "name": "geometry · 页面尺寸",
     "line": r"\usepackage[a4paper,margin=2.4cm]{geometry}", "default": True},
    {"id": "xcolor", "name": "xcolor · 颜色",
     "line": r"\usepackage{xcolor}", "default": True},
    {"id": "hyperref", "name": "hyperref · 超链接（最后加载）",
     "line": r"\usepackage{hyperref}", "default": False},
    {"id": "cancel", "name": "cancel · 划线约分 \\cancel{}",
     "line": r"\usepackage{cancel}", "default": False},
    {"id": "siunitx", "name": "siunitx · 单位与数值",
     "line": r"\usepackage{siunitx}", "default": False},
    {"id": "tcolorbox", "name": "tcolorbox · 彩色框",
     "line": r"\usepackage{tcolorbox}", "default": False},
    {"id": "multicol", "name": "multicol · 分栏",
     "line": r"\usepackage{multicol}", "default": False},
    {"id": "longtable", "name": "longtable · 跨页表格",
     "line": r"\usepackage{longtable}", "default": False},
]

DEFAULT_PACKAGES = [str(item["id"]) for item in PACKAGES if item.get("default")]

# 文档类：ctexart 直接用 XeLaTeX 排中文
DOCUMENT_CLASSES = ["ctexart", "article", "ctexrept", "report"]

# ------------------------------------------------------------------ 字体 / 字号
# 图形化「字体」按钮：显示名 -> 命令
FONT_FAMILIES: List[Dict[str, str]] = [
    {"id": "mathrm", "name": "正体（罗马）", "command": r"\mathrm"},
    {"id": "mathbf", "name": "粗体", "command": r"\mathbf"},
    {"id": "mathit", "name": "斜体", "command": r"\mathit"},
    {"id": "mathsf", "name": "无衬线", "command": r"\mathsf"},
    {"id": "mathtt", "name": "等宽", "command": r"\mathtt"},
    {"id": "mathcal", "name": "花体（大型运算符）", "command": r"\mathcal"},
    {"id": "mathbb", "name": "空心体（集合）", "command": r"\mathbb"},
    {"id": "mathfrak", "name": "哥特体", "command": r"\mathfrak"},
    {"id": "boldsymbol", "name": "粗斜体（向量）", "command": r"\boldsymbol"},
]

TEXT_FAMILIES: List[Dict[str, str]] = [
    {"id": "textup", "name": "正文正体", "command": r"\textup"},
    {"id": "textbf", "name": "正文粗体", "command": r"\textbf"},
    {"id": "textit", "name": "正文斜体", "command": r"\textit"},
    {"id": "textsf", "name": "无衬线", "command": r"\textsf"},
    {"id": "texttt", "name": "等宽", "command": r"\texttt"},
    {"id": "underline", "name": "下划线", "command": r"\underline"},
    {"id": "emph", "name": "强调", "command": r"\emph"},
]

SIZES = ["tiny", "scriptsize", "footnotesize", "small", "normalsize",
         "large", "Large", "LARGE", "huge", "Huge"]

ALIGNMENTS = {
    "left": r"\begin{flushleft}%s\end{flushleft}",
    "center": r"\begin{center}%s\end{center}",
    "right": r"\begin{flushright}%s\end{flushright}",
}

# 希腊字母：显示符号 -> 命令名
GREEK: List[Dict[str, str]] = [
    {"id": "alpha", "symbol": "α"}, {"id": "beta", "symbol": "β"},
    {"id": "gamma", "symbol": "γ"}, {"id": "delta", "symbol": "δ"},
    {"id": "epsilon", "symbol": "ϵ"}, {"id": "varepsilon", "symbol": "ε"},
    {"id": "zeta", "symbol": "ζ"}, {"id": "eta", "symbol": "η"},
    {"id": "theta", "symbol": "θ"}, {"id": "vartheta", "symbol": "ϑ"},
    {"id": "iota", "symbol": "ι"}, {"id": "kappa", "symbol": "κ"},
    {"id": "lambda", "symbol": "λ"}, {"id": "mu", "symbol": "μ"},
    {"id": "nu", "symbol": "ν"}, {"id": "xi", "symbol": "ξ"},
    {"id": "pi", "symbol": "π"}, {"id": "rho", "symbol": "ρ"},
    {"id": "sigma", "symbol": "σ"}, {"id": "tau", "symbol": "τ"},
    {"id": "upsilon", "symbol": "υ"}, {"id": "phi", "symbol": "ϕ"},
    {"id": "varphi", "symbol": "φ"}, {"id": "chi", "symbol": "χ"},
    {"id": "psi", "symbol": "ψ"}, {"id": "omega", "symbol": "ω"},
    {"id": "Gamma", "symbol": "Γ"}, {"id": "Delta", "symbol": "Δ"},
    {"id": "Theta", "symbol": "Θ"}, {"id": "Lambda", "symbol": "Λ"},
    {"id": "Xi", "symbol": "Ξ"}, {"id": "Pi", "symbol": "Π"},
    {"id": "Sigma", "symbol": "Σ"}, {"id": "Phi", "symbol": "Φ"},
    {"id": "Psi", "symbol": "Ψ"}, {"id": "Omega", "symbol": "Ω"},
]

# 常用运算符 / 结构：按钮文字 -> 插入的片段（{sel} 表示当前选区）
SNIPPETS: List[Dict[str, str]] = [
    {"name": "分式", "code": r"\frac{%sel%}{}"},
    {"name": "根号", "code": r"\sqrt{%sel%}"},
    {"name": "n 次根", "code": r"\sqrt[3]{%sel%}"},
    {"name": "上下标 a_n", "code": r"%sel%_{}"},
    {"name": "上标 a^n", "code": r"%sel%^{}"},
    {"name": "求和 Σ", "code": r"\sum_{i=1}^{n} %sel%"},
    {"name": "连乘 Π", "code": r"\prod_{i=1}^{n} %sel%"},
    {"name": "积分 ∫", "code": r"\int_{a}^{b} %sel% \,\mathrm{d}x"},
    {"name": "极限 lim", "code": r"\lim_{n \to \infty} %sel%"},
    {"name": "向量", "code": r"\vec{%sel%}"},
    {"name": "范数", "code": r"\lVert %sel% \rVert"},
    {"name": "内积", "code": r"\langle %sel%, \cdot \rangle"},
    {"name": "分段函数", "code": (r"\begin{cases}" "\n"
                                r"  %sel% & x > 0 \\" "\n"
                                r"  0 & \text{其他}" "\n"
                                r"\end{cases}")},
    {"name": "方程组", "code": (r"\begin{cases}" "\n"
                              r"  x + y = 1 \\" "\n"
                              r"  x - y = 0" "\n"
                              r"\end{cases}")},
    {"name": "行内公式", "code": r"$%sel%$"},
    {"name": "行间公式", "code": "\\[\n  %sel%\n\\]"},
    {"name": "矩阵 / 行列式", "code": "__MATRIX__"},
]


# ------------------------------------------------------------------ 基础片段
def wrap_selection(selected: str, template: str) -> str:
    """把模板里的 %sel% 换成当前选中的内容。"""
    return template.replace("%sel%", selected)


def font_command(family_id: str, math: bool = True) -> Optional[str]:
    table = FONT_FAMILIES if math else TEXT_FAMILIES
    for item in table:
        if item["id"] == family_id:
            return item["command"]
    return None


def apply_font(text: str, family_id: str, math: bool = True) -> str:
    command = font_command(family_id, math)
    if command is None:
        return text
    return f"{command}{{{text or ''}}}"


def apply_size(text: str, size: str) -> str:
    """字号在 LaTeX 里是「声明式」的，需要加组。"""
    if size not in SIZES:
        return text
    return "{\\" + size + " " + (text or "") + "}"


def apply_alignment(text: str, align: str) -> str:
    template = ALIGNMENTS.get(align)
    if template is None:
        return text
    return template % (text or "")


def apply_color(text: str, color: str) -> str:
    """color 形如 #007AFF 或 red。"""
    name = color
    if color.startswith("#") and len(color) == 7:
        # 用 \color[HTML]{RRGGBB} 更省事
        return r"\textcolor[HTML]{" + color[1:].upper() + "}{" + (text or "") + "}"
    return r"\textcolor{" + name + "}{" + (text or "") + "}"


def inline_math(text: str = "") -> str:
    return f"${text}$"


def display_math(text: str = "") -> str:
    return "\\[\n  " + (text or "") + "\n\\]"


# ------------------------------------------------------------------ 矩阵 / 行列式
MATRIX_ENVS = [
    {"id": "pmatrix", "name": "圆括号 ( )", "left": "(", "right": ")"},
    {"id": "bmatrix", "name": "方括号 [ ]", "left": "[", "right": "]"},
    {"id": "vmatrix", "name": "竖线（行列式） | |", "left": "|", "right": "|"},
    {"id": "Vmatrix", "name": "双竖线 ‖ ‖", "left": "‖", "right": "‖"},
    {"id": "Bmatrix", "name": "花括号 { }", "left": "{", "right": "}"},
    {"id": "matrix", "name": "无括号", "left": "", "right": ""},
    {"id": "array", "name": "array（可配列对齐）", "left": "", "right": ""},
]

MATRIX_ALIGNS = [{"id": "r", "name": "右对齐"}, {"id": "c", "name": "居中"},
                 {"id": "l", "name": "左对齐"}]


def matrix_code(cells: Sequence[Sequence[str]], env: str = "pmatrix",
                align: str = "c", math_wrap: bool = True) -> str:
    """把二维格子变成 LaTeX 矩阵代码。

    cells 里可以是数字、符号，也可以直接写 \\frac{1}{2} 这种片段。
    """
    if env not in {item["id"] for item in MATRIX_ENVS}:
        env = "pmatrix"
    align = align if align in ("l", "c", "r") else "c"
    clean_rows: List[List[str]] = []
    for row in cells:
        values = [str(value).strip() for value in row]
        if any(values):
            clean_rows.append(values)
    if not clean_rows:
        clean_rows = [["" for _ in range(2)] for _ in range(2)]

    width = max(len(row) for row in clean_rows)
    lines = []
    for row in clean_rows:
        padded = row + [""] * (width - len(row))
        lines.append(" & ".join(padded))
    body = " \\\\\n".join("  " + line for line in lines)

    if env == "array":
        spec = "{" + align * width + "}"
        code = "\\begin{array}" + spec + "\n" + body + "\n\\end{array}"
    else:
        code = "\\begin{" + env + "}\n" + body + "\n\\end{" + env + "}"
    if math_wrap:
        return display_math(code)
    return code


def determinant_code(cells: Sequence[Sequence[str]], math_wrap: bool = True) -> str:
    return matrix_code(cells, env="vmatrix", math_wrap=math_wrap)


# ------------------------------------------------------------------ 表格 / 列表 / 图片
def tabular_code(rows: Sequence[Sequence[str]], header: bool = True,
                 align: str = "l", booktabs: bool = True) -> str:
    if not rows:
        rows = [["A", "B"], ["1", "2"]]
    width = max(len(row) for row in rows)
    spec = "{" + align * width + "}"
    lines: List[str] = ["\\begin{tabular}" + spec]
    if booktabs:
        lines.append("\\toprule")
    for index, row in enumerate(rows):
        padded = [str(value).strip() for value in row] + [""] * (width - len(row))
        lines.append(" & ".join(padded) + " \\\\")
        if header and index == 0:
            lines.append("\\midrule" if booktabs else "\\hline")
    if booktabs:
        lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    return "\n".join(lines)


def itemize_code(items: Sequence[str], ordered: bool = False) -> str:
    env = "enumerate" if ordered else "itemize"
    body = "\n".join("  \\item " + (item or "") for item in (items or ["第一项", "第二项"]))
    return "\\begin{" + env + "}\n" + body + "\n\\end{" + env + "}"


def figure_code(path: str = "figure.png", width: str = "0.8") -> str:
    name = path.replace("\\", "/")
    return (
        "\\begin{figure}[htbp]\n"
        "  \\centering\n"
        f"  \\includegraphics[width={width}\\textwidth]{{{name}}}\n"
        "  \\caption{图注}\n"
        "  \\label{fig:label}\n"
        "\\end{figure}"
    )


# ------------------------------------------------------------------ 文档组装
def preamble(package_ids: Iterable[str], extra: str = "") -> str:
    wanted = list(package_ids)
    # hyperref 建议最后加载
    lines: List[str] = []
    late: List[str] = []
    for item in PACKAGES:
        if item["id"] not in wanted:
            continue
        line = str(item["line"])
        if item["id"] == "hyperref":
            late.append(line)
        else:
            lines.append(line)
    lines.extend(late)
    if extra.strip():
        lines.append(extra.rstrip())
    return "\n".join(lines)


def build_document(body: str, package_ids: Optional[Iterable[str]] = None,
                   document_class: str = "ctexart",
                   options: str = "a4paper,12pt", extra_preamble: str = "") -> str:
    ids = DEFAULT_PACKAGES if package_ids is None else list(package_ids)
    head = (
        "% 由 Lumina 的公式编辑器生成\n"
        "\\documentclass[" + options + "]{" + document_class + "}\n"
        + preamble(ids, extra_preamble) + "\n"
    )
    return head + "\n\\begin{document}\n\n" + (body or "") + "\n\n\\end{document}\n"


# 只编译「片段」时（预览用）包一层最小文档
def build_preview_document(body: str, package_ids: Optional[Iterable[str]] = None,
                           extra_preamble: str = "") -> str:
    return build_document(body or "\\ ", package_ids, extra_preamble=extra_preamble)


_BLANK_LINES = re.compile(r"\n{3,}")


def tidy(source: str) -> str:
    """轻量整理：连续空行压成两行，去掉行尾空白。"""
    text = "\n".join(line.rstrip() for line in (source or "").splitlines())
    return _BLANK_LINES.sub("\n\n", text)
