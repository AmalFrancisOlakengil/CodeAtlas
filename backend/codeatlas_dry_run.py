import argparse, base64, json, os, re, sys
from collections import defaultdict, Counter
from dataclasses import dataclass
from urllib.parse import urlparse, quote
import requests
from tree_sitter import Language, Parser
import tree_sitter_python as tspython
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts

API='https://api.github.com'; MAX_SIZE=1_000_000
EXT={'.py':'python','.java':'java','.js':'javascript','.jsx':'javascript','.ts':'typescript','.tsx':'tsx'}
IGNORE={'.git','.idea','.vscode','.cache','coverage','tmp','temp','logs','build','dist','out','target','bin','obj','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.hypothesis','.tox','.nox','.venv','venv','env','ENV','site-packages','htmlcov','.gradle','.gradle-cache','node_modules','.next','.nuxt','.nitro','.svelte-kit','.angular','.expo','.parcel-cache','.vite','.turbo','storybook-static'}
BIN={'.pyc','.pyo','.class','.jar','.war','.ear','.dll','.exe','.so','.dylib','.o','.a','.node','.png','.jpg','.jpeg','.gif','.webp','.ico','.pdf','.zip','.gz','.tar','.7z','.mp4','.mp3','.woff','.woff2','.ttf','.otf'}

@dataclass
class F: path:str; lang:str; sha:str; size:int
@dataclass
class N: id:str; typ:str; name:str; file:str|None=None; line:int|None=None; lang:str|None=None
@dataclass
class E: src:str; dst:str; typ:str; line:int|None=None
@dataclass
class S: id:str; name:str; kind:str; file:str; line:int; parent:str|None=None

def repo_url(u):
    p=urlparse(u.strip()); parts=[x for x in p.path.strip('/').split('/') if x]
    if p.netloc.lower() not in {'github.com','www.github.com'} or len(parts)<2: raise ValueError('Use https://github.com/owner/repo')
    return parts[0],parts[1].removesuffix('.git')

class GH:
    def __init__(self,token=None):
        self.s=requests.Session(); self.s.headers.update({'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','User-Agent':'CodeAtlas/0.1'})
        if token:self.s.headers['Authorization']=f'Bearer {token}'
    def get(self,path,params=None):
        r=self.s.get(path if path.startswith('http') else API+path,params=params,timeout=30)
        if r.status_code==403: raise RuntimeError('GitHub 403: rate limit or permissions')
        if r.status_code==404: raise RuntimeError('GitHub 404: repository/tree/blob not found')
        r.raise_for_status(); return r.json()
    def repo(self,o,r): return self.get(f'/repos/{o}/{r}')
    def branch(self,o,r,b): return self.get(f'/repos/{o}/{r}/branches/{quote(b,safe="")}')
    def tree(self,o,r,sha): return self.get(f'/repos/{o}/{r}/git/trees/{sha}',{'recursive':'1'})
    def blob(self,o,r,sha): return base64.b64decode(self.get(f'/repos/{o}/{r}/git/blobs/{sha}')['content'])

def skip(path,size):
    parts=set(path.replace('\\','/').lower().split('/')); name=path.rsplit('/',1)[-1].lower(); ext=os.path.splitext(name)[1]
    if parts & IGNORE:
        if 'node_modules' in parts:return 'node_modules'
        if 'target' in parts:return 'java_target'
        if 'venv' in parts or '.venv' in parts:return 'python_virtualenv'
        if '__pycache__' in parts:return 'python_cache'
        return 'ignored_directory'
    if ext in BIN:return 'binary_or_compiled'
    if name.endswith('.min.js'):return 'minified'
    if size>MAX_SIZE:return 'file_too_large'
    return None

def select(tree):
    out=[]; skipped=Counter()
    for x in tree:
        if x.get('type')!='blob':continue
        reason=skip(x['path'],x.get('size',0) or 0)
        if reason:skipped[reason]+=1;continue
        lang=EXT.get(os.path.splitext(x['path'])[1].lower())
        if not lang:skipped['unsupported_file_type']+=1;continue
        out.append(F(x['path'],lang,x['sha'],x.get('size',0) or 0))
    return out,skipped

def parser(lang):
    if lang=='python': l=Language(tspython.language())
    elif lang=='java': l=Language(tsjava.language())
    elif lang=='javascript': l=Language(tsjs.language())
    elif lang=='typescript': l=Language(tsts.language_typescript())
    else:l=Language(tsts.language_tsx())
    return Parser(l)

def txt(n,b): return b[n.start_byte:n.end_byte].decode('utf-8','replace')
def fld(n,k): return n.child_by_field_name(k)

def main():
    ap=argparse.ArgumentParser(description='CodeAtlas GitHub -> master graph dry run')
    ap.add_argument('repo_url'); ap.add_argument('--token',default=os.getenv('GITHUB_TOKEN')); ap.add_argument('--max-files',type=int,default=500); ap.add_argument('--json',dest='json_path'); a=ap.parse_args()
    owner,repo=repo_url(a.repo_url); gh=GH(a.token)
    print('='*80);print('CODEATLAS — GITHUB → MASTER GRAPH DRY RUN');print('='*80)
    meta=gh.repo(owner,repo); branch=meta['default_branch']; print(f'\n[1] {meta["full_name"]} | default branch: {branch}')
    tree_sha=gh.branch(owner,repo,branch)['commit']['commit']['tree']['sha']; td=gh.tree(owner,repo,tree_sha); tree=td['tree']
    print(f'[2] Tree entries: {len(tree)} | truncated: {td.get("truncated",False)}')
    if td.get('truncated'):print('WARNING: GitHub recursive tree is truncated; production version needs subtree traversal.')
    files,skipped=select(tree)
    if len(files)>a.max_files:print(f'[3] Found {len(files)} source files; limiting to {a.max_files}');files=files[:a.max_files]
    else:print(f'[3] Source files: {len(files)}')
    print('Languages:',dict(Counter(f.lang for f in files)));print('Skipped:',dict(skipped))

    nodes={}; edges=[]; symbols=[]; byname=defaultdict(list); file_nodes={}; pending_calls=[]; pending_imports=[]; pending_types=[]; pending_new=[]
    def node(i,t,n,file=None,line=None,lang=None):
        nodes.setdefault(i,N(i,t,n,file,line,lang));return i
    def edge(s,d,t,line=None):
        if s==d and t!='references':return
        if not any(e.src==s and e.dst==d and e.typ==t for e in edges):edges.append(E(s,d,t,line))
    def sym(i,n,k,f,l,p=None):
        x=S(i,n,k,f,l,p);symbols.append(x);byname[n].append(x)
    def addfile(f):
        fid=f'file::{f.path}';node(fid,'file',os.path.basename(f.path),f.path,1,f.lang);file_nodes[f.path]=fid;parent=f'repository::{meta["full_name"]}';node(parent,'repository',meta['full_name']);cur=''
        for part in f.path.split('/')[:-1]:
            cur=f'{cur}/{part}' if cur else part;did=f'directory::{cur}';node(did,'directory',part,cur);edge(parent,did,'contains');parent=did
        edge(parent,fid,'contains')
    for f in files:addfile(f)

    def decl(f,fid,n,name,typ,parent=None):
        i=f'{f.path}::{nodes[parent].name}::{name}' if parent else f'{f.path}::{name}';node(i,typ,name,f.path,n.start_point[0]+1,f.lang);edge(parent or fid,i,'contains');sym(i,name,typ,f.path,n.start_point[0]+1,parent);return i
    def rel(n,b,f,current):
        if not current:return
        if n.type=='call':
            q=fld(n,'function')
            if q:pending_calls.append((current,txt(q,b).split('.')[-1],f.path,n.start_point[0]+1))
        elif n.type=='method_invocation':
            q=fld(n,'name')
            if q:pending_calls.append((current,txt(q,b),f.path,n.start_point[0]+1))
        elif n.type in {'new_expression','object_creation_expression'}:
            q=fld(n,'type') or fld(n,'constructor')
            if q:pending_new.append((current,txt(q,b).split('.')[-1],f.path,n.start_point[0]+1))

    def py(root,b,f,fid):
        def walk(n,parent=None):
            if n.type in {'function_definition','async_function_definition'}:
                q=fld(n,'name')
                if q:
                    i=decl(f,fid,n,txt(q,b),'method' if parent else 'function',parent);rel(n,b,f,i)
                    for c in n.named_children:walk(c,i)
                    return
            if n.type=='class_definition':
                q=fld(n,'name')
                if q:
                    i=decl(f,fid,n,txt(q,b),'class'); bases=fld(n,'superclasses')
                    if bases:
                        for z in bases.named_children:pending_types.append((i,txt(z,b),f.path,'inherits',n))
                    for c in n.named_children:walk(c,i)
                    return
            if n.type in {'import_statement','import_from_statement'}:pending_imports.append((fid,txt(n,b),f.path,n.start_point[0]+1))
            rel(n,b,f,parent)
            for c in n.named_children:walk(c,parent)
        walk(root)

    def java(root,b,f,fid):
        def walk(n,parent=None):
            if n.type in {'class_declaration','interface_declaration','enum_declaration'}:
                q=fld(n,'name')
                if q:
                    typ='interface' if n.type=='interface_declaration' else 'class';i=decl(f,fid,n,txt(q,b),typ);d=txt(n,b)
                    m=re.search(r'\bextends\s+([A-Za-z_$][\w$]*)',d)
                    if m:pending_types.append((i,m.group(1),f.path,'inherits',n))
                    m=re.search(r'\bimplements\s+([^\{]+)',d)
                    if m:
                        for x in m.group(1).split(','):pending_types.append((i,x.strip().split()[0],f.path,'implements',n))
                    for c in n.named_children:walk(c,i)
                    return
            if n.type in {'method_declaration','constructor_declaration'}:
                q=fld(n,'name')
                if q:
                    i=decl(f,fid,n,txt(q,b),'method',parent);rel(n,b,f,i)
                    for c in n.named_children:walk(c,i)
                    return
            if n.type in {'import_declaration','package_declaration'}:pending_imports.append((fid,txt(n,b),f.path,n.start_point[0]+1))
            rel(n,b,f,parent)
            for c in n.named_children:walk(c,parent)
        walk(root)

    def js(root,b,f,fid):
        def walk(n,parent=None):
            if n.type in {'function_declaration','generator_function_declaration'}:
                q=fld(n,'name')
                if q:
                    i=decl(f,fid,n,txt(q,b),'function',parent);rel(n,b,f,i)
                    for c in n.named_children:walk(c,i)
                    return
            if n.type=='class_declaration':
                q=fld(n,'name')
                if q:
                    i=decl(f,fid,n,txt(q,b),'class');d=txt(n,b);m=re.search(r'\bextends\s+([A-Za-z_$][\w$]*)',d)
                    if m:pending_types.append((i,m.group(1),f.path,'inherits',n))
                    for c in n.named_children:walk(c,i)
                    return
            if n.type=='method_definition':
                q=fld(n,'name')
                if q:
                    i=decl(f,fid,n,txt(q,b),'method',parent);rel(n,b,f,i)
                    for c in n.named_children:walk(c,i)
                    return
            if n.type=='variable_declarator':
                q=fld(n,'name');v=fld(n,'value')
                if q and v and v.type in {'arrow_function','function_expression'}:
                    i=decl(f,fid,n,txt(q,b),'function',parent);rel(v,b,f,i)
            if n.type in {'import_statement','import_clause'}:pending_imports.append((fid,txt(n,b),f.path,n.start_point[0]+1))
            rel(n,b,f,parent)
            for c in n.named_children:walk(c,parent)
        walk(root)

    print('[4] Fetching blobs + Tree-sitter parsing')
    failures=[]
    for i,f in enumerate(files,1):
        print(f'  [{i}/{len(files)}] {f.lang:12} {f.path}')
        try:
            b=gh.blob(owner,repo,f.sha);root=parser(f.lang).parse(b).root_node
            if f.lang=='python':py(root,b,f,file_nodes[f.path])
            elif f.lang=='java':java(root,b,f,file_nodes[f.path])
            else:js(root,b,f,file_nodes[f.path])
        except Exception as e:failures.append((f.path,str(e)));print('     FAILED:',e)

    def resolve(name,file):
        c=byname.get(name.split('.')[-1],[]);same=[x for x in c if x.file==file]
        return same[0] if len(same)==1 else (c[0] if len(c)==1 else None)
    def resolve_import(src,raw,file,line):
        m=re.search(r'\bimport\s+(?:static\s+)?([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*;',raw)
        if m:
            x=resolve(m.group(1).split('.')[-1],file)
            if x:edge(src,x.id,'imports',line);return
        m=re.search(r'\bfrom\s+[A-Za-z_][\w.]*\s+import\s+(.+)',raw)
        if m:
            x=resolve(m.group(1).split(',')[0].strip(),file)
            if x:edge(src,x.id,'imports',line);return
        m=re.search(r'\bimport\s+([A-Za-z_][\w.]*)',raw)
        if m:
            mod=m.group(1).replace('.','/')
            for p,fid in file_nodes.items():
                if p.endswith(mod+'.py'):edge(src,fid,'imports',line);return
        m=re.search(r"(?:from\\s+|import\\s*)['\"](.+?)['\"]",raw)
        if m and m.group(1).startswith('.'):
            base=os.path.normpath(os.path.join(os.path.dirname(file),m.group(1))).replace('\\','/')
            for p in [base,base+'.ts',base+'.tsx',base+'.js',base+'.jsx',base+'/index.ts',base+'/index.tsx',base+'/index.js',base+'/index.jsx']:
                if p in file_nodes:edge(src,file_nodes[p],'imports',line);return

    print('[5] Resolving calls/imports/inheritance')
    for x in pending_imports:resolve_import(*x)
    for src,name,file,line in pending_calls:
        x=resolve(name,file)
        if x:edge(src,x.id,'calls',line)
    for src,name,file,line in pending_new:
        x=resolve(name,file)
        if x:edge(src,x.id,'instantiates',line)
    for src,name,file,typ,n in pending_types:
        x=resolve(name,file)
        if x:edge(src,x.id,typ,n.start_point[0]+1)
    for e in list(edges):
        if e.typ not in {'imports','calls','instantiates','inherits','implements'}:continue
        sf=nodes[e.src].file if e.src in nodes else None;tf=nodes[e.dst].file if e.dst in nodes else None
        if sf and tf and sf!=tf:edge(file_nodes[sf],file_nodes[tf],'depends')

    adj=defaultdict(lambda:defaultdict(list))
    for nid in nodes:adj[nid]
    for e in edges:adj[e.src][e.typ].append(e.dst)
    graph={s:{t:sorted(set(ds)) for t,ds in sorted(v.items())} for s,v in sorted(adj.items())}
    print('\n[6] MASTER GRAPH — ADJACENCY LIST');print('='*80);print(json.dumps(graph,indent=2,ensure_ascii=False))
    print('\nNode counts:',dict(Counter(n.typ for n in nodes.values())));print('Edge counts:',dict(Counter(e.typ for e in edges)));print('Parse failures:',len(failures))
    if a.json_path:
        with open(a.json_path,'w',encoding='utf-8') as fp:json.dump(graph,fp,indent=2,ensure_ascii=False)
        print('Saved:',a.json_path)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('ERROR:',e,file=sys.stderr);sys.exit(1)
