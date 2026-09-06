"""Extract the public API surface of the installed midas-civil package via AST.
No import / no network / no MIDAS connection -- faithful to source.
Output: a Markdown reference of public classes + their public methods (with
signatures and first docstring line), grouped by module.
"""
import ast
import os
import sys

# midas-civil 安装位置（如包升级或换机，改这里）。也可用环境变量 MIDAS_CIVIL_PKG 覆盖。
PKG = os.environ.get("MIDAS_CIVIL_PKG", r"E:/anaconda3/Lib/site-packages/midas_civil")
# 输出固定写到本脚本同级 ../reference/midas_civil_api.md（与运行目录解耦）
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "reference", "midas_civil_api.md")

# Module -> human-facing group title (modeling order)
MODULE_TITLE = {
    "_mapi": "连接与底层接口 (MAPI / MidasAPI)",
    "_model": "项目与单位 (Model)",
    "_node": "节点 (Node)",
    "_element": "单元 (Element)",
    "_section": "截面 (Section / Offset)",
    "_material": "材料 (Material / 时间依存)",
    "_thickness": "板厚 (Thickness)",
    "_boundary": "边界条件 (Boundary)",
    "_BoundaryChangeAssignment": "边界变更 (施工阶段)",
    "_group": "组 (Group)",
    "_load": "静力荷载 (Load / Load_Case)",
    "_temperature": "温度荷载 (Temperature)",
    "_settlement": "沉降 (Settlement)",
    "_tendon": "预应力钢束 (Tendon)",
    "_movingload": "移动荷载 / 车道 / 车辆 (MovingLoad)",
    "_construction": "施工阶段 (CS)",
    "_loadcomb": "荷载组合 (LoadCombination)",
    "_analysiscontrol": "分析控制 (AnalysisControl)",
    "_responseSpectrum": "反应谱 (RS)",
    "_view": "视图与结果图形 (View / ResultGraphic)",
    "_result_table": "结果读取 (Result / TableOptions)",
    "_utils": "工具函数 (utils / getID ...)",
}

# Modules to skip (internal/experimental/dupes)
SKIP_FILES = {"_result_test.py", "_view_trial.py", "_visualise.py",
              "_pscSS copy.py", "__init__.py"}


def sig(node):
    """Render a def's argument signature, dropping self/cls."""
    a = node.args
    parts = []
    posonly = getattr(a, "posonlyargs", [])
    args = posonly + a.args
    defaults = a.defaults
    ndef = len(defaults)
    npos = len(args)
    for i, arg in enumerate(args):
        if arg.arg in ("self", "cls"):
            continue
        di = i - (npos - ndef)
        if di >= 0:
            try:
                d = ast.unparse(defaults[di])
                parts.append(f"{arg.arg}={d}")
            except Exception:
                parts.append(arg.arg)
        else:
            parts.append(arg.arg)
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    for i, arg in enumerate(a.kwonlyargs):
        d = a.kw_defaults[i]
        if d is not None:
            try:
                parts.append(f"{arg.arg}={ast.unparse(d)}")
            except Exception:
                parts.append(arg.arg)
        else:
            parts.append(arg.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    return "(" + ", ".join(parts) + ")"


def doc1(node):
    d = ast.get_docstring(node)
    if not d:
        return ""
    line = d.strip().splitlines()[0].strip()
    return line[:160]


def is_static_or_class(fn):
    for dec in fn.decorator_list:
        n = getattr(dec, "id", None) or getattr(dec, "attr", None)
        if n in ("staticmethod", "classmethod"):
            return n
    return None


def collect(path):
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    classes = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods = []
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and not sub.name.startswith("_"):
                    kind = is_static_or_class(sub)
                    methods.append((sub.name, sig(sub), doc1(sub), kind))
                # nested public classes (e.g. Element.Beam, Section.PSC)
                elif isinstance(sub, ast.ClassDef) and not sub.name.startswith("_"):
                    inits = ""
                    nd = ""
                    for s2 in sub.body:
                        if isinstance(s2, ast.FunctionDef) and s2.name == "__init__":
                            inits = sig(s2)
                            nd = doc1(s2)
                    methods.append((sub.name, inits or "(...)", nd or doc1(sub), "nested-class"))
            cdoc = doc1(node)
            classes.append((node.name, cdoc, methods))
    return classes


def main():
    # Build module -> file map (top-level .py + _section/*.py merged under _section)
    files = []
    for fn in sorted(os.listdir(PKG)):
        if fn.endswith(".py") and fn not in SKIP_FILES:
            files.append((fn[:-3], os.path.join(PKG, fn)))
    sect_dir = os.path.join(PKG, "_section")
    sect_files = []
    if os.path.isdir(sect_dir):
        for fn in sorted(os.listdir(sect_dir)):
            if fn.endswith(".py") and fn not in SKIP_FILES:
                sect_files.append(os.path.join(sect_dir, fn))

    out = []
    out.append("# midas-civil 官方 Python 包 API 说明（自动提取）\n")
    out.append("> **来源**：已安装的 `midas-civil v1.6.6`"
               "（`E:/anaconda3/Lib/site-packages/midas_civil/`），"
               "由 `extract_midas_api.py` 用 AST 静态提取**公开类与公开方法的真实签名**，"
               "不靠记忆、不联网、不连 MIDAS。\n")
    out.append("> **铁律**：生成器调用本包时，方法名与参数以本文件为准；"
               "底层端点与实测陷阱见 [`midas_automation_reference.md`](midas_automation_reference.md)，"
               "建模后逐表校验见 [`midas_model_validation_tables.md`](midas_model_validation_tables.md)。\n")
    out.append("> 注：`staticmethod`/`classmethod` 已标注；嵌套类（如 `Element.Beam`、"
               "`Section.PSC`）以 `ClassName.Nested(...)` 形式列出，括号为其构造参数。\n")

    # order modules by MODULE_TITLE first, then rest
    ordered = [m for m in MODULE_TITLE if any(f[0] == m for f in files)]
    rest = [f[0] for f in files if f[0] not in MODULE_TITLE]
    file_map = dict(files)

    total_cls = 0
    total_meth = 0

    def emit_classes(classes):
        nonlocal total_cls, total_meth
        for cname, cdoc, methods in classes:
            total_cls += 1
            head = f"### `{cname}`"
            if cdoc:
                head += f" — {cdoc}"
            out.append(head + "\n")
            if not methods:
                out.append("_(无公开方法/为数据类)_\n")
            for mname, msig, mdoc, kind in methods:
                total_meth += 1
                tag = ""
                if kind == "staticmethod":
                    tag = " _[static]_"
                elif kind == "classmethod":
                    tag = " _[class]_"
                elif kind == "nested-class":
                    tag = " _[嵌套类]_"
                line = f"- `{cname}.{mname}{msig}`{tag}"
                if mdoc:
                    line += f" — {mdoc}"
                out.append(line)
            out.append("")

    for m in ordered:
        title = MODULE_TITLE.get(m, m)
        classes = collect(file_map[m])
        if m == "_section":
            # merge section subfiles
            for sf in sect_files:
                classes += collect(sf)
        if not classes:
            continue
        out.append(f"\n## {title}\n")
        emit_classes(classes)

    if rest:
        out.append("\n## 其他模块\n")
        for m in rest:
            classes = collect(file_map[m])
            if classes:
                out.append(f"\n### 模块 `{m}`\n")
                emit_classes(classes)

    out.insert(4, f"\n> **规模**：本次提取公开类 **{total_cls}** 个、公开方法/嵌套类 **{total_meth}** 项。\n")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"OK -> {OUT}")
    print(f"classes={total_cls} methods={total_meth}")


if __name__ == "__main__":
    main()
