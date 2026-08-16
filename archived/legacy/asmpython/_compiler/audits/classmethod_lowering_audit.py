"""Inspect reachable classmethod metadata and its emitted IR."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..ssa import ir_lower as IR
from ..program import load_program
from ..sema import SemaAnalyzer


INTERESTING_SUFFIXES = (
    "__supports_realm",
    "__normalize",
    "__get_value",
)


def _interesting(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in INTERESTING_SUFFIXES)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path)
    args = parser.parse_args(argv)

    entry = args.entry.resolve()
    module = load_program(entry.read_text(encoding="utf-8"), entry)
    analyzer = SemaAnalyzer(module, source_dir=entry.parent, collect_errors=True)
    try:
        analyzer.analyze()
        print("ANALYZE PASS")
    except Exception as error:
        print("ANALYZE ERROR", type(error).__name__ + ":", str(error))

    print("REACHABLE_WRAPPER", IR._reachable_callables.__module__, IR._reachable_callables.__name__)
    print("LOWER_FUNC_WRAPPER", IR.lower_func.__module__, IR.lower_func.__name__)
    print("CTX_INIT_WRAPPER", IR._FuncCtx.__init__.__module__, IR._FuncCtx.__init__.__name__)
    print("LOWER_EXPR_WRAPPER", IR._lower_expr.__module__, IR._lower_expr.__name__)

    top_functions, method_functions = IR._reachable_callables(module)
    print("REACHABLE_METHODS")
    for function in method_functions:
        if not _interesting(function.name):
            continue
        print(
            "METHOD",
            function.name,
            "PARAMS",
            list(function.params),
            "PARAM_TYPES",
            list(function.param_types),
            "DECORATORS",
            list(function.decorators),
        )

    try:
        lowered = IR.lower_module(module)
    except Exception as error:
        print("LOWER ERROR", type(error).__name__ + ":", str(error))
        return 0

    print("LOWER PASS")
    for function in lowered.funcs:
        if not _interesting(function.name):
            continue
        print("IR_FUNCTION", function.name)
        for block in function.blocks:
            print("  BLOCK", block.label)
            for instruction in block.instrs:
                print(
                    "    IR",
                    instruction.op,
                    "DST",
                    getattr(instruction.dst, "name", None),
                    "ARGS",
                    [getattr(argument, "name", argument) for argument in instruction.args],
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
