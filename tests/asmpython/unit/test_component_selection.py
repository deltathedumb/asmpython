"""What `-o thing.wasm` means without anyone naming a backend.

THE RULE THIS FILE PINS. A spelling chooses; a flag overrules. Two components
claiming one spelling is an ERROR naming both, never a pick -- a warning
followed by a build of the wrong thing is worse than stopping, because the
user reads the warning after the artifact already exists.

WHY THE AMBIGUOUS CASE IS TESTED WITH REAL COMPONENTS rather than a stub: `c`
and `cpyext` both write `.c` today, and that is not a hypothetical to be
mocked up -- it is the case the driver actually meets. A stub would keep
passing after the real collision was resolved or a third claimant appeared.
"""
from __future__ import annotations

from pathlib import Path

from tests import harness

from asmpython import backend as backend_registry
from asmpython import frontend as frontend_registry
from asmpython import link as link_registry
from asmpython.backend.families import SelectionError
from asmpython.driver.select import (
    choose_backend, choose_frontend, choose_linker,
)

backend_registry.load_builtin()
link_registry.load_builtin()
frontend_registry.load_builtin()


class TestTheSourceChoosesTheFrontend:
    def test_a_python_source_needs_no_flag(self):
        assert choose_frontend(Path("t.py"), None, frontend_registry) == "python"

    def test_a_named_frontend_overrules_the_spelling(self):
        assert choose_frontend(Path("t.py"), "apir", frontend_registry) == "apir"

    def test_an_unreadable_extension_says_what_is_readable(self):
        try:
            choose_frontend(Path("t.zzz"), None, frontend_registry)
        except SelectionError as exc:
            assert ".py" in str(exc) and "-fr/--frontend" in str(exc)
        else:
            raise AssertionError("expected a refusal")


class TestTheOutputChoosesTheLinker:
    def test_an_extension_module_is_the_cpyext_linker(self):
        got = choose_linker(Path("t.so"), None, link_registry, fallback="none")
        assert got == "cpyext"

    def test_a_jar_is_the_jar_linker(self):
        got = choose_linker(Path("t.jar"), None, link_registry, fallback="none")
        assert got == "jar"

    def test_an_unclaimed_extension_falls_back(self):
        got = choose_linker(Path("t.wasm"), None, link_registry,
                            fallback="none")
        assert got == "none"

    def test_no_output_at_all_falls_back(self):
        assert choose_linker(None, None, link_registry, fallback="cc") == "cc"

    def test_a_named_linker_overrules_the_spelling(self):
        got = choose_linker(Path("t.so"), "none", link_registry,
                            fallback="cc")
        assert got == "none"


class TestTheLinkerChoosesTheBackend:
    def test_cpyext_reaches_its_own_backend_not_c(self):
        # BOTH WRITE `.c`, so the extension cannot tell them apart and the
        # linker's own declaration is the only thing that can.
        got = choose_backend(Path("t.so"), None, "cpyext",
                             backend_registry, link_registry)
        assert got == "cpyext"

    def test_a_jar_reaches_the_jvm_backend(self):
        got = choose_backend(Path("t.jar"), None, "jar",
                             backend_registry, link_registry)
        assert got == "jvm"

    def test_cc_prefers_the_first_backend_it_declares(self):
        # A PREFERENCE IS NOT AN AMBIGUITY -- `-o thing` names no extension
        # at all, and `cc` listing `c` first is a declaration, not a tie.
        got = choose_backend(Path("thing"), None, "cc",
                             backend_registry, link_registry)
        assert got == "c"

    def test_an_unlinked_output_is_read_as_the_backends_own_artifact(self):
        got = choose_backend(Path("t.wasm"), None, "none",
                             backend_registry, link_registry)
        assert got == "wasm"

    def test_a_named_backend_overrules_everything(self):
        got = choose_backend(Path("t.so"), "llvm", "cpyext",
                             backend_registry, link_registry)
        assert got == "llvm"


class TestAmbiguityStopsAndNamesTheCandidates:
    def test_dot_c_does_not_say_whether_c_or_cpyext(self):
        try:
            choose_backend(Path("out.c"), None, "none",
                           backend_registry, link_registry)
        except SelectionError as exc:
            said = str(exc)
            assert "c" in said and "cpyext" in said, said
            assert "-bk/--backend" in said, said
        else:
            raise AssertionError(
                "'.c' is claimed by both c and cpyext; selection must refuse")

    def test_a_linker_taking_anything_with_no_spelling_refuses(self):
        try:
            choose_backend(None, None, "none",
                           backend_registry, link_registry)
        except SelectionError as exc:
            assert "-bk/--backend" in str(exc)
        else:
            raise AssertionError("expected a refusal")


class TestTheDeclarationsNameRealBackends:
    def test_every_linker_names_backends_that_exist(self):
        known = set(backend_registry.available())
        wrong = {name: [b for b in tc.backends if b not in known]
                 for name, tc in link_registry.available().items()
                 if [b for b in tc.backends if b not in known]}
        assert wrong == {}, f"linkers naming unregistered backends: {wrong}"
