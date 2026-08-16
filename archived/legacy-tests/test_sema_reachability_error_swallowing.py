from __future__ import annotations

import unittest

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze, SemaError


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), frozenset()).parse()


class SemaReachabilityErrorSwallowingTests(unittest.TestCase):
    def test_main_with_a_real_error_and_no_module_body_call_still_raises(self) -> None:
        # Regression: _analyze_with_unreachable_project_tolerance's own
        # reachability walker (_syntactic_reachable_names, in sema.py --
        # separate from ir_lower.py's _reachable_callables and
        # live_definition_compat_fixes.py's _live_definitions, both fixed
        # for the identical reason) didn't know "main" is a real root when
        # nothing in mod.body calls it by name (the common case: no
        # `if __name__ == "__main__": main()` guard). That made it set
        # is_stdlib=True on main purely to borrow sema's stdlib-tolerance
        # path -- which SILENTLY DISCARDS every real error in main's body,
        # not just relaxes checking. Any genuine bug in an entry-point
        # function (a typo'd undefined variable here) was accepted instead
        # of raising.
        module = _parse(
            "def main() -> int:\n"
            "    return this_name_does_not_exist\n"
        )
        with self.assertRaises(SemaError):
            sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_exported_function_with_a_real_error_still_raises(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def broken(x: int) -> int:\n"
            "    return this_name_does_not_exist\n"
        )
        for function in module.funcs:
            if function.name == "broken":
                function.is_public_export = True
        with self.assertRaises(SemaError):
            sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_genuinely_unreachable_function_error_still_tolerated(self) -> None:
        # The pass's real job (tolerating errors in merged-but-unreachable
        # source) must keep working -- this isn't a blanket "never
        # tolerate," just excluding main/exports from it.
        module = _parse(
            "def unused() -> int:\n"
            "    return this_name_does_not_exist\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())


if __name__ == "__main__":
    unittest.main()
