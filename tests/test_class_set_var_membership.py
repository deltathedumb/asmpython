from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def test_class_set_variable_retains_set_type_for_membership() -> None:
    source = """
from asmpython import Public, access

class Parser:
    INFORMATIONAL = {"property", "staticmethod"}

    def accepts(self, name: str) -> int:
        return 1 if name in self.INFORMATIONAL else 0

@access(Public)
def check() -> int:
    return Parser().accepts("property")
"""
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    lowered = ir_lower.lower_module(module)
    assert any(function.name == "check" for function in lowered.funcs)
