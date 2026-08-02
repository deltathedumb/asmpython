from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def test_ir_backend_lowers_set_iteration() -> None:
    source = (
        """
from asmpython import Public, access

@access(Public)
def collect() -> list[str]:
    result: list[str] = []
    values = {"alpha", "beta"}
    for value in values:
        result.append(value)
    return result
"""
    )
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    lowered = ir_lower.lower_module(module)
    assert any(function.name == "collect" for function in lowered.funcs)
