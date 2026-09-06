"""Phase 2b 对照组 B：**仅顶束 greenfield**（删 template Top*，写生成 GenTop*，**保留** template 底束）。
目的：隔离变量——证"顶束生成器单独是否产出与 baseline 同级的模型"，把对照组 A(全 greenfield)
侧跨 SLS 失效归因于底束(而非顶束生成器)。
做法：打开干净 baseline → 删 NAME 以 Top 开头的 template 束(+其 TDPL) → 写 greenfield.json 中
  GenTop01/GenTop02(新 ID 自 201，避让保留的底束 1..46) → 保留 Bot01/FSMBot* → saveAs。
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
    ro = MidasAPI("POST","/doc/OPEN",{"Argument": str(HERE/"fcm_box_jtg.mcb")})
    w("OPEN fcm_box_jtg.mcb -> " + ("ERR" if isinstance(ro,dict) and "error" in ro else "OK"))
    gf = json.loads((HERE/"tendon_greenfield.json").read_text(encoding="utf-8"))
    # 只取生成顶束
    gen_top = {k:v for k,v in gf["TDNA"].items() if str(v.get("NAME","")).startswith("GenTop")}
    gen_top_pl = {k:gf["TDPL"][k] for k in gen_top}
    w(f"生成顶束 {len(gen_top)} 束")

    a0 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    p0 = MidasAPI("GET","/db/TDPL",{}).get("TDPL",{})
    top_ids = [k for k,v in a0.items() if str(v.get("NAME","")).startswith("Top")]
    w(f"写前 TDNA {len(a0)}；template 顶束 {len(top_ids)} 条将删")
    if top_ids:
        MidasAPI("DELETE","/db/TDNA/"+",".join(top_ids))
        MidasAPI("DELETE","/db/TDPL/"+",".join(top_ids))
    a1 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    w(f"删后 TDNA {len(a1)}（应=46 底束）")
    kept_ids = set(int(k) for k in a1.keys())

    # 生成顶束重排 ID 自 201，避让保留底束
    NEW = {}; NEWPL = {}; nid = 201
    for k in sorted(gen_top, key=lambda x:int(x)):
        while nid in kept_ids: nid += 1
        NEW[str(nid)] = gen_top[k]
        pl = json.loads(json.dumps(gen_top_pl[k]))
        NEWPL[str(nid)] = pl
        nid += 1
    MidasAPI("PUT","/db/TDNA",{"Assign":NEW})
    MidasAPI("PUT","/db/TDPL",{"Assign":NEWPL})
    a2 = MidasAPI("GET","/db/TDNA",{}).get("TDNA",{})
    from collections import Counter
    c = Counter()
    for v in a2.values():
        nm=str(v.get("NAME",""))
        key = "GenTop" if nm.startswith("GenTop") else ("Top(tmpl)" if nm.startswith("Top") else
              ("Bot/FSM(tmpl)" if (nm.startswith("Bot") or nm.startswith("FSM")) else "other"))
        c[key]+=1
    w(f"写后 TDNA {len(a2)}：" + json.dumps(dict(c), ensure_ascii=False))
    Model.saveAs(str(HERE/"fcm_box_jtg_greentop.mcb"))
    w("已 saveAs fcm_box_jtg_greentop.mcb（未分析）。下一步 analyze+verify。")
except Exception as e:
    import traceback
    w("!! 异常 "+repr(e)); w(traceback.format_exc())
(HERE/"tendon_greentop_write_out.txt").write_text("\n".join(log), encoding="utf-8")
print("done")
