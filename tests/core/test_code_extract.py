from hx.code_extract import extract
import pytest


@pytest.mark.parametrize(
    "path,text,qualified,signature,attribute",
    [
        (
            "a.py",
            "class Store:\n    @cached\n    def get(\n        self, name: str\n    ) -> str:\n        return name\n",
            "Store.get",
            "def get( self, name: str ) -> str",
            "cached",
        ),
        (
            "a.cs",
            "namespace Acme;\n[TestFixture]\npublic class Store {\n [Test]\n public string Get(\n string name\n ) { return name; }\n}\n",
            "Acme.Store.Get",
            "public string Get( string name )",
            "Test",
        ),
        (
            "a.ts",
            "export class Store {\n @cached\n get(\n name: string\n ): string { return name; }\n}\n",
            "Store.get",
            "get( name: string ): string",
            "cached",
        ),
        (
            "a.go",
            "package main\ntype Store struct { Name string }\nfunc (s *Store) Get(\n name string,\n) string { return name }\n",
            "Store.Get",
            "func (s *Store) Get( name string, ) string",
            None,
        ),
        (
            "a.rs",
            "pub struct Store {}\nimpl Store {\n #[test]\n pub fn get(\n &self, name: &str\n ) -> String { name.into() }\n}\n",
            "Store.get",
            "pub fn get( &self, name: &str ) -> String",
            "test",
        ),
    ],
)
def test_structural_declarations(path, text, qualified, signature, attribute):
    symbols, _, _ = extract(text, path)
    found = [s for s in symbols if s["qualified"] == qualified]
    assert len(found) == 1, symbols
    assert found[0]["signature"] == signature
    assert found[0]["end_line"] >= found[0]["line"]
    assert len(found[0]["anchor_sha"]) == 64
    if attribute:
        assert attribute in found[0]["attributes"]


def test_attributes_only_on_adjacent_declaration_and_inline_method():
    text = "[TestFixture]\nclass Tests { [Test] public void Works() { Foo.Run(); }\n public void Helper() { Foo.Run(); }\n}\nclass Production { public void Run() {} }\n"
    rows, refs, _ = extract(text, "a.cs")
    by_name = {r["qualified"]: r for r in rows}
    assert by_name["Tests.Works"]["attributes"] == ["Test"]
    assert by_name["Tests.Helper"]["attributes"] == []
    assert by_name["Production"]["attributes"] == []
    assert any(r["name"] == "Run" for r in refs)


def test_dotted_namespace_rust_trait_impl_and_expression_span():
    rows, _, _ = extract(
        "namespace Acme.Deep;\nclass Foo { int Get() =>\n 42;\n}\n", "a.cs"
    )
    row = next(r for r in rows if r["name"] == "Get")
    assert row["qualified"] == "Acme.Deep.Foo.Get" and row["end_line"] == 3
    assert row["signature"] == "int Get()"
    rows, _, _ = extract(
        "trait Read { fn get(&self); }\nimpl Read for Store { fn get(&self) {} }",
        "a.rs",
    )
    assert any(r["qualified"] == "Store.get" for r in rows)


def test_rust_lifetimes_do_not_mask_declarations():
    text = "impl Store {\n pub fn get<'a>(&'a self, name: &'a str) -> &'a str { name }\n}\n"
    rows, _, _ = extract(text, "a.rs")
    method = next(r for r in rows if r["name"] == "get")
    assert method["qualified"] == "Store.get" and method["end_line"] == 2
    assert "&'a self" in method["signature"]


def test_python_unicode_offsets_and_csharp_properties():
    rows, _, _ = extract("class Café:\n def get(self):\n  return 'café'\n", "a.py")
    method = next(r for r in rows if r["name"] == "get")
    assert method["qualified"] == "Café.get" and method["end_line"] == 3
    rows, _, _ = extract(
        'interface IFoo { string Name { get; } }\nclass Foo : IFoo { public string Name =>\n "foo";\n}',
        "a.cs",
    )
    properties = [r for r in rows if r["kind"] == "property"]
    assert len(properties) == 2 and properties[1]["end_line"] == 3


def test_go_interface_declarations_have_exact_headers():
    rows, _, _ = extract(
        "package io\ntype Reader interface {\n Read(p []byte) (n int, err error)\n Close() error\n}\n",
        "a.go",
    )
    assert next(r for r in rows if r["name"] == "Reader")["kind"] == "interface"
    assert (
        next(r for r in rows if r["qualified"] == "Reader.Read")["signature"]
        == "Read(p []byte) (n int, err error)"
    )
    assert (
        next(r for r in rows if r["qualified"] == "Reader.Close")["signature"]
        == "Close() error"
    )


def test_attributes_are_part_of_non_python_anchor_span():
    from hx.code_store import span_sha

    text = "class Tests {\n [Fact]\n public void Runs() {}\n}\n"
    rows, _, _ = extract(text, "a.cs")
    row = next(r for r in rows if r["name"] == "Runs")
    assert row["line"] == 2
    assert row["anchor_sha"] == span_sha(text, 2, 3)
    assert row["signature"] == "public void Runs()"


@pytest.mark.parametrize(
    "declaration,kind",
    [
        ("record Customer(string Name);", "class"),
        ("record struct Point(int X, int Y);", "struct"),
    ],
)
def test_csharp_positional_records(declaration, kind):
    rows, _, _ = extract("namespace Acme;\npublic " + declaration + "\n", "record.cs")
    record = next(r for r in rows if r["kind"] == kind)
    assert record["qualified"] == "Acme." + ("Customer" if kind == "class" else "Point")
    assert record["signature"] == "public " + declaration.rstrip(";")
