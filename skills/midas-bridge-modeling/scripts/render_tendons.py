"""钢束数据示意验证器 + 调整决策助手（解决 MIDAS 全图"线太多没法判断"，并把"看见"接到"怎么调"）。
读 TDNA/TDNT/TDPL + SECTIONALL（皆无需分析）。

无参数：全桥概览 + 分组小多图 + 名实校验表
  python render_tendons.py
按族过滤（少量束时图上自动标名，图↔JSON 可对应）：
  python render_tendons.py Top
★调整决策：按临界截面查询"哪些束过此断面、各束对顶/底纤维的预压杠杆"：
  python render_tendons.py --at E51        # 元素 51（S10 σtp 临界）
  python render_tendons.py --at X215 --fiber top   # 里程 215m，临界纤维=顶（消压）
  → 列过该断面的束(名/贴顶底/该处偏心/+面积对顶底纤维的 Δσ)，排序给"增哪族/勿加哪族"建议
    + tmp/tendon_at_<...>.png 标注图（竖线标断面、过断面束加粗标名）

约定（截图标定）：z_from_top=−PROFZ_z（距梁顶、向下为正）；顶纤维=0、底=H=Czp+Czm、形心=Czp。
σ 拉+压−。Δσ 用名义 P=area·σcon（未计损失，仅供"调哪根/朝哪向"排序，量级由 design_check 反算）。
"""
import os, io, contextlib, json, sys, re
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

AREA_PER_STRAND = 140.0   # mm²，1×7 φ15.2（仅估股数，信息性）

# ---- 参数解析 ----
argv = sys.argv[1:]
at_tok = fiber = flt = None
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--at": at_tok = argv[i + 1]; i += 2
    elif a == "--fiber": fiber = argv[i + 1].lower(); i += 2
    elif not a.startswith("--"): flt = a; i += 1
    else: i += 1

def fetch():
    tdna = MidasAPI("GET", "/db/TDNA", {}).get("TDNA", {})
    tdnt = MidasAPI("GET", "/db/TDNT", {}).get("TDNT", {})
    tdpl = MidasAPI("GET", "/db/TDPL", {}).get("TDPL", {})
    elem = MidasAPI("GET", "/db/ELEM", {}).get("ELEM", {})
    node = MidasAPI("GET", "/db/NODE", {}).get("NODE", {})
    nd = {int(k): (v["X"], v["Z"]) for k, v in node.items()}
    e2s = {int(k): v.get("SECT") for k, v in elem.items()}
    e2n = {int(k): [n for n in v["NODE"] if n != 0] for k, v in elem.items()}
    girder = sorted({e for v in tdna.values() for e in (v.get("ELEM") or [])})
    js = {"TABLE_NAME": "S", "TABLE_TYPE": "SECTIONALL",
          "NODE_ELEMS": {"KEYS": girder}, "PARTS": ["PartI"]}
    r = MidasAPI("POST", "/post/TABLE", {"Argument": js})
    H = r["S"]["HEAD"]
    iID, iCzp, iCzm = H.index("ID"), H.index("Czp"), H.index("Czm")
    iA, iI = H.index("Area"), H.index("Iyy")
    sect = {}
    for row in r["S"]["DATA"]:
        try: sect[int(row[iID])] = (float(row[iCzp]), float(row[iCzm]), float(row[iA]), float(row[iI]))
        except Exception: pass
    return tdna, tdnt, tdpl, nd, e2s, e2n, sect, girder

def area_of(tdnt, prop):
    p = tdnt.get(str(prop)) or tdnt.get(prop)
    return float(p.get("AREA", 0.0)) if p else 0.0

def sigcon_of(tdpl, tid):
    p = tdpl.get(str(tid))
    if not p: return None
    for it in p.get("ITEMS", []):
        for key in ("END", "BEGIN"):
            v = it.get(key)
            if v: return float(v)
    return None

def station_map(v, nd, e2n):
    els = v.get("ELEM", [])
    ins = v.get("INS_ELEM", els[0] if els else None)
    insn = e2n.get(ins, [])
    istart = insn[0] if v.get("INS_PT", "END-I") == "END-I" else insn[-1]
    xstart = nd[istart][0]
    xs = [nd[n][0] for e in els for n in e2n.get(e, [])]
    sgn = 1 if abs(xstart - min(xs)) <= abs(xstart - max(xs)) else -1
    return xstart, sgn

def interp_z(pts_m, x_m):
    """pts_m: [(X_m, zft_mm)]; 线性插值 zft@x_m；越界返回 None。"""
    p = sorted(pts_m, key=lambda t: t[0])
    if x_m < p[0][0] - 1e-6 or x_m > p[-1][0] + 1e-6: return None
    for (x0, z0), (x1, z1) in zip(p, p[1:]):
        if x0 - 1e-6 <= x_m <= x1 + 1e-6:
            if x1 == x0: return z0
            return z0 + (z1 - z0) * (x_m - x0) / (x1 - x0)
    return p[-1][1]

def main():
    tdna, tdnt, tdpl, nd, e2s, e2n, sect, girder = fetch()
    eHC = {e: (sect[e2s[e]][0] + sect[e2s[e]][1], sect[e2s[e]][0])
           for e in girder if e2s.get(e) in sect}
    emidX = {e: sum(nd[n][0] for n in e2n[e]) / len(e2n[e]) for e in girder if e in e2n}
    env = sorted(((emidX[e], eHC[e][0], eHC[e][1]) for e in girder if e in eHC and e in emidX),
                 key=lambda t: t[0])
    envX = [t[0] / 1000 for t in env]; envH = [t[1] for t in env]; envCz = [t[2] for t in env]

    def H_at(xmm):
        b = min(env, key=lambda t: abs(t[0] - xmm)); return b[1], b[2]

    tendons = []
    for tid, v in sorted(tdna.items(), key=lambda x: int(x[0])):
        nm = str(v.get("NAME", ""))
        if flt and not nm.startswith(flt): continue
        prof = v.get("PROFZ", [])
        if not prof: continue
        xstart, sgn = station_map(v, nd, e2n)
        pts = [(xstart + sgn * p["PT"][0], -p["PT"][1]) for p in prof]
        zfts = [p[1] for p in pts]; Xs = [p[0] for p in pts]
        Hmid, Czmid = H_at(sum(Xs) / len(Xs)); zmean = sum(zfts) / len(zfts)
        fam = "顶" if zmean < Czmid else "底"
        grp = re.sub(r"-\d+$", "", nm)
        expect = "顶" if nm.lower().startswith("top") else ("底" if re.match(r"(?i)(bot|fsmbot|addbot)", nm) else "?")
        tendons.append({
            "id": int(tid), "name": nm, "group": grp, "tdn_grup": v.get("TDN_GRUP"),
            "elems": [min(v.get("ELEM", [0])), max(v.get("ELEM", [0]))],
            "x_m": [round(min(Xs) / 1000, 2), round(max(Xs) / 1000, 2)],
            "zft_min": round(min(zfts), 1), "zft_max": round(max(zfts), 1),
            "H_mid": round(Hmid, 0), "ecc_mean": round(Czmid - zmean, 1),
            "fam": fam, "expect": expect, "name_vs_geom": "OK" if expect in ("?", fam) else "MISMATCH",
            "area": round(area_of(tdnt, v.get("TDN_PROP")), 1),
            "strands_est": round(area_of(tdnt, v.get("TDN_PROP")) / AREA_PER_STRAND, 1),
            "sigcon": sigcon_of(tdpl, tid),
            "_pts": [(X / 1000, zft) for X, zft in pts],
        })

    # ============ ★ --at：临界截面调整决策 ============
    if at_tok:
        m = re.match(r"(?i)^e(?:lem)?(\d+)$", at_tok)
        if m:
            el = int(m.group(1)); st_mm = emidX.get(el)
            where = f"E{el} @ X={st_mm/1000:.2f}m"
        else:
            xv = float(re.sub(r"(?i)^x", "", at_tok))
            st_mm = xv * 1000 if xv < 1000 else xv
            where = f"X={st_mm/1000:.2f}m"
        st_m = st_mm / 1000
        el_at = min(emidX, key=lambda e: abs(emidX[e] - st_mm))
        Czp, Czm, A, I = sect[e2s[el_at]]
        rows = []
        for t in tendons:
            z = interp_z(t["_pts"], st_m)
            if z is None: continue
            e = Czp - z                       # 偏心(+ = 形心以上)
            P = (t["area"] or 0) * (t["sigcon"] or 0)   # 名义预压力 N
            sig_top = -(P / A) - (P * e * Czp) / I      # MPa，压−
            sig_bot = -(P / A) + (P * e * Czm) / I
            rows.append({**{k: t[k] for k in ("name", "group", "fam", "area", "sigcon")},
                         "z_at": round(z, 1), "e_at": round(e, 1),
                         "dSig_top": round(sig_top, 3), "dSig_bot": round(sig_bot, 3), "_pts": t["_pts"]})
        crit = fiber or "top"
        key = "dSig_top" if crit == "top" else "dSig_bot"
        rows.sort(key=lambda r: r[key])    # 最负(最增压)在前
        L = [f"# 临界截面调整决策 @ {where}（截面 {el_at}：A={A:.3e}mm² I={I:.3e}mm⁴ Czp={Czp:.0f} Czm={Czm:.0f}）",
             f"过此断面 {len(rows)} 束。dSig=名义 P 在该纤维产生的应力(压−,MPa)；临界纤维=**{crit}**，越负越增压(越能治拉/消压)。",
             "",
             "| 束 | 贴 | 该处z(mm) | 偏心e(mm) | dSig_顶 | dSig_底 | 面积 |",
             "|---|---|---|---|---|---|---|"]
        for r in rows:
            L.append(f"| {r['name']} | {r['fam']} | {r['z_at']:.0f} | {r['e_at']:+.0f} | "
                     f"{r['dSig_top']:+.3f} | {r['dSig_bot']:+.3f} | {r['area']:.0f} |")
        help_f = [r for r in rows if r[key] < 0]
        hurt_f = [r for r in rows if r[key] > 0]
        fam_help = sorted({r["group"] for r in help_f})
        fam_hurt = sorted({r["group"] for r in hurt_f})
        L += ["", f"**建议（临界纤维={crit}）**：",
              f"- 增压(治拉/消压)→ **增大/新增**这些族：{', '.join(fam_help) or '（无）'}（dSig_{crit}<0）。",
              f"- ⚠ **勿加**：{', '.join(fam_hurt) or '（无）'}（dSig_{crit}>0，会把临界纤维拉得更狠——即 e2 负优化机理）。",
              f"- 量级（加多少面积/根数）由 `design_check.py` 反算 ΔP=Δσ/(1/A+e·c/I)。"]
        (HERE / "tendon_section_query.md").write_text("\n".join(L), encoding="utf-8")
        # 标注图
        fig, ax = plt.subplots(figsize=(16, 5))
        ax.plot(envX, [0]*len(envX), color="0.4", lw=1.2, ls="--")
        ax.plot(envX, envH, color="0.4", lw=1.2); ax.plot(envX, envCz, color="0.7", lw=0.8, ls=":")
        ax.axvline(st_m, color="green", lw=1.5, ls="-.", label=f"section {where}")
        cross = {r["name"] for r in rows}
        for t in tendons:
            xs = [p[0] for p in t["_pts"]]; zs = [p[1] for p in t["_pts"]]
            on = t["name"] in cross
            c = "#c0392b" if t["fam"] == "顶" else "#2471a3"
            ax.plot(xs, zs, color=c, lw=2.0 if on else 0.5, alpha=0.9 if on else 0.2)
        # 标过断面束名（去重族，避免叠字）
        seen = set()
        for r in rows:
            if r["group"] in seen: continue
            seen.add(r["group"])
            z = interp_z(r["_pts"], st_m)
            ax.annotate(r["group"], (st_m, z), fontsize=8, color="black",
                        xytext=(6, 0), textcoords="offset points", va="center")
        ax.invert_yaxis(); ax.grid(alpha=0.3); ax.legend(loc="lower right", fontsize=8)
        ax.set_xlabel("Station X (m)"); ax.set_ylabel("Depth below deck top (mm)")
        ax.set_title(f"Tendons crossing {where} (bold=crossing)  critical fiber={crit}")
        outp = TMP / f"tendon_at_{re.sub(r'[^0-9A-Za-z]', '', at_tok)}.png"
        fig.tight_layout(); fig.savefig(outp, dpi=110); plt.close(fig)
        print(f"AT {where}: {len(rows)} tendons cross; help={fam_help} hurt={fam_hurt}")
        print(f"  -> tendon_section_query.md + {outp.name}")

    # ---------- 概览图（过滤少量时标名→图↔JSON 可对应）----------
    label = flt is not None and len(tendons) <= 24
    fig, ax = plt.subplots(figsize=(16, 5))
    ax.plot(envX, [0]*len(envX), color="0.4", lw=1.2, ls="--", label="Top fiber (deck)")
    ax.plot(envX, envH, color="0.4", lw=1.2, label="Bottom fiber")
    ax.plot(envX, envCz, color="0.7", lw=0.8, ls=":", label="Centroid")
    for t in tendons:
        xs = [p[0] for p in t["_pts"]]; zs = [p[1] for p in t["_pts"]]
        c = "#c0392b" if t["fam"] == "顶" else "#2471a3"
        ax.plot(xs, zs, color=c, lw=1.6 if t["name_vs_geom"] == "MISMATCH" else 0.7, alpha=0.55)
        if label:
            ax.annotate(t["name"], (xs[0], zs[0]), fontsize=6, color=c,
                        xytext=(-2, 0), textcoords="offset points", ha="right", va="center")
    ax.invert_yaxis(); ax.set_xlabel("Station X (m)"); ax.set_ylabel("Depth below deck top (mm)")
    ax.set_title(f"Tendon elevation schematic ({len(tendons)} tendons)  red=Top  blue=Bottom"
                 + (f"  [filter:{flt}]" if flt else ""))
    ax.legend(loc="lower right", fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(TMP / "tendon_schematic_all.png", dpi=110); plt.close(fig)

    # ---------- 分组小多图 ----------
    groups = {}
    for t in tendons: groups.setdefault(t["group"], []).append(t)
    gkeys = sorted(groups); n = len(gkeys); cols = 2; rows_ = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_, cols, figsize=(15, 2.6 * rows_), squeeze=False)
    for i, gk in enumerate(gkeys):
        ax = axes[i // cols][i % cols]
        ax.plot(envX, [0]*len(envX), color="0.5", lw=1, ls="--")
        ax.plot(envX, envH, color="0.5", lw=1); ax.plot(envX, envCz, color="0.8", lw=0.7, ls=":")
        mism = 0
        for t in groups[gk]:
            xs = [p[0] for p in t["_pts"]]; zs = [p[1] for p in t["_pts"]]
            ax.plot(xs, zs, color="#c0392b" if t["fam"] == "顶" else "#2471a3", lw=0.9, alpha=0.7)
            if t["name_vs_geom"] == "MISMATCH": mism += 1
        ax.invert_yaxis(); ax.grid(alpha=0.3)
        fam_en = "Top" if groups[gk][0]['fam'] == "顶" else "Bot"
        ax.set_title(f"{gk}  (n={len(groups[gk])}, {fam_en}" + (f", MISMATCH x{mism}" if mism else "") + ")", fontsize=9)
    for j in range(n, rows_ * cols): axes[j // cols][j % cols].axis("off")
    fig.tight_layout(); fig.savefig(TMP / "tendon_by_group.png", dpi=110); plt.close(fig)

    # ---------- 报告 ----------
    for t in tendons: t.pop("_pts", None)
    (HERE / "tendon_geom.json").write_text(json.dumps(tendons, ensure_ascii=False, indent=1), encoding="utf-8")
    mis = [t for t in tendons if t["name_vs_geom"] == "MISMATCH"]
    L = ["# 钢束几何示意 / 名实校验（读 TDNA + SECTIONALL，无需分析）",
         f"共 {len(tendons)} 束；z_from_top=距梁顶(mm)，顶纤维=0、底纤维=H、形心=Czp。",
         f"\n**名实校验**：{'⚠ 有 '+str(len(mis))+' 束名实不符（图中加粗）' if mis else '✓ 全部名实相符'}"]
    for t in mis:
        L.append(f"  - {t['name']}: 名义 {t['expect']} 但几何 {t['fam']}（z {t['zft_min']}~{t['zft_max']} / H {t['H_mid']:.0f}）")
    L += ["\n## 分组汇总",
          "| 组 | 束数 | 贴 | z_from_top范围(mm) | 里程范围(m) | 面积(mm²) | σcon | 名实 |",
          "|---|---|---|---|---|---|---|---|"]
    for gk in gkeys:
        ts = groups[gk]
        L.append(f"| {gk} | {len(ts)} | {ts[0]['fam']} | {min(t['zft_min'] for t in ts):.0f}~{max(t['zft_max'] for t in ts):.0f} | "
                 f"{min(t['x_m'][0] for t in ts):.1f}~{max(t['x_m'][1] for t in ts):.1f} | {ts[0]['area']:.0f} | "
                 f"{ts[0]['sigcon'] if ts[0]['sigcon'] else '-'} | {'⚠' if any(t['name_vs_geom']=='MISMATCH' for t in ts) else '✓'} |")
    L.append("\n> 图：tmp/tendon_schematic_all.png、tmp/tendon_by_group.png；调整查询见 `--at`。")
    (HERE / "tendon_report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"OK {len(tendons)} tendons, {len(mis)} mismatch")

main()
