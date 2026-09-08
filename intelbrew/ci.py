# SPDX-License-Identifier: BSD-2-Clause
"""Build and verification stages. This module refuses to mutate non-CI hosts."""
from __future__ import annotations
import argparse, copy, io, json, os, re, shutil, sys, tarfile, tempfile
from pathlib import Path
from .cli import attest
from .core import (ROOT, Error, Planner, artifact_url, basename, brew_env, canonical_name,
                   check_bottle, digest, download, load_config, native, read_json,
                   registry, require_sha, run, validate_record, write_json_new)

BOTTLE_OPTIONS = ("--json", "--no-rebuild")


def require_ci_mac() -> None:
    expected={"GITHUB_ACTIONS":"true","RUNNER_ENVIRONMENT":"github-hosted","RUNNER_OS":"macOS","RUNNER_ARCH":"X64"}
    if any(os.environ.get(k)!=v for k,v in expected.items()):raise Error("This operation is restricted to ephemeral GitHub-hosted Intel macOS runners")
    require_sha(os.environ.get("INTELBREW_CORE_COMMIT"),git=True);require_sha(os.environ.get("GITHUB_SHA"),git=True)


def permissive_license(expression, allowed:set[str], *, depth:int=0)->bool:
    if depth>12:return False
    if isinstance(expression,dict):
        if len(expression)!=1 or next(iter(expression),None) not in {"any_of","all_of"}:return False
        terms=next(iter(expression.values()))
        return isinstance(terms,list) and 0<len(terms)<=64 and all(permissive_license(x,allowed,depth=depth+1) for x in terms)
    if not isinstance(expression,str) or len(expression)>4096:return False
    tokens=re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*|[()]",expression)
    if "".join(tokens)!=re.sub(r"\s+","",expression) or not tokens:return False
    want,par=True,0
    for token in tokens:
        if want:
            if token=="(":par+=1
            elif token in allowed:want=False
            else:return False
            if par>12:return False
        elif token in {"AND","OR"}:want=True
        elif token==")" and par:par-=1
        else:return False
    return not want and par==0


def allowed_redistribution(meta:dict,config:dict)->None:
    expression=meta.get("license")
    if permissive_license(expression,set(config["permissive_license_tokens"])):return
    exception=config["redistribution_exceptions"].get(meta["name"],{})
    if exception.get("formula_sha256")==meta["formula_sha256"] and isinstance(exception.get("review"),str) and len(exception["review"].strip())>=30:return
    raise Error(f'{meta["name"]}: redistribution review required for {expression!r}')


def runtime_closure(name:str,nodes:dict)->list[str]:
    seen=set()
    def visit(item):
        for dep in nodes[item]["runtime"]:
            if dep not in seen:seen.add(dep);visit(dep)
    visit(name);return sorted(seen)


def source_bundle(name,meta,sources,output,core_commit):
    if sources["formula_sha256"]!=meta["formula_sha256"]:raise Error("Source recipe differs from plan")
    archive=output/f'{name}--{meta["pkg_version"]}.sources.tar.gz'
    if archive.exists():raise Error("Refusing to overwrite a source bundle")
    index={"schema":1,"name":name,"core_commit":core_commit,"formula_sha256":meta["formula_sha256"],"resources":[]}
    with tarfile.open(archive,"x:gz") as bundle:
        recipe=Path(sources["formula_path"])
        if not recipe.is_file() or digest(recipe)!=meta["formula_sha256"]:raise Error("Recipe changed during source collection")
        bundle.add(recipe,arcname=f"recipe/{name}.rb",recursive=False)
        bundle.add(ROOT/"LICENSES/Homebrew-BSD-2-Clause.txt",arcname="LICENSES/Homebrew-BSD-2-Clause.txt",recursive=False)
        for i,res in enumerate(sources["resources"]):
            path=Path(res["path"])
            if not path.is_file() or digest(path)!=res["sha256"]:raise Error("Source archive changed")
            filename=f'{i:03d}-{basename(path.name)}';bundle.add(path,arcname=f"inputs/{filename}",recursive=False)
            index["resources"].append({"filename":f"inputs/{filename}","label":res["label"],"url":res["url"],"sha256":res["sha256"]})
        data=(json.dumps(index,indent=2)+"\n").encode();info=tarfile.TarInfo("sources.json");info.size=len(data);info.mode=0o644;info.mtime=0;bundle.addfile(info,io.BytesIO(data))
    if archive.stat().st_size>2_000_000_000:raise Error("Source bundle exceeds budget")
    return archive


def extract_bottle_metadata(details:dict, tag:str)->tuple[str,str]:
    bottle=next(iter(details.values()))["bottle"]
    tag_data=bottle["tags"][tag]
    return tag_data["sha256"], str(bottle["cellar"]).lstrip(":")


def install_binary(name,meta,*,local=None,sha=None,as_dependency=True):
    req={"mode":"install","name":name,"pkg_version":meta["pkg_version"],"formula_sha256":meta["formula_sha256"],"as_dependency":as_dependency,"target":str(local.resolve()) if local else f"homebrew/core/{name}"}
    if local:req["sha256"]=sha
    native(req,capture=False,ci=True)


def build(root:str,output:Path)->None:
    require_ci_mac();config=load_config();allowed=read_json(ROOT/"policy/targets.json")["formulae"]
    if root not in allowed:raise Error("Root is not in reviewed target list")
    if output.exists():raise Error("Build output must be fresh")
    output.mkdir(parents=True);records=registry();inspector=lambda batch:native({"mode":"inspect","names":batch},ci=True)
    plan=Planner(inspector,records,build=True,max_nodes=config["max_graph_nodes"],blocked=config["blocked_source_builds"]).make([root])
    to_build=[n for n in plan["order"] if plan["nodes"][n]["provider"]=="build"]
    if len(to_build)>config["max_source_builds"]:raise Error("Source build budget exceeded")
    for name in to_build:allowed_redistribution(plan["nodes"][name],config)
    core_commit=require_sha(os.environ["INTELBREW_CORE_COMMIT"],git=True);workflow_commit=require_sha(os.environ["GITHUB_SHA"],git=True)
    manifest={"schema":1,"root":root,"core_commit":core_commit,"brew_commit":config["brew_commit"],"workflow_commit":workflow_commit,"plan":plan,"packages":[],"verified":False}
    if not to_build:write_json_new(output/"manifest.json",manifest);return
    work=Path(tempfile.mkdtemp(prefix="intelbrew-build-",dir=os.environ["RUNNER_TEMP"]))
    for name in plan["order"]:
        meta=plan["nodes"][name]
        if meta["provider"]=="official":install_binary(name,meta,as_dependency=name!=root);continue
        if meta["provider"]=="personal":
            rec=records[name];path=work/rec["sha256"]/rec["filename"];download(artifact_url(config["repository"],rec),path,rec["sha256"],rec["size"]);attest(path,config["repository"],rec["workflow_commit"]);check_bottle(path,rec);install_binary(name,meta,local=path,sha=rec["sha256"],as_dependency=name!=root);continue
        if meta["provider"]!="build":raise Error("Unexpected CI package provider")
        sources=native({"mode":"sources","name":name},ci=True);source_path=source_bundle(name,meta,sources,output,core_commit)
        run(["brew","install","--build-bottle","--no-ask",f"homebrew/core/{name}"],capture=False,env=brew_env(ci=True))
        bw=work/name;bw.mkdir();run(["brew","bottle",*BOTTLE_OPTIONS,f"homebrew/core/{name}"],capture=False,env=brew_env(ci=True),cwd=bw)
        files=list(bw.glob("*.sequoia.bottle*.tar.gz"));js=list(bw.glob("*.bottle.json"))
        if len(files)!=1 or len(js)!=1:raise Error("Expected one Sequoia bottle and JSON")
        details=read_json(js[0]);bottle_sha,bottle_cellar=extract_bottle_metadata(details,"sequoia");filename=basename(files[0].name);target=output/filename;shutil.copyfile(files[0],target)
        if digest(target)!=bottle_sha:raise Error("Bottle checksum mismatch")
        rec={"schema":1,"name":name,"version":meta["version"],"revision":meta["revision"],"version_scheme":meta["version_scheme"],"pkg_version":meta["pkg_version"],"formula_sha256":meta["formula_sha256"],"recipe_sha256":sources["recipe_sha256"],"core_commit":core_commit,"brew_commit":config["brew_commit"],"tag":"sequoia","arch":"x86_64","cellar":bottle_cellar,"filename":filename,"sha256":digest(target),"size":target.stat().st_size,"license":meta["license"],"runtime_dependencies":[{"name":d,"pkg_version":plan["nodes"][d]["pkg_version"],"formula_sha256":plan["nodes"][d]["formula_sha256"]} for d in runtime_closure(name,plan["nodes"])],"source":{"filename":source_path.name,"sha256":digest(source_path),"size":source_path.stat().st_size},"release":None,"workflow_commit":workflow_commit,"run_id":os.environ["GITHUB_RUN_ID"]}
        check_bottle(target,rec);manifest["packages"].append(rec)
    write_json_new(output/"manifest.json",manifest);validate_candidate(output,expected_root=root,verified=False)


def validate_candidate(directory:Path,*,expected_root:str,verified=None)->dict:
    if directory.is_symlink() or not directory.is_dir():raise Error("Candidate directory invalid")
    if sum(p.stat().st_size for p in directory.iterdir() if p.is_file())>load_config()["max_candidate_bytes"]:raise Error("Candidate exceeds transfer budget")
    manifest=read_json(directory/"manifest.json")
    required={"schema","root","core_commit","brew_commit","workflow_commit","plan","packages","verified"}
    if not isinstance(manifest,dict) or set(manifest)!=required or manifest["schema"]!=1 or manifest["root"]!=expected_root:raise Error("Unexpected candidate manifest")
    for key in ("core_commit","brew_commit","workflow_commit"):require_sha(manifest[key],git=True)
    if type(manifest["verified"]) is not bool or (verified is not None and manifest["verified"] is not verified):raise Error("Candidate verification state invalid")
    if not isinstance(manifest["packages"],list) or len(manifest["packages"])>60:raise Error("Invalid candidate count")
    allowed_files={"manifest.json"};names=set()
    for rec in manifest["packages"]:
        validate_record(rec,published=False)
        if rec["name"] in names:raise Error("Duplicate candidate formula")
        names.add(rec["name"])
        for key in ("core_commit","brew_commit","workflow_commit"):
            if rec[key]!=manifest[key]:raise Error("Candidate provenance mismatch")
        check_bottle(directory/rec["filename"],rec);src=rec["source"];sp=directory/src["filename"]
        if sp.is_symlink() or not sp.is_file() or sp.stat().st_size!=src["size"] or digest(sp)!=src["sha256"]:raise Error("Source bundle missing/corrupt")
        allowed_files.update((rec["filename"],src["filename"]))
    if {p.name for p in directory.iterdir()}!=allowed_files:raise Error("Unexpected candidate files")
    return manifest


def verify(root:str,candidate:Path,output:Path)->None:
    require_ci_mac();config=load_config();manifest=validate_candidate(candidate,expected_root=root,verified=False)
    if manifest["workflow_commit"]!=os.environ["GITHUB_SHA"] or manifest["core_commit"]!=os.environ["INTELBREW_CORE_COMMIT"]:raise Error("Candidate provenance mismatch")
    if output.exists():raise Error("Verification output must be new")
    output.mkdir(parents=True)
    if manifest["packages"]:
        native({"mode":"guard-test"},ci=True);plan=manifest["plan"]
        independent=Planner(lambda batch:native({"mode":"inspect","names":batch},ci=True),registry(),build=True,max_nodes=config["max_graph_nodes"],blocked=config["blocked_source_builds"]).make([root])
        if plan!=independent:raise Error("Independent plan differs")
        local={r["name"]:r for r in manifest["packages"]};expected={n for n in plan["order"] if plan["nodes"][n]["provider"]=="build"}
        if expected!=set(local):raise Error("Candidate package set differs")
        current=native({"mode":"inspect","names":plan["order"]},ci=True);records=registry()
        for name in plan["order"]:
            meta=plan["nodes"][name]
            if current[name]["formula_sha256"]!=meta["formula_sha256"] or current[name]["pkg_version"]!=meta["pkg_version"]:raise Error("Independent metadata differs")
            if name in local:
                rec=local[name];allowed_redistribution(meta,config);install_binary(name,meta,local=candidate/rec["filename"],sha=rec["sha256"],as_dependency=name!=root)
            elif meta["provider"]=="personal":
                rec=records[name];cache=output.parent/("verify-cache-"+root)/rec["sha256"]/rec["filename"];download(artifact_url(config["repository"],rec),cache,rec["sha256"],rec["size"]);attest(cache,config["repository"],rec["workflow_commit"]);check_bottle(cache,rec);install_binary(name,meta,local=cache,sha=rec["sha256"],as_dependency=name!=root)
            else:install_binary(name,meta,as_dependency=name!=root)
        for name in local:
            receipt=native({"mode":"receipt","name":name},ci=True)
            if receipt["tap"]!="homebrew/core" or not receipt["poured_from_bottle"]:raise Error("Core bottle verification failed")
            run(["brew","linkage","--test",f"homebrew/core/{name}"],capture=False,env=brew_env(ci=True));run(["brew","test",f"homebrew/core/{name}"],capture=False,env=brew_env(ci=True))
    for rec in manifest["packages"]:
        for filename in (rec["filename"],rec["source"]["filename"]):shutil.copyfile(candidate/filename,output/filename)
    out=copy.deepcopy(manifest);out["verified"]=True;write_json_new(output/"manifest.json",out);validate_candidate(output,expected_root=root,verified=True)


def main()->int:
    parser=argparse.ArgumentParser();parser.add_argument("command",choices=("build","verify","validate"));parser.add_argument("--root",required=True);parser.add_argument("--output",type=Path);parser.add_argument("--candidate",type=Path);args=parser.parse_args()
    try:
        root=canonical_name(args.root)
        if args.command=="build":build(root,args.output.resolve())
        elif args.command=="verify":verify(root,args.candidate.resolve(),args.output.resolve())
        else:
            m=validate_candidate(args.candidate.resolve(),expected_root=root,verified=True)
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"],"a") as h:h.write(f'has_bottles={str(bool(m["packages"])).lower()}\n')
            print(json.dumps({"has_bottles":bool(m["packages"]),"root":root}))
        return 0
    except (Error,OSError,KeyError,TypeError,ValueError,AttributeError) as exc:
        print(f"intelbrew CI: {exc}",file=sys.stderr);return 1

if __name__=="__main__":raise SystemExit(main())
