# SPDX-License-Identifier: BSD-2-Clause
"""Build and verification stages. This module refuses to mutate non-CI hosts."""
from __future__ import annotations
import argparse, copy, hashlib, io, json, os, re, shutil, sys, tarfile, tempfile
from pathlib import Path, PurePosixPath
from .archive_notices import archive_notices
from .build_sources import MAX_FILES as MAX_BUILD_SOURCE_FILES, collect_build_sources
from .cli import attest
from .core import (MAX_JSON, ROOT, Error, Planner, artifact_url, basename, brew_env, canonical_name,
                   check_bottle, digest, download, load_config, native, read_json,
                   registry, require_sha, run, validate_record, write_json_new)

BOTTLE_OPTIONS = ("--json", "--no-rebuild")
MAX_SOURCE_NOTICES = 128


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


def _string_license_requirements(expression:str,config:dict):
    if len(expression)>4096:return None
    tokens=re.findall(r"[A-Za-z0-9][A-Za-z0-9._+-]*|[()]",expression)
    if not tokens or len(tokens)>256 or "".join(tokens)!=re.sub(r"\s+","",expression):return None
    permissive=set(config["permissive_license_tokens"]);source=set(config.get("source_required_license_tokens",()))
    exceptions=set(config.get("source_required_license_exceptions",()))
    position=0
    def primary(depth):
        nonlocal position
        if depth>12 or position>=len(tokens):raise ValueError
        token=tokens[position]
        if token=="(":
            position+=1;requirements=or_expression(depth+1)
            if position>=len(tokens) or tokens[position]!=")":raise ValueError
            position+=1;return requirements,None
        if token in {"AND","OR","WITH",")"}:raise ValueError
        position+=1
        if token in permissive:return (),token
        if token in source:return (token,),token
        raise ValueError
    def with_expression(depth):
        nonlocal position
        requirements,license_id=primary(depth)
        if position<len(tokens) and tokens[position]=="WITH":
            if license_id is None or position+1>=len(tokens):raise ValueError
            exception=tokens[position+1]
            if exception in {"AND","OR","WITH","(",")"}:raise ValueError
            pair=f"{license_id} WITH {exception}"
            if pair not in exceptions:raise ValueError
            position+=2;return (pair,)
        return requirements
    def and_expression(depth):
        nonlocal position
        requirements=with_expression(depth)
        while position<len(tokens) and tokens[position]=="AND":
            position+=1;other=with_expression(depth)
            requirements=tuple(sorted(set(requirements)|set(other)))
        return requirements
    def or_expression(depth):
        nonlocal position
        choices=[and_expression(depth)]
        while position<len(tokens) and tokens[position]=="OR":
            position+=1;choices.append(and_expression(depth))
        return min(choices,key=lambda item:(len(item),item))
    try:
        result=or_expression(0)
        return result if position==len(tokens) else None
    except ValueError:return None


def _license_requirements(expression,config:dict,*,depth:int=0):
    if depth>12:return None
    if isinstance(expression,str):return _string_license_requirements(expression,config)
    if permissive_license(expression,set(config["permissive_license_tokens"])):return ()
    source=set(config.get("source_required_license_tokens",()))
    if not isinstance(expression,dict) or len(expression)!=1:return None
    key,value=next(iter(expression.items()))
    if key in {"any_of","all_of"}:
        if not isinstance(value,list) or not 0<len(value)<=64:return None
        choices=[_license_requirements(item,config,depth=depth+1) for item in value]
        if key=="any_of":
            valid=[item for item in choices if item is not None]
            return min(valid,key=lambda item:(len(item),item)) if valid else None
        if any(item is None for item in choices):return None
        return tuple(sorted({token for item in choices for token in item}))
    if (key in source and isinstance(value,dict) and set(value)=={"with"}
            and isinstance(value["with"],str)):
        pair=f'{key} WITH {value["with"]}'
        if pair in config.get("source_required_license_exceptions",()):return (pair,)
    return None


def allowed_redistribution(meta:dict,config:dict)->tuple[str,...]:
    expression=meta.get("license")
    requirements=_license_requirements(expression,config)
    if requirements is not None:return requirements
    source_exception=config.get("source_required_formula_exceptions",{}).get(meta["name"],{})
    if (source_exception.get("formula_sha256")==meta["formula_sha256"]
            and source_exception.get("license")==expression
            and isinstance(source_exception.get("requirement"),str) and source_exception["requirement"]
            and isinstance(source_exception.get("review"),str) and len(source_exception["review"].strip())>=30):
        return (source_exception["requirement"],)
    exception=config["redistribution_exceptions"].get(meta["name"],{})
    if exception.get("formula_sha256")==meta["formula_sha256"] and isinstance(exception.get("review"),str) and len(exception["review"].strip())>=30:return ()
    raise Error(f'{meta["name"]}: redistribution review required for {expression!r}')


def runtime_closure(name:str,nodes:dict)->list[str]:
    seen=set()
    def visit(item):
        for dep in nodes[item]["runtime"]:
            if dep not in seen:seen.add(dep);visit(dep)
    visit(name);return sorted(seen)


def _prepare_source_sets(names,work:Path):
    caches={};sources={}
    for name in names:
        cache=work/f"{name}-cache";cache.mkdir();caches[name]=cache
        sources[name]=native({"mode":"sources","name":name},ci=True,cache=cache)
    return caches,sources


def _build_env(cache:Path):
    env=brew_env(ci=True);env["HOMEBREW_CACHE"]=str(cache);return env


def _safe_archive_name(value:str)->bool:
    path=PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _building_text(name:str,core_commit:str,brew_commit:str,requirements=())->bytes:
    altered=("\nThis is a Homebrew-packaged and patched build, not an unmodified upstream "
             "Info-ZIP release. The recipe and indexed patch archive identify every applied change.\n"
             if "Info-ZIP" in requirements else "")
    return (f"Build inputs for homebrew/core/{name}\n\n"
            f"Homebrew/brew: https://github.com/Homebrew/brew/tree/{brew_commit}\n"
            f"Homebrew/core: https://github.com/Homebrew/homebrew-core/tree/{core_commit}\n\n"
            f"Rebuild command: brew install --build-bottle homebrew/core/{name}\n\n"
            "Use the pinned recipe in recipe/ with the commits above. sources.json lists the "
            "original source archives and their hashes. Its build_sources labels preserve the "
            "original Go or Cargo cache paths for files stored in build-inputs/. Build output "
            "can vary with the build environment and is not guaranteed to be bit-for-bit identical.\n"
            f"{altered}").encode()


def source_bundle(name,meta,sources,output,core_commit,brew_commit,*,requirements=(),build_sources=()):
    if sources["formula_sha256"]!=meta["formula_sha256"]:raise Error("Source recipe differs from plan")
    archive=output/f'{name}--{meta["pkg_version"]}.sources.tar.gz'
    if archive.exists():raise Error("Refusing to overwrite a source bundle")
    index={"schema":1,"name":name,"core_commit":core_commit,"formula_sha256":meta["formula_sha256"],
           "license":meta.get("license"),"license_requirements":list(requirements),"resources":[],"notices":[],"build_sources":[]}
    with tarfile.open(archive,"x:gz") as bundle:
        recipe=Path(sources["formula_path"])
        if recipe.is_symlink() or not recipe.is_file() or digest(recipe)!=meta["formula_sha256"]:raise Error("Recipe changed during source collection")
        bundle.add(recipe,arcname=f"recipe/{name}.rb",recursive=False)
        bundle.add(ROOT/"LICENSES/Homebrew-BSD-2-Clause.txt",arcname="LICENSES/Homebrew-BSD-2-Clause.txt",recursive=False)
        building=_building_text(name,core_commit,brew_commit,requirements);info=tarfile.TarInfo("BUILDING.txt");info.size=len(building);info.mode=0o644;info.mtime=0;bundle.addfile(info,io.BytesIO(building))
        for i,res in enumerate(sources["resources"]):
            path=Path(res["path"])
            if path.is_symlink() or not path.is_file() or digest(path)!=res["sha256"]:raise Error("Source archive changed")
            filename=f'{i:03d}-{basename(path.name)}';bundle.add(path,arcname=f"inputs/{filename}",recursive=False)
            index["resources"].append({"filename":f"inputs/{filename}","label":res["label"],"url":res["url"],"sha256":res["sha256"]})
            for source_name,data in archive_notices(path, MAX_JSON, MAX_SOURCE_NOTICES):
                if len(index["notices"])>=MAX_SOURCE_NOTICES:raise Error("Too many upstream license notices")
                notice_name=f"upstream-notices/{i:03d}-{len(index['notices']):03d}-{basename(PurePosixPath(source_name).name)}"
                info=tarfile.TarInfo(notice_name);info.size=len(data);info.mode=0o644;info.mtime=0;bundle.addfile(info,io.BytesIO(data))
                index["notices"].append({"filename":notice_name,"source":source_name,"sha256":hashlib.sha256(data).hexdigest()})
        for i,item in enumerate(build_sources):
            path=Path(item["path"]);sha=require_sha(item["sha256"])
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=item["size"] or digest(path)!=sha:raise Error("Build source changed")
            mode=item["mode"]
            if type(mode) is not int or mode not in (0o644,0o755) or path.stat().st_mode&0o7777!=mode:raise Error("Build source mode changed")
            filename=f"build-inputs/{i:03d}-{basename(path.name)}";bundle.add(path,arcname=filename,recursive=False)
            index["build_sources"].append({"filename":filename,"label":item["label"],"sha256":sha,"size":item["size"],"mode":mode})
        if requirements and not index["notices"]:raise Error(f"{name}: source-required license notice missing from upstream archives")
        data=(json.dumps(index,indent=2)+"\n").encode()
        if len(data)>MAX_JSON:raise Error("Source index exceeds budget")
        info=tarfile.TarInfo("sources.json");info.size=len(data);info.mode=0o644;info.mtime=0;bundle.addfile(info,io.BytesIO(data))
    if archive.stat().st_size>2_000_000_000:raise Error("Source bundle exceeds budget")
    return archive


def _validate_source_bundle(path:Path,record:dict,config:dict)->None:
    requirements=allowed_redistribution(record,config)
    if not requirements:return # Compatibility with already-built permissive candidates.
    regular={};index_data=None;total=members=0
    try:
        with tarfile.open(path,"r:*") as archive:
            for member in archive:
                members+=1
                if members>110_000:raise Error("Source bundle has too many members")
                if not _safe_archive_name(member.name) or member.name in regular:raise Error("Invalid source bundle member")
                if member.isdir():continue
                if not member.isfile():raise Error("Source bundle contains links or special files")
                total+=member.size
                if total>2_000_000_000:raise Error("Source bundle expansion exceeds budget")
                regular[member.name]=member
                if member.name=="sources.json":
                    if member.size>MAX_JSON:raise Error("Source index exceeds budget")
                    handle=archive.extractfile(member);index_data=handle.read(MAX_JSON+1) if handle else b""
            if index_data is None:raise Error("Source bundle index missing")
            index=json.loads(index_data)
            required={"schema","name","core_commit","formula_sha256","license","license_requirements","resources","notices","build_sources"}
            if (not isinstance(index,dict) or set(index)!=required or index["schema"]!=1
                    or index["name"]!=record["name"] or index["core_commit"]!=record["core_commit"]
                    or index["formula_sha256"]!=record["formula_sha256"] or index["license"]!=record["license"]
                    or index["license_requirements"]!=list(requirements)):raise Error("Source bundle identity mismatch")
            resources=index["resources"];notices=index["notices"];build_sources=index["build_sources"]
            if not isinstance(resources,list) or not 0<len(resources)<=400:raise Error("Invalid source resource index")
            if not isinstance(notices,list) or not 0<len(notices)<=MAX_SOURCE_NOTICES:raise Error("Source-required license notices missing")
            if not isinstance(build_sources,list) or len(build_sources)>MAX_BUILD_SOURCE_FILES:raise Error("Invalid build source index")
            if sum(item.get("label")=="main" for item in resources if isinstance(item,dict))!=1:raise Error("Main source resource missing")
            expected={f"recipe/{record['name']}.rb","LICENSES/Homebrew-BSD-2-Clause.txt","BUILDING.txt","sources.json"}
            def member_hash(filename,*,size=None):
                member=regular.get(filename)
                if member is None or (size is not None and member.size!=size):raise Error("Indexed source file missing")
                handle=archive.extractfile(member)
                if handle is None:raise Error("Indexed source file missing")
                value=hashlib.sha256()
                for chunk in iter(lambda:handle.read(1024*1024),b""):value.update(chunk)
                return value.hexdigest()
            if member_hash(f"recipe/{record['name']}.rb")!=record["formula_sha256"]:raise Error("Source recipe checksum mismatch")
            building=regular.get("BUILDING.txt")
            handle=archive.extractfile(building) if building else None
            if handle is None or handle.read(MAX_JSON+1)!=_building_text(record["name"],record["core_commit"],record["brew_commit"],requirements):raise Error("Source build instructions mismatch")
            for item in resources:
                if not isinstance(item,dict) or set(item)!={"filename","label","url","sha256"}:raise Error("Invalid source resource")
                if not item["filename"].startswith("inputs/") or not _safe_archive_name(item["filename"]):raise Error("Invalid source resource path")
                if item["filename"] in expected:raise Error("Duplicate source resource")
                if member_hash(item["filename"])!=require_sha(item["sha256"]):raise Error("Source resource checksum mismatch")
                expected.add(item["filename"])
            for item in build_sources:
                if not isinstance(item,dict) or set(item)!={"filename","label","sha256","size","mode"}:raise Error("Invalid build source")
                if not item["filename"].startswith("build-inputs/") or not _safe_archive_name(item["filename"]):raise Error("Invalid build source path")
                if item["filename"] in expected:raise Error("Duplicate build source")
                if type(item["size"]) is not int or item["size"]<0 or member_hash(item["filename"],size=item["size"])!=require_sha(item["sha256"]):raise Error("Build source checksum mismatch")
                if type(item["mode"]) is not int or item["mode"] not in (0o644,0o755) or regular[item["filename"]].mode!=item["mode"]:raise Error("Build source mode mismatch")
                expected.add(item["filename"])
            for item in notices:
                if not isinstance(item,dict) or set(item)!={"filename","source","sha256"} or not _safe_archive_name(item["source"]):raise Error("Invalid source notice")
                filename=item["filename"];member=regular.get(filename)
                if not filename.startswith("upstream-notices/") or member is None or member.size>MAX_JSON:raise Error("Invalid source notice")
                if filename in expected:raise Error("Duplicate source notice")
                handle=archive.extractfile(member);data=handle.read(MAX_JSON+1) if handle else b""
                if hashlib.sha256(data).hexdigest()!=require_sha(item["sha256"]):raise Error("Source notice checksum mismatch")
                expected.add(filename)
            if set(regular)!=expected:raise Error("Unexpected source bundle files")
    except (tarfile.TarError,json.JSONDecodeError,EOFError) as exc:raise Error("Invalid source bundle") from exc


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
    license_requirements={name:allowed_redistribution(plan["nodes"][name],config) for name in to_build}
    core_commit=require_sha(os.environ["INTELBREW_CORE_COMMIT"],git=True);workflow_commit=require_sha(os.environ["GITHUB_SHA"],git=True)
    manifest={"schema":1,"root":root,"core_commit":core_commit,"brew_commit":config["brew_commit"],"workflow_commit":workflow_commit,"plan":plan,"packages":[],"verified":False}
    if not to_build:write_json_new(output/"manifest.json",manifest);return
    work=Path(tempfile.mkdtemp(prefix="intelbrew-build-",dir=os.environ["RUNNER_TEMP"]))
    package_caches,source_sets=_prepare_source_sets(to_build,work)
    for name in plan["order"]:
        meta=plan["nodes"][name]
        if meta["provider"]=="official":install_binary(name,meta,as_dependency=name!=root);continue
        if meta["provider"]=="personal":
            rec=records[name];path=work/rec["sha256"]/rec["filename"];download(artifact_url(config["repository"],rec),path,rec["sha256"],rec["size"]);attest(path,config["repository"],rec["workflow_commit"]);check_bottle(path,rec);install_binary(name,meta,local=path,sha=rec["sha256"],as_dependency=name!=root);continue
        if meta["provider"]!="build":raise Error("Unexpected CI package provider")
        sources=source_sets[name];cache=package_caches[name];env=_build_env(cache)
        run(["brew","install","--build-bottle","--no-ask",f"homebrew/core/{name}"],capture=False,env=env)
        context=native({"mode":"build-context"},ci=True,cache=cache)
        collected=collect_build_sources(context,work/f"{name}-build-sources")
        source_path=source_bundle(name,meta,sources,output,core_commit,config["brew_commit"],
                                  requirements=license_requirements[name],build_sources=collected["files"])
        bw=work/name;bw.mkdir();run(["brew","bottle",*BOTTLE_OPTIONS,f"homebrew/core/{name}"],capture=False,env=env,cwd=bw)
        files=list(bw.glob("*.sequoia.bottle*.tar.gz"));js=list(bw.glob("*.bottle.json"))
        if len(files)!=1 or len(js)!=1:raise Error("Expected one Sequoia bottle and JSON")
        details=read_json(js[0]);bottle_sha,bottle_cellar=extract_bottle_metadata(details,"sequoia");filename=basename(files[0].name);target=output/filename;shutil.copyfile(files[0],target)
        if digest(target)!=bottle_sha:raise Error("Bottle checksum mismatch")
        rec={"schema":1,"name":name,"version":meta["version"],"revision":meta["revision"],"version_scheme":meta["version_scheme"],"pkg_version":meta["pkg_version"],"formula_sha256":meta["formula_sha256"],"recipe_sha256":sources["recipe_sha256"],"core_commit":core_commit,"brew_commit":config["brew_commit"],"tag":"sequoia","arch":"x86_64","cellar":bottle_cellar,"filename":filename,"sha256":digest(target),"size":target.stat().st_size,"license":meta["license"],"runtime_dependencies":[{"name":d,"pkg_version":plan["nodes"][d]["pkg_version"],"formula_sha256":plan["nodes"][d]["formula_sha256"]} for d in runtime_closure(name,plan["nodes"])],"source":{"filename":source_path.name,"sha256":digest(source_path),"size":source_path.stat().st_size},"release":None,"workflow_commit":workflow_commit,"run_id":os.environ["GITHUB_RUN_ID"]}
        check_bottle(target,rec);manifest["packages"].append(rec)
    write_json_new(output/"manifest.json",manifest);validate_candidate(output,expected_root=root,verified=False)


def validate_candidate(directory:Path,*,expected_root:str,verified=None)->dict:
    if directory.is_symlink() or not directory.is_dir():raise Error("Candidate directory invalid")
    config=load_config()
    if sum(p.stat().st_size for p in directory.iterdir() if p.is_file())>config["max_candidate_bytes"]:raise Error("Candidate exceeds transfer budget")
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
        notices=config.get("required_license_notices",{}).get(rec["name"],{})
        check_bottle(directory/rec["filename"],rec,license_notices=notices);src=rec["source"];sp=directory/src["filename"]
        if sp.is_symlink() or not sp.is_file() or sp.stat().st_size!=src["size"] or digest(sp)!=src["sha256"]:raise Error("Source bundle missing/corrupt")
        _validate_source_bundle(sp,rec,config)
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
