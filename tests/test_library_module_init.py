from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def test_library_mode_emits_callable_module_initializer() -> None:
    module = Parser(
        Lexer("values: list[int] = [0]\n").tokenize(),
        frozenset(),
    ).parse()
    module.force_module_init = True
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    lowered = ir_lower.lower_module(module)
    assert any(
        function.name == "__asmpy_module_init"
        and function.visibility == "global"
        for function in lowered.funcs
    )
