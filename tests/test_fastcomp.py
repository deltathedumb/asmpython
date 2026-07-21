from asmpython._compiler.fastcomp import _make_fragment_sources


def test_fragment_split_exports_and_imports_symbols() -> None:
    lines = [
        "; generated",
        "BITS 64",
        "default rel",
        "extern puts",
        "global main",
        "section .text",
        "main:",
        "    call foo",
        "foo:",
        "    lea rax, [rel message]",
        "    ret",
        "section .data",
        "message:",
        "    db 0",
    ]
    fragments = _make_fragment_sources(lines, [("foo", 8, 11)])
    assert [fragment.name for fragment in fragments] == ["__base__", "foo"]
    assert "extern foo" in fragments[0].source
    assert "global message" in fragments[0].source
    assert "global foo" in fragments[1].source
    assert "extern message" in fragments[1].source
    assert "extern puts" in fragments[1].source
