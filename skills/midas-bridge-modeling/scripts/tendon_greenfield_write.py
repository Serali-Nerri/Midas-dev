"""Phase 2b 步骤2a：greenfield **写入**（删 template 钢束 → 写生成束 → 存盘 pre-analysis）。
读 tendon_greenfield.json（步骤1 dry-run 产物）。
  1) 备份校验（baseline 必须在）
  2) DELETE 全部 template TDNA + TDPL（greenfield，免叠加双倍预压）
  3) PUT 生成 TDNA + TDPL（面积/σcon 承自 TDNT/TDPL=我方定值；阶段映射 PS{p}-{j}/KeyPS）
  4) 读回校验束数
  5) Model.saveAs fcm_box_jtg_greenfield.mcb（未分析；分析单列下一步）
不分析（POST /doc/ANAL 在 analyze_model.py 或下一步）。原 fcm_box_jtg.mcb 不动。
"""
import os, io, contextlib, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    from midas_civil import MAPI_BASEURL, MAPI_KEY, MidasAPI, NX, Model
MAPI_BASEURL(os.environ["MIDAS_MAPI_BASEURL"]); MAPI_KEY(os.environ["MIDAS_MAPI_KEY"])
NX.user_print = False
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    try: Model.units(force="N", length="MM")
    except Exception: pass
log = []
def w(s=""): log.append(s if isinstance(s, str) else json.dumps(s, ensure_ascii=False))

try:
    if not (HERE/"fcm_box_jtg_baseline.mcb").exists():
        raise SystemExit("!! baseline 备份缺失，拒绝写入")
    # 先打开磁盘事实源(免活动模型漂移)
    ro = MidasAPI("POST","/doc/OPEN",{"Argument": str(HERE/"fcm_box_jtg.mcb")})
    w("OPEN fcm_box_jtg.mcb -> " + ("ERR "+json.dumps(ro,ensure_ascii=False)[:160] if isinstance(ro,dict) and "error" in ro else "OK"))
    gf = json.loads((HERE/"tendon_greenfield.json").read_text(encoding="utf-8"))
    TDNA_new, TDPL_new = gf["TDNA"], gf["TDPL"]
    w(f"读 greenfield: TDNA {len(TDNA_new)} 束、TDPL {len(TDPL_new)} 条 | meta={json.dumps(gf['meta']['counts'],ensure_ascii=False)}")

    a0 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    p0 = MidasAPI("GET","/db/TDPL",{}).get("TDPL",{})
    w(f"写前: TDNA {len(a0)} 束、TDPL {len(p0)} 条")

    # --- DELETE 全部 template 钢束 ---
    if a0:
        ra = MidasAPI("DELETE","/db/TDNA/"+",".join(a0.keys()))
        w("DELETE TDNA -> " + ("ERR "+json.dumps(ra,ensure_ascii=False)[:160] if isinstance(ra,dict) and "error" in ra else "OK"))
    if p0:
        rp = MidasAPI("DELETE","/db/TDPL/"+",".join(p0.keys()))
        w("DELETE TDPL -> " + ("ERR "+json.dumps(rp,ensure_ascii=False)[:160] if isinstance(rp,dict) and "error" in rp else "OK"))
    a1 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    p1 = MidasAPI("GET","/db/TDPL",{}).get("TDPL",{})
    w(f"删后: TDNA {len(a1)} 束、TDPL {len(p1)} 条（应为 0）")
    if a1 or p1:
        # 兜底逐条删
        for k in list(a1.keys()): MidasAPI("DELETE","/db/TDNA/"+k)
        for k in list(p1.keys()): MidasAPI("DELETE","/db/TDPL/"+k)
        a1 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
        w(f"兜底删后: TDNA {len(a1)} 束")

    # --- PUT 生成束 ---
    ra = MidasAPI("PUT","/db/TDNA",{"Assign":TDNA_new})
    w("PUT TDNA -> " + ("ERR "+json.dumps(ra,ensure_ascii=False)[:200] if isinstance(ra,dict) and "error" in ra else "OK"))
    rp = MidasAPI("PUT","/db/TDPL",{"Assign":TDPL_new})
    w("PUT TDPL -> " + ("ERR "+json.dumps(rp,ensure_ascii=False)[:200] if isinstance(rp,dict) and "error" in rp else "OK"))

    a2 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    p2 = MidasAPI("GET","/db/TDPL",{}).get("TDPL",{})
    w(f"写后: TDNA {len(a2)} 束、TDPL {len(p2)} 条")
    # 按族统计读回
    from collections import Counter
    c = Counter()
    for v in a2.values():
        nm=str(v.get("NAME",""))
        for pre in ("GenTop01","GenTop02","GenBot01","GenFSMBot1","GenFSMBot2"):
            if nm.startswith(pre): c[pre]+=1; break
    w("读回族计: " + json.dumps(dict(c), ensure_ascii=False))

    # --- 存盘 pre-analysis（不覆盖原模型）---
    out_mcb = str(HERE/"fcm_box_jtg_greenfield.mcb")
    Model.saveAs(out_mcb)
    w(f"已 saveAs {out_mcb}（未分析）。下一步：POST /doc/ANAL → verify_engine。")
except SystemExit as e:
    w(str(e))
except Exception as e:
    import traceback
    w("!! 异常 "+repr(e)); w(traceback.format_exc())
(HERE/"tendon_greenfield_write_out.txt").write_text("\n".join(log), encoding="utf-8")
print("done")
