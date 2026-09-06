"""(b) Phase 1：钢束布置反算器（从零定束的设计核心：弯矩包络 → 每断面束数）。
方法回 ASPIRE/AASHTO（与规范无关）、取值回 JTG、弯矩回 MIDAS：
  ① demand 弯矩(无预应力) = 恒荷载(CS) + ψ_veh·活载(Vehicle) + ψ_tmp·温度  ——回 MIDAS 结果表
  ② σ_demand(受拉边) = M·c/I（经典梁理论，截面回 SECTIONALL）
  ③ 每断面束数 n = (σ_demand − σ_LIM)/σ_PT,  σ_PT(每束) = P/A + α·P·e·c/I   ——ASPIRE 式
     悬臂束(顶,治墩区负弯矩) / 连续束(底,治跨中正弯矩) 分别算，沿跨给 n(站) 曲线
验证：把反算 n 与现有模板束数(Top/Bot 每族)对比——量级吻合即证反算可信。
仅读+算，不改模型。输出 tendon_sizer_report.md + tmp/tendon_demand_vs_supply.png

★ ASSUME（显式假设，便于审/调）：
  σ_pe 有效预应力(扣损失)=1100MPa(≈0.82σcon=1341.5)；α 束效率=0.85(ASPIRE)；
  顶束保护层 z=235mm、底束距底 290mm（取自本桥模板实测）；
  频遇系数 ψ_veh=0.7 / ψ_tmp=0.8（JTG→code_tables「组合」）；
  σ_LIM=0（全预应力消压，JTG3362 §6.3.1，不允许拉应力）；
  束面积=2635mm²(模板束)→ n 以"模板束当量根数"计，直接对比模板。
"""
import os, io, contextlib, json, re
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

# ---- 参数（见文件头 ASSUME）----
SIG_PE, ALPHA = 1100.0, 0.85
COVER_TOP, COVER_BOT = 235.0, 290.0
PSI_VEH, PSI_TMP = 0.7, 0.8
SIG_LIM = 0.0
TENDON_AREA = 2635.3
P1 = TENDON_AREA * SIG_PE                 # 每束有效预压力 N
FTK = 2.65                                # C50 主梁 ftk(MPa)→ 主拉限值 0.40ftk
T_PRIN = 0.40 * FTK                       # §6.3.3 全预应力现浇 主拉限值(MPa)

def post(tt, keys, cs=False):
    arg = {"TABLE_NAME": "x", "TABLE_TYPE": tt, "NODE_ELEMS": {"KEYS": list(keys)},
           "PARTS": ["PartI", "PartJ"]}
    if cs: arg["OPT_CS"] = True
    r = MidasAPI("POST", "/post/TABLE", {"Argument": arg})
    return r.get("x", {}) if isinstance(r, dict) else {}

def fetch():
    tdna = MidasAPI("GET", "/db/TDNA", {}).get("TDNA", {})
    elem = MidasAPI("GET", "/db/ELEM", {}).get("ELEM", {})
    node = MidasAPI("GET", "/db/NODE", {}).get("NODE", {})
    nd = {int(k): v["X"] for k, v in node.items()}
    e2s = {int(k): v.get("SECT") for k, v in elem.items()}
    e2n = {int(k): [n for n in v["NODE"] if n != 0] for k, v in elem.items()}
    girder = sorted({e for v in tdna.values() for e in (v.get("ELEM") or [])})
    js = {"TABLE_NAME": "S", "TABLE_TYPE": "SECTIONALL",
          "NODE_ELEMS": {"KEYS": girder}, "PARTS": ["PartI"]}
    r = MidasAPI("POST", "/post/TABLE", {"Argument": js})
    H = r["S"]["HEAD"]
    iID, iCzp, iCzm = H.index("ID"), H.index("Czp"), H.index("Czm")
    iA, iI, iAsz = H.index("Area"), H.index("Iyy"), H.index("Asz")
    sect = {int(row[iID]): (float(row[iCzp]), float(row[iCzm]), float(row[iA]), float(row[iI]), float(row[iAsz]))
            for row in r["S"]["DATA"]}
    return tdna, nd, e2s, e2n, sect, girder

def pnorm(p):
    return "J" if "J" in str(p).upper() else "I"   # 归一 PartI/PartJ ↔ I/J ↔ Part I/J

def moments(girder):
    """每 (elem,part归一) 的弯矩分量 My(N·mm)：恒荷载(CS) + Vehicle(max/min) + 温度。"""
    M = {}   # (elem, 'I'|'J') -> dict
    B = 12
    for i in range(0, len(girder), B):
        bt = girder[i:i + B]
        t = post("BEAMFORCE", bt, cs=True)
        if t.get("DATA"):
            H = t["HEAD"]
            iE, iL, iSt, iStep, iP, iM = (H.index("Elem"), H.index("Load"), H.index("Stage"),
                                          H.index("Step"), H.index("Part"), H.index("Moment-y"))
            for row in t["DATA"]:
                e, p, load, my = int(row[iE]), pnorm(row[iP]), row[iL], float(row[iM])
                d = M.setdefault((e, p), {})
                fin = (row[iSt] == "CS16" and "最后" in row[iStep])   # 成桥末态
                if load == "恒荷载":
                    # 施工阶段恒载包络（逐阶段最不利 sag/hog）——边跨 FSMBot 等支架期 demand
                    d["dead_sag_c"] = max(d.get("dead_sag_c", -9e99), my)
                    d["dead_hog_c"] = min(d.get("dead_hog_c", 9e99), my)
                    if fin: d["dead"] = my
                elif load == "徐变二次" and fin: d["creep2"] = my
                elif load == "收缩二次" and fin: d["shrink2"] = my
        t2 = post("BEAMFORCE", bt)
        if t2.get("DATA"):
            H = t2["HEAD"]; iE, iL, iP, iM = H.index("Elem"), H.index("Load"), H.index("Part"), H.index("Moment-y")
            for row in t2["DATA"]:
                nm = row[iL]
                if nm in ("Vehicle(max)", "Vehicle(min)", "SysTempRise", "SysTempDrop",
                          "GradTempPos", "GradTempNeg"):
                    M.setdefault((int(row[iE]), pnorm(row[iP])), {})[nm] = float(row[iM])
    return M

def shears(girder):
    """每 (elem,part归一) 的频遇组合最不利剪力 |Vz|(N)。用于 §6.3.3 主拉轴压反算。
    组合结果名带 (max)/(min) 后缀；取两者绝对值最大。"""
    V = {}
    B = 12
    for i in range(0, len(girder), B):
        bt = girder[i:i + B]
        t = post("BEAMFORCE", bt)
        if not t.get("DATA"): continue
        H = t["HEAD"]; iE, iL, iP, iV = H.index("Elem"), H.index("Load"), H.index("Part"), H.index("Shear-z")
        for row in t["DATA"]:
            if row[iL] in ("SLS-Frequent(max)", "SLS-Frequent(min)"):
                k = (int(row[iE]), pnorm(row[iP]))
                V[k] = max(V.get(k, 0.0), abs(float(row[iV])))
    return V

def main():
    tdna, nd, e2s, e2n, sect, girder = fetch()
    M = moments(girder)
    V = shears(girder)
    # 模板实际束数（每站过断面束数，按顶/底）
    def tendon_pts(v):
        els = v.get("ELEM", [])
        ins = v.get("INS_ELEM", els[0]); insn = e2n.get(ins, [])
        istart = insn[0] if v.get("INS_PT", "END-I") == "END-I" else insn[-1]
        xs0 = nd[istart]; allx = [nd[n] for e in els for n in e2n.get(e, [])]
        sgn = 1 if abs(xs0 - min(allx)) <= abs(xs0 - max(allx)) else -1
        xr = sorted(xs0 + sgn * p["PT"][0] for p in v.get("PROFZ", []))
        # 判顶/底：用首点 z_from_top vs 该处 Czp
        z0 = -v["PROFZ"][0]["PT"][1]
        return xr[0], xr[-1], z0
    actual = []   # (xmin,xmax,fam)
    for v in tdna.values():
        if not v.get("PROFZ"): continue
        x0, x1, z0 = tendon_pts(v)
        nm = str(v.get("NAME", ""))
        fam = "top" if nm.lower().startswith("top") else "bot"
        actual.append((x0 / 1000, x1 / 1000, fam))

    rows = []
    for e in girder:
        sid = e2s.get(e); pr = sect.get(sid)
        if not pr: continue
        Czp, Czm, A, I, Asz = pr
        for part, nodeidx in (("I", 0), ("J", 1)):
            d = M.get((e, part))
            if not d or "dead" not in d: continue
            X = nd[e2n[e][nodeidx]] / 1000
            dead = d.get("dead", 0.0)
            cr = d.get("creep2", 0.0) + d.get("shrink2", 0.0)   # 徐变/收缩二次=重分布(真实内力,削墩顶/增跨中)
            perm = dead + cr                                    # 永久 demand(无预应力一次/二次)
            vmax = d.get("Vehicle(max)", 0.0); vmin = d.get("Vehicle(min)", 0.0)
            tpos = max(d.get("SysTempRise", 0.0), d.get("GradTempPos", 0.0), 0.0)
            tneg = min(d.get("SysTempDrop", 0.0), d.get("GradTempNeg", 0.0), 0.0)
            # 成桥频遇 demand（无预应力）
            M_hog_f = perm + PSI_VEH * vmin + PSI_TMP * tneg     # 最负→顶受拉
            M_sag_f = perm + PSI_VEH * vmax + PSI_TMP * tpos     # 最正→底受拉
            # 施工阶段恒载包络（支架/悬臂期，无预应力）
            sag_c = d.get("dead_sag_c", -9e99); hog_c = d.get("dead_hog_c", 9e99)
            # 取成桥态与施工期中更不利者
            M_hog = min(M_hog_f, hog_c if hog_c < 8e99 else M_hog_f)
            M_sag = max(M_sag_f, sag_c if sag_c > -8e99 else M_sag_f)
            # 受拉边 demand 应力（拉+）
            sig_top = -M_hog * Czp / I     # 顶纤维 (M_hog<0 → 正=拉)
            sig_bot = M_sag * Czm / I      # 底纤维 (M_sag>0 → 正=拉)
            # 每束在该纤维提供的压应力（ASPIRE σPT）
            e_top = Czp - COVER_TOP
            e_bot = Czm - COVER_BOT
            pPT_top = P1 / A + ALPHA * P1 * e_top * Czp / I
            pPT_bot = P1 / A + ALPHA * P1 * e_bot * Czm / I
            n_top = max(0.0, (sig_top - SIG_LIM) / pPT_top)
            # 纵向束数 = 弯曲消压反算（顶=hogging、底=sagging）。这是纵向束的正确口径。
            n_bot = max(0.0, (sig_bot - SIG_LIM) / pPT_bot)
            # §6.3.3 主拉为**诊断列**（非纵向束数）：剪力诱发的主拉超限须由**竖向预应力**治
            #   （design_check.py A，反算 σcy），而非堆纵向束——实证：堆纵向束先把持久压压爆(持久压 util>1)、σtp 仍不退。
            Vz = V.get((e, part), 0.0)
            tau = abs(Vz) / Asz if Asz else 0.0
            sig_pc_prin = max(0.0, tau * tau / T_PRIN - T_PRIN)   # 满足 σtp≤0.4ftk 所需轴向预压(MPa)，σcx_load≈0
            rows.append({"e": e, "part": part, "X": round(X, 2),
                         "M_hog": round(M_hog / 1e9, 1), "M_sag": round(M_sag / 1e9, 1),
                         "sig_top": round(sig_top, 2), "sig_bot": round(sig_bot, 2),
                         "tau": round(tau, 3), "sig_pc_prin": round(sig_pc_prin, 2),
                         "n_top": round(n_top, 1), "n_bot": round(n_bot, 1)})
    rows.sort(key=lambda r: r["X"])

    # 模板供给：每站过断面束数
    def supply(x, fam):
        return sum(1 for x0, x1, f in actual if f == fam and x0 - 1e-6 <= x <= x1 + 1e-6)
    for r in rows:
        r["sup_top"] = supply(r["X"], "top"); r["sup_bot"] = supply(r["X"], "bot")

    # ---- 图：demand 需求 vs 模板供给 ----
    Xs = [r["X"] for r in rows]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(15, 8), sharex=True)
    a1.plot(Xs, [r["n_top"] for r in rows], "r-", label="required (sizer)")
    a1.plot(Xs, [r["sup_top"] for r in rows], "k--", label="template supply")
    a1.set_ylabel("Top (cantilever) tendons"); a1.legend(); a1.grid(alpha=0.3)
    a1.set_title("Tendon demand (sizer) vs template supply — Top/cantilever (over piers)")
    a2.plot(Xs, [r["n_bot"] for r in rows], "b-", label="required (sizer)")
    a2.plot(Xs, [r["sup_bot"] for r in rows], "k--", label="template supply")
    a2.set_ylabel("Bottom (continuity) tendons"); a2.set_xlabel("Station X (m)")
    a2.legend(); a2.grid(alpha=0.3)
    a2.set_title("Bottom/continuity (at midspans)")
    fig.tight_layout(); fig.savefig(TMP / "tendon_demand_vs_supply.png", dpi=110); plt.close(fig)

    # ---- 报告 ----
    n_top_max = max(rows, key=lambda r: r["n_top"])
    n_bot_max = max(rows, key=lambda r: r["n_bot"])
    sup_top_max = max(r["sup_top"] for r in rows)
    sup_bot_max = max(r["sup_bot"] for r in rows)
    L = ["# 钢束布置反算（Phase 1：弯矩包络→束数）  方法 ASPIRE×JTG，弯矩回 MIDAS",
         f"参数：σpe={SIG_PE} α={ALPHA} 顶保护层={COVER_TOP} 底={COVER_BOT} ψveh={PSI_VEH} ψtmp={PSI_TMP} "
         f"σLIM={SIG_LIM}(全预应力消压) 束面积={TENDON_AREA}（详见脚本头 ASSUME）",
         "",
         "## 验证：反算峰值 vs 模板供给",
         "| 族 | 反算峰值根数 | 位置X(m) | 模板峰值根数 | 比值 |",
         "|---|---|---|---|---|",
         f"| 顶/悬臂 | {n_top_max['n_top']:.1f} | {n_top_max['X']:.1f} | {sup_top_max} | {n_top_max['n_top']/max(sup_top_max,1):.2f} |",
         f"| 底/连续 | {n_bot_max['n_bot']:.1f} | {n_bot_max['X']:.1f} | {sup_bot_max} | {n_bot_max['n_bot']/max(sup_bot_max,1):.2f} |",
         "",
         "## 讨论（诚实；含施工阶段恒载包络 + Phase 2b 实证订正）",
         "- **顶/悬臂束：方法验证通过**。反算峰值≈模板（比值 1.15），taper 形状吻合（墩顶 38≈38、向跨中归零）。Phase 2b greenfield 写入达 baseline 同级。",
         "  顶束由**恒载墩顶 hogging 主导**（几何驱动、量级大、确定）→ 反算最可靠。略偏大因 σLIM=0(全消压)偏保守。",
         "- **底/连续束：纵向束数按弯曲消压反算（此口径正确）**，峰值≈9（边跨）/4.5（midspan）« 模板 14~18。",
         "  **★Phase 2b 实证订正**（曾误读为「模板超配」）：把底束真减半 greenfield → **侧跨 SLS 失效**（主拉 σtp util 3.09、消压 +5.75）。",
         "  但根因**不是纵向底束欠配**：① 试把主拉需求折进纵向底束(n_bot=max(弯曲,主拉轴压)) → midspan **持久压压爆(util 1.37)** 而 σtp 仍 3.50 → ",
         "  **证伪「堆纵向束治 σtp」**；② σtp(主拉)的正解是**竖向预应力**（design_check.py A：反算 σcy，本例 e1 需 σcy≥9.0MPa，3φ15.2@100mm 回代 σtp 3.27→0.83 ✓）。",
         "  → **结论**：纵向底束 = 弯曲消压口径（≈9/4.5 正确）；**主拉 σtp 属竖向预应力的活**，与纵向束数无关。模板重底束是**用纵向束顺带垫高轴压、低效地掩盖 σtp**，非真超配亦非纵向欠配。",
         "- **sig_pc_prin 诊断列**：满足 σtp≤0.4ftk 所需轴向预压(MPa)，σcx_load≈0。**仅作竖向预应力定量的诊断**，不并入 n_bot。",
         "",
         "## 沿跨 demand→束数（每 2m 抽样；sig_pc_prin=主拉所需轴压→竖向预应力，非纵向束）",
         "| X(m) | M_hog | M_sag | σ_top拉 | σ_bot拉 | τ | σpc主拉 | n_top需 | n_bot需 | 模板顶 | 模板底 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    seen = set()
    for r in rows:
        xk = round(r["X"] / 6) * 6
        if xk in seen: continue
        seen.add(xk)
        L.append(f"| {r['X']:.1f} | {r['M_hog']:.0f} | {r['M_sag']:.0f} | {r['sig_top']:.2f} | {r['sig_bot']:.2f} | "
                 f"{r.get('tau',0):.2f} | {r.get('sig_pc_prin',0):.1f} | "
                 f"{r['n_top']:.1f} | {r['n_bot']:.1f} | {r['sup_top']} | {r['sup_bot']} |")
    L.append("\n> 图：tmp/tendon_demand_vs_supply.png（需求曲线 vs 模板供给，顶/底分图）。")
    L.append("> Phase 2/2b：据 n(站) 生成束→greenfield 写入→重分析→S10；主拉 σtp 走竖向预应力(design_check)。")
    (HERE / "tendon_sizer_report.md").write_text("\n".join(L), encoding="utf-8")
    (HERE / "tendon_sizer.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"n_top_max={n_top_max['n_top']:.1f}@{n_top_max['X']} (tmpl {sup_top_max}); "
          f"n_bot_max={n_bot_max['n_bot']:.1f}@{n_bot_max['X']} (tmpl {sup_bot_max})")

main()
