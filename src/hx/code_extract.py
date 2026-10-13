"""Build-free declaration spans for Python and common brace languages.

This lane records syntactic candidates, never claims type resolution.
"""
from __future__ import annotations
import ast
import re
from pathlib import Path
from .code_store import span_sha

LANGUAGES = {'.py':'python','.cs':'csharp','.ts':'typescript','.tsx':'typescript',
             '.js':'javascript','.jsx':'javascript','.go':'go','.rs':'rust'}


def mask(text):
    """Blank comments and literal contents, preserving offsets and line breaks."""
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|`(?:\\.|[^`\\])*`'
    return re.sub(pattern, lambda m: ''.join('\n' if c=='\n' else ' ' for c in m[0]), text)


def header_end(code, start, *, python=False):
    depth = 0
    for i in range(start,len(code)):
        c = code[i]
        if c in '([': depth += 1
        elif c in ')]': depth -= 1
        elif depth == 0 and (c in '{;' or (python and c==':') or code[i:i+2]=='=>'):
            return i
    return len(code)


def _symbol(text, path, name, qualified, kind, start, end, header, attributes=()):
    line = text.count('\n',0,start)+1
    end_line = text.count('\n',0,end)+1
    return dict(name=name,qualified=qualified,kind=kind,path=path,line=line,end_line=end_line,
                signature=re.sub(r'\s+',' ',text[start:header]).strip(),lane='syntax',
                extractor='stdlib-'+LANGUAGES.get(Path(path).suffix,'text'),
                anchor_sha=span_sha(text,line,end_line),attributes=list(attributes))


def python(text,path):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [],[],[]
    lines = text.splitlines(keepends=True)
    offsets=[0]
    for line in lines: offsets.append(offsets[-1]+len(line))
    symbols,refs,edges=[],[],[]
    def visit(node,prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                q='.'.join(filter(None,[prefix,child.name]))
                start=offsets[child.lineno-1]+child.col_offset
                end=offsets[child.end_lineno-1]+child.end_col_offset-1
                attrs=[ast.get_source_segment(text,d) or '' for d in child.decorator_list]
                row=_symbol(text,path,child.name,q,'class' if isinstance(child,ast.ClassDef) else 'function',
                            start,end,header_end(mask(text),start,python=True),attrs)
                if child.decorator_list:
                    row['line']=min(d.lineno for d in child.decorator_list)
                    row['anchor_sha']=span_sha(text,row['line'],row['end_line'])
                symbols.append(row)
                visit(child,q)
            else: visit(child,prefix)
    visit(tree)
    for node in ast.walk(tree):
        if isinstance(node,ast.Call):
            name=ast.unparse(node.func)
            refs.append(dict(name=name.rsplit('.',1)[-1],qualified=name,path=path,line=node.lineno,kind='calls'))
        elif isinstance(node,ast.Import):
            edges.extend((path,a.name,'imports') for a in node.names)
        elif isinstance(node,ast.ImportFrom):
            name='.'*node.level+(node.module or '')
            if name: edges.append((path,name,'imports'))
    return symbols,refs,edges


_TYPES = re.compile(r'\b(namespace|class|interface|struct|enum|trait|mod|type|impl)\s+([A-Za-z_$][\w$]*)')
_FUNCTIONS = re.compile(r'\b(?:function\s*\*?\s*|fn\s+|func\s+(?:\([^)]*\)\s*)?)([A-Za-z_$][\w$]*)\s*(?:<[^{};]*?>)?\s*\(')
_ARROWS = re.compile(r'\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?:\:[^=;]+)?=\s*(?:async\s*)?(?:\([^;]*?\)|[\w$]+)\s*(?:\:[^=;]+)?=>')
_METHODS = re.compile(r'(?:^|[;{}\n])\s*((?:(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|readonly|unsafe|partial|export|default)\s+)*(?:[\w$<>\[\],.?]+\s+)?([A-Za-z_$][\w$]*)\s*(?:<[^{};]*?>)?\s*\([^;{}]*\))', re.M)


def braces(text,path):
    code=mask(text)
    pairs,stack={},[]
    for i,c in enumerate(code):
        if c=='{': stack.append(i)
        elif c=='}' and stack: pairs[stack.pop()]=i
    declarations=[]
    for pattern in (_TYPES,_FUNCTIONS,_ARROWS):
        for m in pattern.finditer(code):
            is_type=pattern is _TYPES
            kind=m[1] if is_type else 'function'
            name=m[2] if is_type else m[1]
            start=m.start()
            # Include modifiers from the same declaration line.
            beginning=max(code.rfind('\n',0,start),code.rfind('{',0,start),code.rfind('}',0,start),code.rfind(';',0,start))+1
            prefix=code[beginning:start].strip()
            if not prefix or re.fullmatch(r'(?:[\w()]+\s*)+',prefix): start=beginning+len(code[beginning:start])-len(code[beginning:start].lstrip())
            h=header_end(code,m.end() if is_type else start)
            if pattern is _ARROWS:
                h=m.end()-2
                opener=code.find('{',m.end()) if code[m.end():].lstrip().startswith('{') else -1
            else: opener=h if h<len(code) and code[h]=='{' else -1
            end=pairs.get(opener,h)
            if kind=='namespace' and h<len(code) and code[h]==';': end=len(code)-1
            declarations.append((start,end,h,name,kind,opener))
    for m in _METHODS.finditer(code):
        start=m.start(1); h=header_end(code,start)
        name=m[2]
        tail=code[m.end():].lstrip()
        if tail.startswith(('.', ')', ',')) or code[start:m.end()].lstrip().startswith(('return ', 'new ', 'throw ')): continue
        containers=[d for d in declarations if d[0]<start<d[1]]
        container=max(containers,key=lambda d:d[0]) if containers else None
        if not container or container[4] not in {'class','interface','struct','trait','impl'}:
            continue
        if name in {'if','for','while','switch','catch','return','new','match'}: continue
        if any(d[0]<=start<=d[2] for d in declarations): continue
        opener=h if h<len(code) and code[h]=='{' else -1
        declarations.append((start,pairs.get(opener,h),h,name,'method',opener))
    declarations=sorted(set(declarations))
    symbols=[]
    for start,end,h,name,kind,opener in declarations:
        parents=[d for d in declarations if d[0]<start<d[1] and d[4] in {'namespace','class','interface','struct','trait','mod','impl','function','method'}]
        scope=[d[3] for d in parents]
        if Path(path).suffix=='.rs' and kind=='impl':
            match=re.search(r'\bfor\s+(\w+)',code[start:h])
            if match: name=match[1]
        if Path(path).suffix=='.go' and kind=='function':
            match=re.search(r'func\s*\(\s*\w+\s+\*?(\w+)',code[start:h])
            if match: scope.append(match[1])
        q='.'.join([*scope,name])
        before=text[max(0,text.rfind('\n',0,start-1)-300):start]
        attrs=re.findall(r'\[([^\]\n]+)\]|@([\w.]+(?:\([^\n]*\))?)|#\[([^\]\n]+)\]',before)
        row=_symbol(text,path,name,q,kind,start,end,h,[next(x for x in a if x) for a in attrs])
        symbols.append(row)
    refs=[]
    for m in re.finditer(r'\b([\w$]+(?:\.[\w$]+)*)\s*(?:<[^;{}()]*>)?\s*\(',code):
        if any(start<=m.start()<h for start,end,h,*_ in declarations): continue
        if m[1] in {'if','for','while','switch','catch','func','function','match'}: continue
        refs.append(dict(name=m[1].rsplit('.',1)[-1],qualified=m[1],path=path,line=code.count('\n',0,m.start())+1,kind='calls'))
    edges=[]
    for row in symbols:
        if row['kind']=='class':
            bases=re.search(r':\s*([^\{]+)|\bimplements\s+([^\{]+)',row['signature'])
            if bases:
                edges.extend((path,b,'implements') for b in re.findall(r'\b\w+\b',next(g for g in bases.groups() if g)))
    for m in re.finditer(r'\b(AddScoped|AddSingleton|AddTransient)\s*(?:<([^>]+)>|\(([^;]+)\))',text):
        for name in re.findall(r'[\w.]+',m[2] or m[3]):
            refs.append(dict(name=name,qualified=name,path=path,line=text.count('\n',0,m.start())+1,kind='registers'))
    suffix=Path(path).suffix
    if suffix in {'.ts','.tsx','.js','.jsx'}:
        edges.extend((path,m[1],'imports') for m in re.finditer(r'(?:\bfrom\s*|\bimport\s*|\brequire\s*\(\s*)[\x27"]([^\x27"]+)[\x27"]',text))
    elif suffix=='.cs':
        edges.extend((path,m[1],'imports') for m in re.finditer(r'\busing\s+([\w.]+)\s*;',text))
    elif suffix=='.rs':
        edges.extend((path,m[1].replace('::','.'),'imports') for m in re.finditer(r'\buse\s+([\w:]+)',text))
    elif suffix=='.go':
        for m in re.finditer(r'\bimport\s*(\([^)]*\)|(?:\w+\s+)?"[^\n"]+")',text):
            edges.extend((path,name,'imports') for name in re.findall(r'"([^"]+)"',m[1]))
    return symbols,refs,edges


def extract(text,path):
    if Path(path).suffix=='.py': return python(text,path)
    if Path(path).suffix in LANGUAGES: return braces(text,path)
    return [],[],[]
