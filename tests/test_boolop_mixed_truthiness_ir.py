from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def test_mixed_string_boolop_condition_lowers_to_integer_result_slot() -> None:
    source = """
from asmpython import Public, access

@access(Public)
def check(text: str) -> int:
    while text and text.isalnum():
        return 1
    return 0
"""
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    lowered = ir_lower.lower_module(module)
    function = next(function for function in lowered.funcs if function.name == "check")
    assert any("truthyboolrhs" in block.label for block in function.blocks)
