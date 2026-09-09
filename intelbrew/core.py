# SPDX-License-Identifier: BSD-2-Clause
"""Pure planning, validation and transport. No Homebrew mutations happen here."""
from __future__ import annotations
import hashlib,json,os,re,subprocess,tarfile,urllib.parse,urllib.request
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1];NAME=re.compile(r"[a-z0-9][a-z0-9+_.-]*(?:@[0-9][a-z0-9+_.-]*)?\Z");SHA256=re.compile(r"[0-9a-f]{64}\Z");SHA1=re.compile(r"[0-9a-f]{40}\Z");TAG=re.compile(r"intel-[0-9]+-[0-9]+-[a-z0-9_.+-]+\Z");MAX_JSON=8*1024*1024;MAX_ARTIFACT=2_000_000_000
RECORD_KEYS={"schema","name","version","revision","version_scheme","pkg_version","formula_sha256","recipe_sha256","core_commit","brew_commit","tag","arch","cellar","filename","sha256","size","license","runtime_dependencies","source","release","workflow_commit","run_id"}
class Error(RuntimeError):pass

def canonical_name(value):
    if isinstance(value,str) and value.startswith('homebrew/core/'):value=value.removeprefix('homebrew/core/')
    if not isinstance(value,str) or not NAME.fullmatch(value) or '..' in value or len(value)>120:raise Error(f'Not a canonical core formula name: {value!r}')
    return value

def basename(value):
    if not isinstance(value,str) or not value or len(value)>240 or value in {'.','..'} or Path(value).name!=value or not re.fullmatch(r'[A-Za-z0-9@+_.-]+',value):raise Error(f'Unsafe artifact filename: {value!r}')
    return value

def require_sha(value,*,git=False):
    if not isinstance(value,str) or not (SHA1 if git else SHA256).fullmatch(value):raise Error('Invalid commit or SHA-256 digest')
    return value

def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_JSON:raise Error(f'Not a regular bounded JSON file: {path}')
    try:return json.loads(path.read_text())
    except (ValueError,UnicodeError) as exc:raise Error(f'Invalid JSON: {exc}') from exc

def write_json_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as h:json.dump(data,h,indent=2,sort_keys=True);h.write('\n')

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def load_config():
    c=read_json(ROOT/'policy/config.json')
    if c.get('schema')!=1 or c.get('repository')!='adriank1410/homebrew-intel':raise Error('Unexpected configuration')
    require_sha(c['brew_commit'],git=True);return c

def validate_record(d,*,published=True):
    if not isinstance(d,dict) or set(d)!=RECORD_KEYS or d.get('schema')!=1:raise Error('Unknown/incomplete record schema')
    n=canonical_name(d['name']);[require_sha(d[x]) for x in ('formula_sha256','recipe_sha256','sha256')];[require_sha(d[x],git=True) for x in ('core_commit','brew_commit','workflow_commit')]
    if any(type(d[x]) is not int or d[x]<0 for x in ('revision','version_scheme')):raise Error('Invalid revision')
    if not isinstance(d['version'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,150}',d['version']):raise Error('Invalid version')
    if d['pkg_version']!=d['version']+(f'_{d["revision"]}' if d['revision'] else ''):raise Error('Version/revision mismatch')
    if d['tag']!='sequoia' or d['arch']!='x86_64' or d['cellar'] not in ('/usr/local/Cellar','any','any_skip_relocation'):raise Error('Wrong platform/cellar')
    fn=basename(d['filename']);prefix=f'{n}--{d["pkg_version"]}.sequoia.bottle'
    if not fn.startswith(prefix) or not re.fullmatch(re.escape(prefix)+r'(?:\.\d+)?\.tar\.gz',fn):raise Error('Bottle filename mismatch')
    if type(d['size']) is not int or not 0<d['size']<=MAX_ARTIFACT:raise Error('Invalid size')
    if not isinstance(d['run_id'],str) or not d['run_id'].isdigit():raise Error('Invalid run id')
    deps=d['runtime_dependencies']
    if not isinstance(deps,list) or len(deps)>1000:raise Error('Invalid dependencies')
    for dep in deps:
        if not isinstance(dep,dict) or set(dep)!={'name','pkg_version','formula_sha256'}:raise Error('Invalid dependency record')
        canonical_name(dep['name']);require_sha(dep['formula_sha256'])
    src=d['source']
    if not isinstance(src,dict) or set(src)!={'filename','sha256','size'} or not basename(src['filename']).endswith('.sources.tar.gz'):raise Error('Invalid source bundle')
    require_sha(src['sha256'])
    if type(src['size']) is not int or not 0<src['size']<=MAX_ARTIFACT:raise Error('Invalid source size')
    if published:
        if not isinstance(d['release'],str) or not TAG.fullmatch(d['release']):raise Error('Invalid immutable release')
    elif d['release'] is not None:raise Error('Unpublished record selects release')
    return d

def registry():
    out={}
    for p in sorted((ROOT/'registry').glob('*.json')):
        item=validate_record(read_json(p))
        if p.stem!=item['name'] or item['name'] in out:raise Error('Duplicate registry record')
        out[item['name']]=item
    return out

def matching_record(meta,records):
    r=records.get(meta['name']);return r if r and all(r[k]==meta[k] for k in ('pkg_version','formula_sha256','version_scheme')) else None

def brew_env(*,ci=False):
    env=os.environ.copy()
    for k in ('HOMEBREW_BUILD_FROM_SOURCE','HOMEBREW_INSTALL_FROM_API','HOMEBREW_DEVELOPER','HOMEBREW_DEBUG','RUBYOPT','RUBYLIB','GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','ACTIONS_ID_TOKEN_REQUEST_TOKEN','ACTIONS_ID_TOKEN_REQUEST_URL'):env.pop(k,None)
    env.update(HOMEBREW_NO_AUTO_UPDATE='1',HOMEBREW_NO_INSTALL_CLEANUP='1',HOMEBREW_NO_ANALYTICS='1',HOMEBREW_NO_ENV_HINTS='1',HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK='1',HOMEBREW_NO_ASK='1')
    if ci:env['HOMEBREW_NO_INSTALL_FROM_API']='1'
    return env

def run(args,*,capture=True,input_text=None,env=None,cwd=None):
    try:p=subprocess.run(args,input=input_text,text=True,stdout=subprocess.PIPE if capture else None,stderr=subprocess.PIPE if capture else None,env=env,cwd=cwd)
    except OSError as exc:raise Error(f'Cannot execute {args[0]}: {exc}') from exc
    if p.returncode:raise Error(f'{args[0]} failed ({p.returncode}): {(p.stderr or "").strip() if capture else "see output"}')
    return p.stdout or ''

def native(request,*,capture=True,ci=False,cache=None):
    env=brew_env(ci=ci)
    if cache is not None:env['HOMEBREW_CACHE']=os.fspath(cache)
    out=run(['brew','ruby',str(ROOT/'libexec/native.rb')],input_text=json.dumps(request),capture=capture,env=env)
    if not capture:return None
    try:return json.loads(out)
    except ValueError as exc:raise Error('Native bridge returned non-JSON') from exc

class Planner:
    def __init__(self,inspect,records,*,build=False,max_nodes=400,blocked=()):self.inspect=inspect;self.records=records;self.build=build;self.max_nodes=max_nodes;self.blocked=set(blocked);self.nodes={}
    def make(self,roots):
        requested=list(dict.fromkeys(canonical_name(n) for n in roots))
        if not requested:raise Error('At least one formula required')
        self.nodes={};pending=set(requested)
        while pending:
            if len(self.nodes)+len(pending)>self.max_nodes:raise Error(f'Dependency graph exceeds {self.max_nodes} nodes')
            batch=sorted(pending);pending.clear();fetched=self.inspect(batch)
            if set(fetched)!=set(batch):raise Error('Incomplete metadata/alias')
            for name in batch:
                m=dict(fetched[name])
                if m.get('name')!=name or m.get('tap')!='homebrew/core':raise Error(f'Non-core or renamed formula: {name}')
                require_sha(m['formula_sha256'])
                if m.get('disabled') or not m.get('version'):raise Error(f'Disabled/non-stable: {name}')
                if m.get('installed_options') or m.get('installed_head'):raise Error(f'Options/HEAD needs review: {name}')
                if m.get('foreign_install'):raise Error(f'Foreign install: {name}')
                if m.get('installed_newer'):raise Error(f'Refusing downgrade: {name}')
                if m.get('pinned') and not m.get('installed_current'):raise Error(f'Pinned formula would change: {name}')
                if type(m.get('vcs_source')) is not bool:raise Error(f'Invalid source strategy metadata: {name}')
                rec=matching_record(m,self.records)
                provider='installed' if m.get('installed_current') and not self.build else 'official' if m.get('official_bottle') else 'personal' if rec else 'build' if self.build else 'missing'
                if provider=='build' and name in self.blocked:raise Error(f'Source build excluded by policy: {name}')
                if provider=='build' and m['vcs_source'] and m.get('pinned_git_source') is not True:raise Error(f'VCS source needs review: {name}')
                deps=set(m.get('runtime',[]))
                if provider=='build':deps.update(m.get('build',[]));deps.update(m.get('test',[]))
                m.update(provider=provider,dependencies=sorted(deps));self.nodes[name]=m;pending.update(deps-set(self.nodes))
            pending.difference_update(self.nodes)
        stale=set()
        for name,m in self.nodes.items():
            if m['provider']!='personal':continue
            for dep in self.records[name]['runtime_dependencies']:
                actual=self.nodes.get(dep['name'])
                if actual is None or actual['pkg_version']!=dep['pkg_version'] or actual['formula_sha256']!=dep['formula_sha256']:
                    if not self.build:raise Error(f'Personal bottle dependency drift: {name} -> {dep["name"]}; rebuild needed')
                    stale.add(name)
        if stale:return Planner(self.inspect,{n:r for n,r in self.records.items() if n not in stale},build=True,max_nodes=self.max_nodes,blocked=self.blocked).make(requested)
        ordered=[];visiting=[];done=set()
        def visit(name):
            if name in done:return
            if name in visiting:raise Error('Dependency cycle: '+' -> '.join(visiting+[name]))
            visiting.append(name)
            for dep in self.nodes[name]['dependencies']:visit(dep)
            visiting.pop();done.add(name);ordered.append(name)
        for n in requested:visit(n)
        return {'schema':1,'roots':requested,'order':ordered,'nodes':self.nodes}

def ensure_complete(plan):
    missing=[n for n in plan['order'] if plan['nodes'][n]['provider']=='missing']
    if missing:raise Error('No compatible matching bottle; nothing installed: '+', '.join(missing))

def artifact_url(repository,record,filename=None):
    validate_record(record)
    if repository!='adriank1410/homebrew-intel':raise Error('Unexpected artifact repository')
    name=basename(filename or record['filename'])
    if name not in {record['filename'],record['source']['filename']}:raise Error('Filename not in inventory')
    return f'https://github.com/{repository}/releases/download/{record["release"]}/{urllib.parse.quote(name)}'

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        u=urllib.parse.urlparse(newurl)
        if u.scheme!='https' or u.hostname not in {'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'} or u.username or u.password:raise Error('Unexpected artifact redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def download(url,target,expected_sha,expected_size):
    require_sha(expected_sha);u=urllib.parse.urlparse(url)
    if not 0<expected_size<=MAX_ARTIFACT or u.scheme!='https' or u.hostname!='github.com' or u.username or u.password:raise Error('Invalid artifact URL/size')
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():raise Error('Unsafe cache entry')
        if target.stat().st_size==expected_size and digest(target)==expected_sha:return target
        raise Error(f'Existing cache file invalid; retained: {target}')
    target.parent.mkdir(parents=True,exist_ok=True);part=target.with_name(target.name+f'.partial-{os.getpid()}');opener=urllib.request.build_opener(SafeRedirect());h=hashlib.sha256();total=0
    try:
        with opener.open(url,timeout=60) as response,part.open('xb') as out:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>expected_size:raise Error('Artifact exceeds expected length')
                out.write(chunk);h.update(chunk)
    except (OSError,ValueError) as exc:raise Error(f'Download failed; partial retained at {part}: {exc}') from exc
    if total!=expected_size or h.hexdigest()!=expected_sha:raise Error(f'Checksum/size mismatch; retained at {part}')
    os.link(part,target);return target

def check_bottle(path,record,*,license_notices=None):
    validate_record(record,published=record['release'] is not None)
    if path.is_symlink() or path.stat().st_size!=record['size'] or digest(path)!=record['sha256']:raise Error('Bottle digest/size mismatch')
    base=PurePosixPath(record['name'],record['pkg_version']);recipe_name=str(base/'.brew'/(record['name']+'.rb'));tab_name=str(base/'INSTALL_RECEIPT.json');recipe=tab=None;seen=set();symlinks=set();regular={};expanded=0
    required_notices={str(base/basename(name)):require_sha(sha) for name,sha in (license_notices or {}).items()}
    found_notices=set()
    try:
        with tarfile.open(path,'r:gz') as ar:
            for entry in ar:
                name=PurePosixPath(entry.name);norm=str(name)
                if name.is_absolute() or '..' in name.parts or norm in seen:raise Error('Unsafe/duplicate archive member')
                seen.add(norm)
                if entry.issym():symlinks.add(name)
                if len(seen)>250000:raise Error('Too many archive members')
                if not (name==PurePosixPath(record['name']) or name==base or base in name.parents):raise Error('Files outside declared keg')
                if not (entry.isfile() or entry.isdir() or entry.issym() or entry.islnk()):raise Error('Unsupported special file')
                if entry.islnk():
                    target=PurePosixPath(entry.linkname)
                    if target.is_absolute() or '..' in target.parts or str(target) not in regular:
                        raise Error('Unsafe or unresolved archive hard link')
                    expanded+=regular[str(target)]
                elif entry.isfile():regular[norm]=entry.size
                expanded+=entry.size
                if expanded>15_000_000_000:raise Error('Expansion budget exceeded')
                if norm in required_notices:
                    if not entry.isfile() or not 0<entry.size<=MAX_JSON:raise Error('Invalid required license notice')
                    notice=ar.extractfile(entry).read(MAX_JSON+1)
                    if hashlib.sha256(notice).hexdigest()!=required_notices[norm]:raise Error('Required license notice changed; review needed')
                    found_notices.add(norm)
                if norm in {recipe_name,tab_name}:
                    if not entry.isfile() or entry.size>MAX_JSON:raise Error('Invalid embedded metadata')
                    value=ar.extractfile(entry).read(MAX_JSON+1)
                    if norm==recipe_name:recipe=value
                    else:tab=json.loads(value)
    except (tarfile.TarError,ValueError,OSError) as exc:raise Error(f'Invalid bottle archive: {exc}') from exc
    for filename in seen:
        if any(parent in symlinks for parent in PurePosixPath(filename).parents):raise Error('Bottle writes beneath archive symlink')
    if found_notices!=set(required_notices):raise Error('Required license notice missing')
    if recipe is None or hashlib.sha256(recipe).hexdigest()!=record['recipe_sha256']:raise Error('Embedded formula differs')
    if not isinstance(tab,dict) or tab.get('source',{}).get('tap')!='homebrew/core' or not tab.get('built_as_bottle'):raise Error('Bottle does not retain core identity')
