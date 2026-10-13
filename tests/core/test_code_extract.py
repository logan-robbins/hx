from hx.code_extract import extract
import pytest

@pytest.mark.parametrize('path,text,qualified,signature,attribute',[
 ('a.py','class Store:\n    @cached\n    def get(\n        self, name: str\n    ) -> str:\n        return name\n','Store.get','def get( self, name: str ) -> str','cached'),
 ('a.cs','namespace Acme;\n[TestFixture]\npublic class Store {\n public string Get(\n string name\n ) { return name; }\n}\n','Acme.Store.Get','public string Get( string name )','TestFixture'),
 ('a.ts','export class Store {\n @cached\n get(\n name: string\n ): string { return name; }\n}\n','Store.get','get( name: string ): string','cached'),
 ('a.go','package main\ntype Store struct { Name string }\nfunc (s *Store) Get(\n name string,\n) string { return name }\n','Store.Get','func (s *Store) Get( name string, ) string',None),
 ('a.rs','pub struct Store {}\nimpl Store {\n #[test]\n pub fn get(\n &self, name: &str\n ) -> String { name.into() }\n}\n','Store.get','pub fn get( &self, name: &str ) -> String','test'),
])
def test_structural_declarations(path,text,qualified,signature,attribute):
 symbols,_,_=extract(text,path)
 found=[s for s in symbols if s['qualified']==qualified]
 assert len(found)==1, symbols
 assert found[0]['signature']==signature
 assert found[0]['end_line']>=found[0]['line']
 assert len(found[0]['anchor_sha'])==64
 if attribute: assert attribute in found[0]['attributes']
