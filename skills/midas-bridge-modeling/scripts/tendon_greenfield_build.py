"""Phase 2b 步骤1：greenfield 钢束集**生成器（dry-run，不写模型）**。
从 sizer.json 的 n_top/n_bot(站) 反算 → 全套钢束几何(top 悬臂 + bottom 连续/支架)。
铁律分工：
  · 束数/布置(几族、每族几束、各束伸到哪) ← 我方 sizer 弯矩反算(确定)。
  · 截面/线形深度(PROFZ drape)、web 偏心、阶段张拉映射(PS{p}-{j}/KeyPS) ← template scaffold
    (=截面几何事实 + 施工浇筑时序，属"结构/映射"可借；非 JTG 取值)。
  · 面积/σcon ← 我方定值(2635.3mm² / 1341.5MPa ≤0.75fpk，承自 TDNT/TDPL)。
做法：每根生成束 = 深拷贝**最近 template 束**(同族、nElem 最接近)做骨架 → 覆写
  NAME/ELEM/INS_ELEM/OFF_YZ(web 奇偶)/PROFZ·PROFY(x 拉伸至新长，z 深度承袭)。
  TDPL GROUP_NAME 承自该骨架(top→对应 PS 阶段；bottom→KeyPS 单发)。
输出 tendon_greenfield.json（{"TDNA":{id:..},"TDPL":{id:..},"meta":..} 可直接 PUT）
      tmp/tendon_greenfield_overlay.png（生成 vs template，顶/底叠图，校验几何）
不删不写不分析。下一步 tendon_greenfield_write.py 执行 greenfield 写入+重分析+S10。
"""
import os, io, contextlib, json, copy
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
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

WEB_OFF = {"top": 3650.0, "bot": 3094.0}      # web 偏心幅值(mm)，承自 template
NTOP_GATE = 0.45                               # n>该阈才生成一根(避免半根碎束)
NBOT_GATE = 0.45
MIN_TOP_ELEMS = 5                              # 顶束最短(=template 墩台束长)，免阶梯峰生碎束
MIN_BOT_ELEMS = 3

def fam_of(name):
    n = str(name)
    for p in ("Top01","Top02","Bot01","FSMBot1","FSMBot2"):
        if n.startswith(p): return p
    return "other"

def fetch():
    tdna = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    tdpl = MidasAPI("GET","/db/TDPL",{}).get("TDPL",{})
    node = MidasAPI("GET","/db/NODE",{}).get("NODE",{})
    elem = MidasAPI("GET","/db/ELEM",{}).get("ELEM",{})
    nd = {int(k): v["X"] for k,v in node.items()}
    e2n = {int(k): [n for n in v["NODE"] if n!=0] for k,v in elem.items()}
    return tdna, tdpl, node, elem, nd, e2n

def build_templates(tdna, tdpl, nd, e2n):
    """按 NAME 找 id；汇集每族 template 束的几何 + scaffold + tdpl。"""
    name2id = {v["NAME"]: k for k,v in tdna.items()}
    fams = {}
    for k,v in tdna.items():
        fam = fam_of(v.get("NAME",""))
        if fam=="other": continue
        els = v.get("ELEM") or []
        if not els: continue
        rec = {"id":k, "name":v["NAME"], "elo":min(els), "ehi":max(els),
               "nElem":len(els), "ins_pt":v.get("INS_PT","END-I"),
               "scaffold":v, "tdpl": tdpl.get(k)}
        fams.setdefault(fam, []).append(rec)
    return fams, name2id

def region_bounds(fams, nd, e2n):
    def midX(rec):
        els = rec["scaffold"]["ELEM"]
        xs = [nd[n] for e in els for n in e2n.get(e,[])]
        return (min(xs)+max(xs))/2.0
    p1 = sum(midX(r) for r in fams["Top01"])/len(fams["Top01"])
    p2 = sum(midX(r) for r in fams["Top02"])/len(fams["Top02"])
    return p1, p2   # mm

def elem_span_for(emidX_mm, xl_mm, xr_mm, ebounds=None):
    es = sorted(e for e,x in emidX_mm.items() if xl_mm-1e-6 <= x <= xr_mm+1e-6)
    if ebounds:                                  # 限定在该族物理布置区(template 元素范围)
        elo,ehi = ebounds
        es = [e for e in es if elo <= e <= ehi]
    return es

def staircase(rows, key, gate):
    """rows: [{X(m), key:n}]. 返回 levels: [(level k, Xl_m, Xr_m)]。"""
    pts = sorted(((r["X"], r[key]) for r in rows), key=lambda t:t[0])
    if not pts: return []
    Nmax = int(round(max(p[1] for p in pts)))
    out = []
    for k in range(1, Nmax+1):
        xs = [x for x,n in pts if n >= k-0.5]
        if not xs: continue
        xl,xr = min(xs), max(xs)
        if xr-xl < 1.0: continue
        out.append((k, xl, xr))
    return out

def rescale_prof(prof, Lnew):
    """把 PROFZ/PROFY 点列 x 线性缩放到新总长 Lnew，z/y 深度不变。"""
    if not prof: return prof
    Lold = prof[-1]["PT"][0]
    if Lold <= 1e-9: return copy.deepcopy(prof)
    out = []
    for p in prof:
        q = copy.deepcopy(p)
        q["PT"] = [round(p["PT"][0]*Lnew/Lold, 3), p["PT"][1]]
        out.append(q)
    return out

def nearest_scaffold(recs, nElem, elo):
    return min(recs, key=lambda r: (abs(r["nElem"]-nElem), abs(r["elo"]-elo)))

def make_tendon(fam, recs, els, ins_pt, web, nd, e2n, fiber):
    """构造一根生成束 TDNA(深拷贝最近 scaffold + 覆写)。返回 (tdna_dict, scaffold_rec)。"""
    elo, ehi = els[0], els[-1]
    nElem = len(els)
    sc = nearest_scaffold(recs, nElem, elo)
    t = copy.deepcopy(sc["scaffold"])
    t["ELEM"] = list(els)
    ins_elem = elo if ins_pt=="END-I" else ehi
    t["INS_PT"] = ins_pt; t["INS_ELEM"] = ins_elem
    off = WEB_OFF[fiber] * (1 if web=="B" else -1)
    t["OFF_YZ"] = [off, 0]
    # 新长 = 该 ELEM 链端到端 X 距离
    xs = [nd[n] for e in els for n in e2n.get(e,[])]
    Lnew = max(xs)-min(xs)
    t["PROFZ"] = rescale_prof(sc["scaffold"]["PROFZ"], Lnew)
    if sc["scaffold"].get("PROFY"):
        t["PROFY"] = rescale_prof(sc["scaffold"]["PROFY"], Lnew)
    return t, sc

def fam_ebounds(recs):
    return (min(r["elo"] for r in recs), max(r["ehi"] for r in recs))

def expand_min(els, min_n, ebounds, all_elems):
    """把碎束的元素跨对称扩到 min_n 根(限族区内、限存在元素)。"""
    if len(els) >= min_n: return els
    elo, ehi = els[0], els[-1]
    while (ehi - elo + 1) < min_n:
        grew = False
        if ehi < ebounds[1]: ehi += 1; grew = True
        if (ehi - elo + 1) < min_n and elo > ebounds[0]: elo -= 1; grew = True
        if not grew: break
    return [e for e in range(elo, ehi + 1) if e in all_elems]

def gen_top(pier_name, rows_region, recs, emidX_mm, nd, e2n):
    eb = fam_ebounds(recs)
    levels = staircase(rows_region, "n_top", NTOP_GATE)
    allset = set(emidX_mm)
    gens = []
    for i,(k,xl,xr) in enumerate(levels):
        els = elem_span_for(emidX_mm, xl*1000, xr*1000, eb)
        if not els: continue
        els = expand_min(els, MIN_TOP_ELEMS, eb, allset)
        web = "A" if (k % 2 == 1) else "B"      # 奇偶分两腹板
        t, sc = make_tendon(pier_name, recs, els, "END-I", web, nd, e2n, "top")
        gens.append((t, sc, k, xl, xr))
    return gens

def gen_bot(fam_name, rows_region, recs, emidX_mm, nd, e2n, ins_pt):
    eb = fam_ebounds(recs)                        # 底束限定在 template 该族物理区(不入深墩区)
    levels = staircase(rows_region, "n_bot", NBOT_GATE)
    allset = set(emidX_mm)
    gens = []
    for i,(k,xl,xr) in enumerate(levels):
        els = elem_span_for(emidX_mm, xl*1000, xr*1000, eb)
        if not els: continue
        els = expand_min(els, MIN_BOT_ELEMS, eb, allset)
        web = "A" if (k % 2 == 1) else "B"
        t, sc = make_tendon(fam_name, recs, els, ins_pt, web, nd, e2n, "bot")
        gens.append((t, sc, k, xl, xr))
    return gens

def main():
    tdna, tdpl, node, elem, nd, e2n = fetch()
    fams, name2id = build_templates(tdna, tdpl, nd, e2n)
    girder = sorted({e for recs in fams.values() for r in recs for e in r["scaffold"]["ELEM"]})
    emidX_mm = {e: sum(nd[n] for n in e2n[e])/len(e2n[e]) for e in girder if e in e2n}
    p1, p2 = region_bounds(fams, nd, e2n)         # mm
    Xend = max(nd.values())
    rows = json.loads((HERE/"tendon_sizer.json").read_text(encoding="utf-8"))

    # 分域(用 X_m)
    p1m, p2m = p1/1000, p2/1000
    top1_rows = [r for r in rows if r["X"] < p2m]              # pier1 域: 用 SPLIT 在两墩间
    top2_rows = [r for r in rows if r["X"] >= p2m - (p2m-p1m)/2]
    # 更稳：以两墩中点分顶束域
    mid12 = (p1m+p2m)/2
    top1_rows = [r for r in rows if r["X"] < mid12]
    top2_rows = [r for r in rows if r["X"] >= mid12]
    side1_rows = [r for r in rows if r["X"] < p1m]
    main_rows  = [r for r in rows if p1m <= r["X"] <= p2m]
    side3_rows = [r for r in rows if r["X"] > p2m]

    G = {}
    G["Top01"] = gen_top("Top01", top1_rows, fams["Top01"], emidX_mm, nd, e2n)
    G["Top02"] = gen_top("Top02", top2_rows, fams["Top02"], emidX_mm, nd, e2n)
    G["FSMBot1"] = gen_bot("FSMBot1", side1_rows, fams["FSMBot1"], emidX_mm, nd, e2n, "END-I")
    G["Bot01"]   = gen_bot("Bot01",   main_rows,  fams["Bot01"],   emidX_mm, nd, e2n, "END-I")
    G["FSMBot2"] = gen_bot("FSMBot2", side3_rows, fams["FSMBot2"], emidX_mm, nd, e2n, "END-J")

    # 组装 TDNA_new + TDPL_new（新 ID 自 1 起，写入前删 template）
    TDNA_new, TDPL_new = {}, {}
    nid = 0
    summary = []
    for fam in ("Top01","Top02","Bot01","FSMBot1","FSMBot2"):
        gens = G[fam]
        for j,(t, sc, k, xl, xr) in enumerate(gens):
            nid += 1
            nm = f"Gen{fam}-{j+1:02d}"
            t["NAME"] = nm
            TDNA_new[str(nid)] = t
            pl = copy.deepcopy(sc["tdpl"]) if sc["tdpl"] else {"ITEMS":[{"ID":1,"LCNAME":"Prestress",
                 "GROUP_NAME":"KeyPS2","TENDON_NAME":nm,"TYPE":"STRESS","ORDER":"BEGIN",
                 "BEGIN":1341.54972,"END":1341.54972,"GROUTING":0}]}
            for it in pl.get("ITEMS",[]):
                it["TENDON_NAME"] = nm
            TDPL_new[str(nid)] = pl
        tcount = len(fams[fam])
        summary.append((fam, len(gens), tcount))

    meta = {"WEB_OFF":WEB_OFF, "pier1_X_m":round(p1m,2), "pier2_X_m":round(p2m,2),
            "counts": {f:{"generated":g,"template":t} for f,g,t in summary},
            "total_generated": nid, "total_template": len(tdna)}
    out = {"TDNA": TDNA_new, "TDPL": TDPL_new, "meta": meta}
    (HERE/"tendon_greenfield.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 叠图：生成(实) vs template(淡) ----
    def stat_z(prof, ins_pt, elo, ehi):
        """把一束 PROFZ 转 (X_m, z_from_top_mm) 用于画图。z_from_top = -PROFZ_z。"""
        if ins_pt=="END-I":
            x0 = nd[e2n[elo][0]]; sgn=1
        else:
            x0 = nd[e2n[ehi][-1]]; sgn=-1
        return [((x0+sgn*p["PT"][0])/1000, -p["PT"][1]) for p in prof]
    fig, axes = plt.subplots(2,1, figsize=(16,9), sharex=True)
    # template
    for k,v in tdna.items():
        fam=fam_of(v.get("NAME",""));
        if fam=="other" or not v.get("PROFZ"): continue
        els=v["ELEM"]; pts=stat_z(v["PROFZ"], v["INS_PT"], min(els), max(els))
        ax = axes[0] if fam.startswith("Top") else axes[1]
        ax.plot([p[0] for p in pts],[p[1] for p in pts], color="#bbbbbb", lw=0.6, alpha=0.6, zorder=1)
    # generated
    for fam in ("Top01","Top02","Bot01","FSMBot1","FSMBot2"):
        col = "#c0392b" if fam.startswith("Top") else "#2471a3"
        for (t,sc,k,xl,xr) in G[fam]:
            els=t["ELEM"]; pts=stat_z(t["PROFZ"], t["INS_PT"], min(els), max(els))
            ax = axes[0] if fam.startswith("Top") else axes[1]
            ax.plot([p[0] for p in pts],[p[1] for p in pts], color=col, lw=0.8, alpha=0.65, zorder=2)
    axes[0].set_title("TOP cantilever — generated(red) vs template(grey)")
    axes[1].set_title("BOTTOM continuity/falsework — generated(blue) vs template(grey)")
    for ax in axes:
        ax.invert_yaxis(); ax.grid(alpha=0.3); ax.set_ylabel("depth below deck top (mm)")
    axes[1].set_xlabel("Station X (m)")
    axes[0].axvline(p1m,color="k",ls=":",lw=0.6); axes[0].axvline(p2m,color="k",ls=":",lw=0.6)
    fig.tight_layout(); fig.savefig(TMP/"tendon_greenfield_overlay.png", dpi=110); plt.close(fig)

    print("==== greenfield 生成集（dry-run，未写模型）====")
    for fam,g,t in summary:
        print(f"  {fam:9s}: 生成 {g:3d}  | template {t:3d}  ({'≈' if abs(g-t)<=2 else ('↑' if g>t else '↓')})")
    print(f"  合计: 生成 {nid} 束 | template {len(tdna)} 束")
    print(f"  墩1 X={p1m:.1f}m  墩2 X={p2m:.1f}m  跨端 X={Xend/1000:.1f}m")
    print("-> tendon_greenfield.json + tmp/tendon_greenfield_overlay.png")

main()
