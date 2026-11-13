#!/usr/bin/env python3
"""Local Runner Tool Center: searchable catalog, discovery index and health report."""
import argparse, json, os, platform, re, shutil, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CATALOG=ROOT/"config/runner-tools.json"
DEFAULT_ROOT=Path(os.environ.get("RUNNER_TOOLS_ROOT","/opt/runner-tools"))

def load_catalog(path=DEFAULT_CATALOG):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version")!=1 or not isinstance(data.get("categories"),list) or not isinstance(data.get("tools"),list):
        raise ValueError("invalid Runner Tool Center catalog")
    known={x["id"] for x in data["categories"]}; ids=set()
    for tool in data["tools"]:
        tid=tool.get("id")
        if not isinstance(tid,str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*",tid) or tid in ids:
            raise ValueError(f"invalid or duplicate tool id: {tid!r}")
        ids.add(tid)
        if tool.get("category") not in known or not tool.get("description"): raise ValueError(f"invalid catalog entry: {tid}")
    return data

def search_tools(catalog,query):
    terms=re.findall(r"[a-z0-9_+.-]+",query.lower()); scored=[]
    for t in catalog["tools"]:
        hay=" ".join([t["id"],t["name"],t["category"],t["description"]," ".join(t.get("keywords",[]))," ".join(t.get("commands",[]))," ".join(t.get("examples",[]))]).lower()
        score=0
        for term in terms:
            if term==t["id"]: score+=100
            elif term in t["id"].split("-"): score+=35
            elif term in t["name"].lower(): score+=25
            elif term in " ".join(t.get("commands",[])).lower(): score+=20
            elif term in " ".join(t.get("keywords",[])).lower(): score+=12
            elif term in t["description"].lower(): score+=8
            elif term in hay: score+=4
            else: score-=5
        if score>0: scored.append((score,t))
    return [t for _,t in sorted(scored,key=lambda x:(-x[0],x[1]["category"],x[1]["name"].lower()))]

def tool_status(t,deep=False):
    result={"id":t["id"],"status":"MISSING","resolved":[]}
    missing=[]
    for command in t.get("commands",[]):
        found=shutil.which(command)
        if found: result["resolved"].append(found)
        else: missing.append(command)
    if t.get("commands") and not missing: result["status"]="READY"
    elif missing: result["missing_commands"]=missing
    import glob
    paths=[]
    for pattern in t.get("paths",[]):
        paths += [x for x in glob.glob(os.path.expanduser(os.path.expandvars(pattern))) if Path(x).is_file() and os.access(x,os.X_OK)]
    if paths: result["resolved"]+=paths; result["status"]="READY"
    provider=t.get("provider")
    if provider:
        if not shutil.which("rea"):
            result["status"]="MISSING"; result["reason"]="REA CLI is not on PATH"
        elif not deep:
            result["status"]="PROVIDER"; result["reason"]=f"Run rea doctor --provider {provider} --format json"
        else:
            try:
                proc=subprocess.run(["rea","doctor","--provider",provider,"--format","json"],capture_output=True,text=True,timeout=90,check=False)
                result["status"]="READY" if proc.returncode==0 else "MISSING"
                if proc.returncode: result["reason"]=(proc.stderr or proc.stdout).strip()[-1000:]
            except (OSError,subprocess.TimeoutExpired) as e:
                result["status"]="ERROR"; result["reason"]=str(e)
    if not t.get("commands") and not t.get("paths") and not provider: result["status"]="MANAGED"
    return result

def version_for(t):
    cmd=t.get("version_command")
    if not cmd: return None
    try: p=subprocess.run(cmd,capture_output=True,text=True,timeout=8,check=False)
    except (OSError,subprocess.TimeoutExpired): return None
    out=(p.stdout or p.stderr).strip()
    return out.splitlines()[0][:300] if p.returncode==0 and out else None

def index_text(catalog):
    lines=["# Runner Tool Index","","All listed utilities are provided by the shared Runner image/prewarm. Check this local catalog before searching online or installing another copy.","",
    "Quick commands:","- agent-lab tools search QUERY — find by task, alias or command","- agent-lab tools info TOOL_ID — exact commands and examples","- agent-lab tools list — browse categories","- agent-lab tools check — verify tools and REA providers","- agent-lab tools report — machine-readable inventory","- agent-lab tools index — display this index",""]
    for c in catalog["categories"]:
        items=[t for t in catalog["tools"] if t["category"]==c["id"]]
        lines += [f"## {c['label']}","",c["description"],"",", ".join(t["id"] for t in items),""]
    lines += ["Selection: use native Shell commands. REA-managed providers go through rea; do not run MCP setup. Project-specific dependencies remain governed by target repository lockfiles."]
    return "\n".join(lines)+"\n"

def onboarding_summary():
    c=load_catalog()
    return {"index":"agent-lab tools index","search":"agent-lab tools search QUERY","info":"agent-lab tools info TOOL_ID","check":"agent-lab tools check",
        "categories":[{"id":x["id"],"label":x["label"],"tools":[t["id"] for t in c["tools"] if t["category"]==x["id"]]} for x in c["categories"]],
        "rule":"Search this local catalog before looking online or installing a tool; use direct Shell commands after discovery."}

def inventory(c,deep=False):
    entries=[]
    for t in c["tools"]:
        item=tool_status(t,deep)
        item.update({"name":t["name"],"category":t["category"],"required":t.get("required",True),"managed_by":t.get("managed_by","unknown")})
        item["version"]=version_for(t) if item["status"]=="READY" else None
        entries.append(item)
    return {"schema_version":1,"generated_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"platform":platform.platform(),
        "architecture":platform.machine(),"toolchain_version":json.loads((ROOT/"config/runner-toolset.json").read_text()).get("toolchain_version"),
        "tools_root":str(DEFAULT_ROOT),"summary":{"total":len(entries),"ready":sum(x["status"]=="READY" for x in entries),
        "provider_checks":sum(x["status"]=="PROVIDER" for x in entries),"missing":sum(x["status"]=="MISSING" for x in entries),
        "errors":sum(x["status"]=="ERROR" for x in entries)},"tools":entries}

def show(t,state=None):
    print(f"[{state['status'] if state else 'CATALOG'}] {t['id']} — {t['name']}")
    print("  "+t["description"])
    if t.get("commands"): print("  Commands: "+", ".join(t["commands"]))
    if t.get("provider"): print("  REA provider: "+t["provider"])
    print("  Source: "+t.get("managed_by","unknown"))
    for ex in t.get("examples",[])[:4]: print("  $ "+ex)
    if state and state.get("reason"): print("  Note: "+state["reason"])

def main(argv=None):
    p=argparse.ArgumentParser(prog="agent-lab tools"); p.add_argument("--catalog",type=Path,default=DEFAULT_CATALOG)
    s=p.add_subparsers(dest="action",required=True)
    q=s.add_parser("list"); q.add_argument("--category"); q.add_argument("--json",action="store_true")
    q=s.add_parser("search"); q.add_argument("query",nargs="+")
    q=s.add_parser("info"); q.add_argument("tool_id")
    s.add_parser("categories")
    q=s.add_parser("check"); q.add_argument("--json",action="store_true")
    q=s.add_parser("index"); q.add_argument("--output",type=Path)
    q=s.add_parser("report"); q.add_argument("--output",type=Path); q.add_argument("--deep",action="store_true")
    a=p.parse_args(argv); c=load_catalog(a.catalog); tools=c["tools"]; categories={x["id"]:x for x in c["categories"]}
    if a.action=="categories":
        for x in c["categories"]: print(f"{x['id']:<18} {x['label']}")
        return 0
    if a.action=="index":
        text=index_text(c)
        if a.output: a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(text); print(f"TOOL_INDEX={a.output}")
        else: print(text,end="")
        return 0
    if a.action=="list":
        chosen=tools
        if a.category:
            if a.category not in categories: raise ValueError("unknown category: "+a.category)
            chosen=[x for x in tools if x["category"]==a.category]
        if a.json: print(json.dumps(chosen,indent=2)); return 0
        for cat in c["categories"]:
            group=[x for x in chosen if x["category"]==cat["id"]]
            if group:
                print("\n"+cat["label"])
                for t in group: print(f"  [{tool_status(t)['status']:<8}] {t['id']:<18} {t['description']}")
        return 0
    if a.action=="search":
        q=" ".join(a.query); found=search_tools(c,q)
        if not found: print("No local tool matches: "+q); return 1
        for t in found[:12]: show(t,tool_status(t))
        return 0
    if a.action=="info":
        t=next((x for x in tools if x["id"]==a.tool_id),None)
        if not t: raise ValueError("unknown tool id: "+a.tool_id)
        show(t,tool_status(t))
        if t.get("version_command"): print("  Version: "+str(version_for(t) or "unavailable"))
        return 0
    if a.action=="check":
        data=inventory(c,deep=True)
        if a.json: print(json.dumps(data,indent=2))
        else:
            for t in data["tools"]:
                mark=t["status"]
                if t["required"] and mark in ("MISSING","ERROR"): mark="FAIL"
                print(f"{mark:<8} {t['id']:<18} {t['version'] or ''}")
            print(json.dumps(data["summary"],sort_keys=True))
        return 1 if any(t["required"] and t["status"] in ("MISSING","ERROR") for t in data["tools"]) else 0
    if a.action=="report":
        text=json.dumps(inventory(c,a.deep),indent=2)+"\n"
        if a.output: a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(text); print(f"TOOL_REPORT={a.output}")
        else: print(text,end="")
        return 0
    return 2

if __name__=="__main__":
    try: raise SystemExit(main())
    except (OSError,ValueError,json.JSONDecodeError) as e:
        print(f"tool center error: {e}",file=sys.stderr); raise SystemExit(2)
