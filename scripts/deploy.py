#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
"""Dry-run validation and historical fresh-deployment helper."""
from __future__ import annotations
import argparse, hashlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1];REPO='adriank1410/homebrew-intel';OWNER='adriank1410';MARKER='intelbrew-personal-sequoia-v1'
class Failure(RuntimeError):pass

def command(args,*,cwd=None,payload=None):
    r=subprocess.run(args,cwd=cwd,input=json.dumps(payload) if payload is not None else None,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode:raise Failure(f'{args[0]} failed ({r.returncode}): {r.stderr.strip()}')
    return r.stdout

def api(path,*,method='GET',data=None):
    args=['gh','api','--hostname','github.com','--method',method,'-H','Accept: application/vnd.github+json',path]
    if data is not None:args+=['--input','-']
    out=command(args,payload=data);return json.loads(out) if out.strip() else None

def git(args,folder):return command(['git','-c','credential.helper=','-c','credential.https://github.com.helper=!gh auth git-credential',*args],cwd=folder)

def checked_files():
    listing=json.loads((SOURCE/'distribution-files.json').read_text())
    if listing.get('schema')!=1 or not isinstance(listing.get('files'),dict):raise Failure('Invalid distribution manifest')
    result=[]
    for name,expected in listing['files'].items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0]=='.git':raise Failure('Unsafe distribution path')
        path=SOURCE/rel
        if path.is_symlink() or not path.is_file():raise Failure(f'Missing distribution file: {name}')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise Failure(f'Distribution file changed: {name}')
        result.append(rel)
    result.append(Path('distribution-files.json'));return result

def exists():
    try:r=api(f'repos/{REPO}')
    except Failure as exc:
        if 'HTTP 404' in str(exc):return False
        raise
    if r['full_name']!=REPO or r.get('private'):raise Failure('Unexpected repository identity/visibility')
    return True

def configure(report):
    report['settings']='manual-verification-required'
    print('Recommended settings: squash-only; update branch; delete merged branches; read-only default Actions;')
    print('Configure the private publishing App (see docs/OPERATIONS.md); protect main and require tests; protect v*/intel-* tags.')

def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--resume-settings',action='store_true');p.add_argument('--bootstrap',action='store_true');args=p.parse_args()
    try:
        files=checked_files();print(f'Target: {REPO}. Distribution files: {len(files)}.');print('No Homebrew command, deletion, cleanup or shell-profile edit will run.')
        if not args.apply:print('Dry run only. This repository already exists; normal operation uses reviewed commits/build requests.');return 0
        if not shutil.which('gh') or not shutil.which('git'):raise Failure('gh and git required for historical fresh deployment')
        if api('user')['login']!=OWNER:raise Failure('Wrong GitHub account')
        report_dir=Path(tempfile.mkdtemp(prefix='intelbrew-deployment-'));report={'repository':REPO,'report_directory':str(report_dir)}
        try:
            present=exists()
            if present and not args.resume_settings:raise Failure('Repository already exists; refusing source overwrite')
            if args.resume_settings:
                if not present:raise Failure('Existing repository required')
                marker=command(['gh','api','--hostname','github.com','-H','Accept: application/vnd.github.raw+json',f'repos/{REPO}/contents/.intelbrew-project'])
                if marker.strip()!=MARKER:raise Failure('Unexpected project marker')
            configure(report);report['completed']=True
        except Exception as exc:report['completed']=False;report['error']=str(exc);raise
        finally:
            with (report_dir/'deployment-report.json').open('x') as h:json.dump(report,h,indent=2);h.write('\n')
        return 0
    except (Failure,OSError,KeyError,ValueError) as exc:
        print(f'Deployment stopped: {exc}',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
