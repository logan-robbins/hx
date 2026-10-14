"""Content-addressed artifact manifests for fixed-source CI jobs."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from .continuity_store import Conflict,canonical,digest
from .errors import ValidationError


def manifest(repository,artifacts,checks,*,expected_head=None):
    repo=Path(repository).resolve()
    def git(*args):
        result=subprocess.run(['git','--no-optional-locks','-C',str(repo),*args],capture_output=True,check=True)
        return result.stdout.decode().strip()
    head=git('rev-parse','HEAD')
    if expected_head is not None and head!=expected_head:
        raise Conflict('CI source differs from its frozen commit')
    if git('status','--porcelain','--untracked-files=no'):
        raise Conflict('CI manifest requires unchanged tracked source')
    if not checks or any(value not in {'passed','success'} for value in checks.values()):
        raise ValidationError('CI artifact publication requires every declared check to pass')
    files={}
    for name in artifacts:
        path=(repo/name).resolve()
        if not path.is_relative_to(repo) or not path.is_file() or (repo/name).is_symlink():
            raise ValidationError('CI artifacts must be exact regular files inside the source workspace')
        hasher=hashlib.sha256()
        with path.open('rb') as source:
            while chunk:=source.read(65536):
                hasher.update(chunk)
        files[path.relative_to(repo).as_posix()]={'sha256':hasher.hexdigest(),'bytes':path.stat().st_size}
    if not files:
        raise ValidationError('CI manifest requires a tested artifact')
    body={'schema_version':1,'source_commit':head,'source_tree':git('rev-parse','HEAD^{tree}'),
          'artifacts':files,'checks':{name:'passed' for name in checks}}
    return {**body,'build_id':digest(body)}


def main(argv=None):
    parser=argparse.ArgumentParser(prog='python -m hx.ci_manifest')
    parser.add_argument('--repo',default='.')
    parser.add_argument('--expected-head',required=True)
    parser.add_argument('--artifact',action='append',required=True)
    parser.add_argument('--check',action='append',required=True,help='NAME=passed or NAME=success from the CI job result')
    parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    statuses={}
    for declaration in args.check:
        if '=' not in declaration:
            raise ValidationError('CI check requires NAME=STATUS')
        name,status=declaration.split('=',1)
        if not name or name in statuses:
            raise ValidationError('CI check names must be unique')
        statuses[name]=status
    result=manifest(args.repo,args.artifact,statuses,expected_head=args.expected_head)
    Path(args.out).write_text(canonical(result)+'\n')
    print(json.dumps({'build_id':result['build_id'],'source_commit':result['source_commit'],'manifest':str(Path(args.out).resolve())}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
