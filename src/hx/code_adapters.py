"""Optional syntax tools. Unknown versions/output degrade to stdlib facts."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from . import code_extract
from .code_store import span_sha

SCIP_BINARIES=('scip-python','scip-typescript','scip-go','scip-java','scip-clang','scip-ruby','scip-dotnet','rust-analyzer')


def run(argv,*,cwd=None,timeout=30,maximum=8*1024*1024):
    """Bound execution time and captured output, including stderr."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        with subprocess.Popen(argv,cwd=cwd,stdout=out,stderr=err) as process:
            deadline=time.monotonic()+timeout
            while process.poll() is None:
                if time.monotonic()>deadline or os.fstat(out.fileno()).st_size+os.fstat(err.fileno()).st_size>maximum:
                    process.kill(); process.wait()
                    raise ValueError('extractor exceeded its time/output budget')
                time.sleep(.02)
            if process.returncode: raise ValueError('extractor exited '+str(process.returncode))
            out.seek(0)
            raw=out.read(maximum+1)
            if len(raw)>maximum: raise ValueError('extractor output exceeds budget')
            return raw.decode('utf-8',errors='replace')


def detect():
    result={'stdlib':True,'tree-sitter':{'available':False},'ctags':{'available':False},'scip':shutil.which('scip'),
            'indexers':{name:shutil.which(name) for name in SCIP_BINARIES}}
    for tool in ('tree-sitter','ctags'):
        binary=shutil.which(tool)
        if not binary: continue
        try: version=run([binary,'--version'],maximum=8192).strip()
        except (OSError,ValueError): continue
        available='Universal Ctags' in version if tool=='ctags' else version.startswith('tree-sitter 0.25.10')
        result[tool]={'available':available,'binary':binary,'version':version}
    grammars=[]
    for directory in os.environ.get('HX_TREE_SITTER_GRAMMARS','').split(os.pathsep):
        if not directory: continue
        p=Path(directory).expanduser().resolve()
        if (p/'queries/tags.scm').is_file() and (p/'src/parser.c').is_file(): grammars.append(str(p))
    result['tree-sitter'].update(grammars=grammars,compiler=shutil.which('cc') or shutil.which('gcc') or shutil.which('clang'))
    result['tree-sitter']['available'] &= bool(grammars and result['tree-sitter']['compiler'])
    return result


def parse_tags(output):
    pattern=re.compile(r'^\s*(\S+)\s*\|\s*(\w+)\s+(def|ref)\s+\((\d+),\s*(\d+)\)\s*-\s*\((\d+),\s*(\d+)\)')
    rows=[]
    for line in output.splitlines():
        m=pattern.match(line)
        if m: rows.append(dict(name=m[1],kind=m[2],role=m[3],line=int(m[4])+1,end_line=int(m[6])+1,column=int(m[5])))
        elif line.strip(): raise ValueError('unrecognized tree-sitter 0.25.10 tags output')
    return rows


def _merge(text,path,base,refs,rows,extractor):
    by_key={(s['name'],s['line']):dict(s) for s in base}
    lines=text.splitlines(keepends=True)
    for row in rows:
        line=row['line']
        if line<1 or line>len(lines): raise ValueError('tag location outside source')
        if row.get('role')=='ref':
            refs.append(dict(name=row['name'],qualified=row['name'],path=path,line=line,kind='calls',extractor=extractor))
            continue
        key=(row['name'],line)
        found=by_key.get(key)
        if found is None:
            # Decorated stdlib declarations can start above the tag name.
            found=next((s for s in by_key.values() if s['name']==row['name'] and s['line']<=line<=s['end_line']),None)
        if found:
            found['extractor']=extractor
            if row.get('signature'): found['signature']=row['signature']
            continue
        start=sum(len(x) for x in lines[:line-1])+len(lines[line-1])-len(lines[line-1].lstrip())
        code=code_extract.mask(text)
        h=code_extract.header_end(code,start,python=path.endswith('.py'))
        end_line=row.get('end_line',line)
        if end_line==line and h<len(code) and code[h]=='{':
            depth=1; end=h
            for end in range(h+1,len(code)):
                depth += (code[end]=='{')-(code[end]=='}')
                if depth==0: break
            end_line=text.count('\n',0,end)+1
        scope=row.get('scope','').replace('::','.')
        s=dict(name=row['name'],qualified='.'.join(filter(None,[scope,row['name']])),kind=row['kind'],path=path,
               line=line,end_line=end_line,signature=row.get('signature') or re.sub(r'\s+',' ',text[start:h]).strip(),
               lane='syntax',extractor=extractor,attributes=[],anchor_sha=span_sha(text,line,end_line))
        by_key[key]=s
    return list(by_key.values()),refs


def syntax(repo,path,text,symbols,refs,detected):
    ts=detected['tree-sitter']
    if ts['available']:
        # Compiler and parser output live with the operator's grammar, never the product.
        for grammar in ts['grammars']:
            try:
                rows=parse_tags(run([ts['binary'],'tags',str(repo/path)],cwd=grammar))
                if rows: return _merge(text,path,symbols,list(refs),rows,'tree-sitter-0.25.10')
            except (OSError,ValueError): pass
    ctags=detected['ctags']
    if ctags['available']:
        try:
            output=run([ctags['binary'],'--output-format=json','--fields=+neKS','--extras=-F','-f','-',str(repo/path)],cwd=repo)
            rows=[]
            for line in output.splitlines():
                row=json.loads(line)
                if row.get('_type')=='tag' and row.get('line'):
                    rows.append(dict(name=row['name'],kind=row.get('kind','symbol'),line=int(row['line']),
                                     end_line=int(row.get('end',row['line'])),scope=row.get('scope',''),signature=row.get('signature')))
            if rows: return _merge(text,path,symbols,list(refs),rows,'universal-ctags')
        except (OSError,ValueError,KeyError): pass
    return symbols,refs


def resolved(conn,identity,repo,checkout):
    from .code_scip import index
    return index(conn,identity,repo,checkout)
