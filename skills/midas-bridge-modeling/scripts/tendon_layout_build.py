"""(b) Phase 2：钢束布置生成器（n(站) → 交错锚固 + PROFZ 线形）。
读 tendon_sizer.json 的 n_top(站)（已验证的悬臂束族），按"包络阶梯"定每束锚固区间：
  level k 的束 = n_top≥k 的 X 区间 [Xl(k),Xr(k)]，贴顶纤维(z_from_top=保护层)。
墩区自动检测（n_top 两个峰，X=150 为界分 pier1/pier2 域）。
输出：tendon_generated.json（生成束几何，供 Phase 2b 写 TDNA/TDPL）
      tmp/tendon_generated_vs_template.png（生成 vs 模板叠图，验证几何正确）
铁律：束数回 sizer(弯矩反算)、截面回 SECTIONALL、不手算几何；线形用标定约定 z_from_top=−PROFZ_z。
仅读+算+出图，**不写模型**（写入是 Phase 2b，单列）。
"""
import os, io, contextlib, json, math
from pathlib import Path
HERE = Path(__file__).resolve().parent
TMP = Path("E:/Work/midas-dev/tmp")
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    from midas_civil import MAPI_BASEURL, MAPI_KEY, MidasAPI, NX, Model
MAPI_BASEURL(os.environ["MIDAS_MAPI_BASEURL"]); MAPI_KEY(os.environ["MIDAS_MAPI_KEY"])
NX.user_print = False
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    try: Model.units(force="N", length="MM")
    except Exception: pass
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

COVER_TOP = 235.0       # 贴顶保护层(mm)，取自模板
ANCH_Z = 300.0          # 锚端略低(mm)
SPLIT_X = 150.0         # pier1/pier2 域界(m)

def fetch():
    tdna = MidasAPI("GET", "/db/TDNA", {}).get("TDNA", {})
    elem = MidasAPI("GET", "/db/ELEM", {}).get("ELEM", {})
    node = MidasAPI("GET", "/db/NODE", {}).get("NODE", {})
    nd = {int(k): v["X"] for k, v in node.items()}
    e2n = {int(k): [n for n in v["NODE"] if n != 0] for k, v in elem.items()}
    girder = sorted({e for v in tdna.values() for e in (v.get("ELEM") or [])})
    emidX = {e: sum(nd[n] for n in e2n[e]) / len(e2n[e]) for e in girder if e in e2n}
    return tdna, nd, e2n, girder, emidX

def template_top(tdna, nd, e2n):
    """模板顶束 (X_m, z_from_top) 折线，供叠图。"""
    out = []
    for v in tdna.values():
        nm = str(v.get("NAME", ""))
        if not nm.lower().startswith("top") or not v.get("PROFZ"): continue
        els = v.get("ELEM", []); ins = v.get("INS_ELEM", els[0]); insn = e2n.get(ins, [])
        istart = insn[0] if v.get("INS_PT", "END-I") == "END-I" else insn[-1]
        xs0 = nd[istart]; allx = [nd[n] for e in els for n in e2n.get(e, [])]
        sgn = 1 if abs(xs0 - min(allx)) <= abs(xs0 - max(allx)) else -1
        pts = [((xs0 + sgn * p["PT"][0]) / 1000, -p["PT"][1]) for p in v["PROFZ"]]
        out.append(sorted(pts))
    return out

def elems_between(emidX, xl_mm, xr_mm):
    return sorted(e for e, x in emidX.items() if xl_mm - 1e-6 <= x <= xr_mm + 1e-6)

def gen_family(rows_region, emidX, name_prefix, e2n, nd):
    """对一个墩域：用 n_top 包络阶梯生成束。返回 [{name,x_m,elems,profz_pts,...}]"""
    pts = sorted(((r["X"], r["n_top"]) for r in rows_region), key=lambda t: t[0])
    Nmax = int(round(max(p[1] for p in pts)))
    gens = []
    for k in range(1, Nmax + 1):
        xs_ge = [x for x, n in pts if n >= k - 0.5]
        if not xs_ge: continue
        xl, xr = min(xs_ge), max(xs_ge)
        if xr - xl < 1.0: continue
        els = elems_between(emidX, xl * 1000, xr * 1000)
        if not els: continue
        # PROFZ 折线（贴顶；锚端略低）：x_along 自 xl 起
        L = (xr - xl) * 1000
        profz = [(0.0, -ANCH_Z), (1500.0, -COVER_TOP), (L - 1500.0, -COVER_TOP), (L, -ANCH_Z)]
        gens.append({"name": f"{name_prefix}-{k:02d}", "x_m": [round(xl, 2), round(xr, 2)],
                     "level": k, "elems": [els[0], els[-1]], "n_elem": len(els),
                     "profz_xalong_z": profz,
                     "_plot": [(xl, ANCH_Z), (xl + 1.5, COVER_TOP), (xr - 1.5, COVER_TOP), (xr, ANCH_Z)]})
    return gens

def main():
    tdna, nd, e2n, girder, emidX = fetch()
    rows = json.loads((HERE / "tendon_sizer.json").read_text(encoding="utf-8"))
    # 分域
    r1 = [r for r in rows if r["X"] < SPLIT_X and r["n_top"] > 0.4]
    r2 = [r for r in rows if r["X"] >= SPLIT_X and r["n_top"] > 0.4]
    g1 = gen_family(r1, emidX, "GenTop1", e2n, nd)
    g2 = gen_family(r2, emidX, "GenTop2", e2n, nd)
    gens = g1 + g2
    tmpl = template_top(tdna, nd, e2n)

    # 叠图：生成(绿) vs 模板(红淡)
    fig, ax = plt.subplots(figsize=(16, 5))
    for poly in tmpl:
        ax.plot([p[0] for p in poly], [p[1] for p in poly], color="#c0392b", lw=0.7, alpha=0.35)
    for g in gens:
        xs = [p[0] for p in g["_plot"]]; zs = [p[1] for p in g["_plot"]]
        ax.plot(xs, zs, color="#27ae60", lw=0.8, alpha=0.7)
    ax.plot([], [], color="#c0392b", lw=1.2, label=f"template Top (n={len(tmpl)})")
    ax.plot([], [], color="#27ae60", lw=1.2, label=f"generated (n={len(gens)})")
    ax.invert_yaxis(); ax.grid(alpha=0.3); ax.legend(loc="lower right")
    ax.set_xlabel("Station X (m)"); ax.set_ylabel("Depth below deck top (mm)")
    ax.set_title(f"Generated cantilever tendons (green) vs template Top (red) — pier1 {len(g1)} / pier2 {len(g2)}")
    fig.tight_layout(); fig.savefig(TMP / "tendon_generated_vs_template.png", dpi=110); plt.close(fig)

    for g in gens: g.pop("_plot", None)
    (HERE / "tendon_generated.json").write_text(json.dumps(gens, ensure_ascii=False, indent=1), encoding="utf-8")
    # 汇总
    def span(gs): return (min(g["x_m"][0] for g in gs), max(g["x_m"][1] for g in gs)) if gs else (0, 0)
    print(f"GenTop1: {len(g1)} 束 span{span(g1)} | GenTop2: {len(g2)} 束 span{span(g2)} "
          f"| 模板顶束 {len(tmpl)}")
    print("-> tendon_generated.json + tmp/tendon_generated_vs_template.png")

main()
