from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def _lower(source: str):
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    return ir_lower.lower_module(module)


def test_ir_backend_does_not_emit_native_id_or_zip_calls() -> None:
    lowered = _lower(
        """
from asmpython import Public, access

@access(Public)
def check(left: list[int], right: list[int]) -> int:
    pairs = zip(left, right)
    return id(pairs)
"""
    )
    calls = {
        instruction.operands[0]
        for function in lowered.funcs
        for block in function.blocks
        for instruction in block.instrs
        if instruction.op == "call"
        and instruction.operands
        and isinstance(instruction.operands[0], str)
    }
    assert "id" not in calls
    assert "zip" not in calls
