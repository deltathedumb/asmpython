"""The JVM backend, without a JVM.

Everything here is about what gets WRITTEN -- the class-file header, which
methods exist, which constructs are refused, and how the two version options
reach the backend from the command line. Actually running the result needs a
JVM and lives in `tests/asmpython/integration/test_jvm.py`; these run
everywhere, including on a machine with no Java installed at all, which is the
same machine that can still build a jar.
"""
from __future__ import annotations

import struct

from tests import harness

from asmpython import backend as backend_registry
from asmpython import target as target_registry
from asmpython.backend.base import OptionError
from asmpython.backend import BackendUnsupported
from asmpython.backends.jvm import version as V
from asmpython.backends.jvm.classfile import ConstantPool, _modified_utf8
from asmpython.backends.jvm.emit import JvmBackend, _class_name, _normalized
from asmpython.diagnostics import DiagnosticSink, Severity
from asmpython.ir import types as T, verify
from asmpython.ir.printer import parse_module

JVM_TARGET = target_registry.get("jvm")

SIMPLE = """\
module prog

export func main() -> i64 {
entry:
    %0 = i64.const 7
    ret %0
}
"""


def build(text: str, **options) -> dict[str, bytes]:
    module = parse_module(text)
    verify(module)
    be = JvmBackend(V.resolve(**options))
    return be.emit(module, JVM_TARGET)


def major_of(class_bytes: bytes) -> int:
    return struct.unpack(">H", class_bytes[6:8])[0]


class TestTheFileItWrites:
    def test_it_is_a_class_file(self):
        artifacts = build(SIMPLE)
        data = artifacts["Prog.class"]
        assert data[:4] == b"\xca\xfe\xba\xbe"

    def test_the_class_is_named_after_the_module(self):
        assert "Prog.class" in build(SIMPLE)

    def test_a_manifest_comes_with_it_so_java_jar_works(self):
        manifest = build(SIMPLE)["META-INF/MANIFEST.MF"]
        assert b"Main-Class: Prog" in manifest

    @harness.cases("options,expected", [
        ({}, 52),
        ({"java_version": "21"}, 65),
        ({"java_version": "8"}, 52),
        ({"class_version": "75"}, 75),
        ({"class_version": "75", "java_version": "21"}, 75),
    ])
    def test_the_header_carries_the_resolved_version(self, options, expected):
        assert major_of(build(SIMPLE, **options)["Prog.class"]) == expected

    def test_a_module_with_no_main_gets_no_manifest(self):
        """`java -jar` on a jar with a Main-Class naming nothing is a worse
        error than one saying there is no main class."""
        artifacts = build(SIMPLE.replace("main", "helper"))
        assert "META-INF/MANIFEST.MF" not in artifacts

    def test_a_main_taking_arguments_gets_no_entry_point(self):
        """A JVM hands its entry a String[]; the IR wants integers. Inventing
        zeros would make a program that reads its arguments quietly wrong."""
        artifacts = build("""\
module prog

export func main(%0: i64) -> i64 {
entry:
    ret %0
}
""")
        assert "META-INF/MANIFEST.MF" not in artifacts

    def test_two_builds_of_one_module_are_identical(self):
        """Runtime helpers are discovered in visit order and emitted sorted, so
        that a diff of two builds shows what changed rather than reordering."""
        assert build(SIMPLE) == build(SIMPLE)

    @harness.cases("name,expected", [
        ("prog", "Prog"), ("my-prog", "My_prog"), ("2fast", "M2fast"),
        ("", "M"), ("a.b.c", "A_b_c"),
    ])
    def test_a_module_name_becomes_a_class_name(self, name, expected):
        assert _class_name(name) == expected


class TestWhatItRefuses:
    """Refused loudly at compile time, not at class-load time on someone
    else's machine."""

    def test_an_external_with_no_jvm_definition(self):
        with harness.raises(BackendUnsupported, match="rand"):
            build("""\
module prog

func rand() -> i64 external

export func main() -> i64 {
entry:
    %0 = i64.call @rand()
    ret %0
}
""")

    def test_an_external_nothing_calls_is_not_refused(self):
        """DECLARED IS NOT CALLED, and a class file has nothing to resolve for
        a symbol no instruction names -- so an uncalled declaration cannot fail
        at load, at link or at run, and refusing one rejects a program that
        would have worked.

        THIS IS HOW A THIRD PARTY BROKE THIS BACKEND FROM OUTSIDE THE TREE. An
        installed plugin patched `Lowerer.run` to declare its own runtime
        symbols, which declared them for EVERY program rather than only the
        ones using it -- and because `run` drops unused externals inside
        itself, what the wrapper appended afterwards survived the drop. Every
        JVM compile on that machine then failed with `nothing defines
        'luau_l_exec' on the JVM`, naming a symbol the user had never heard of
        in a program that never used it. 31 tests, none of them about the
        plugin.
        """
        assert build("""\
module prog

func luau_l_exec(%0: ptr) -> ptr external

export func main() -> i64 {
entry:
    %0 = i64.const 7
    ret %0
}
""")

    def test_an_external_whose_address_is_taken_is_still_refused(self):
        """A function POINTER reaches a callee no CALL names.

        The rule above is "declared is not called", and this is the other side
        of it: `func_addr` IS a use, and letting it past would pass a program
        that dies at run time -- the exact failure the check exists to prevent,
        arrived at from the direction nobody looks.
        """
        with harness.raises(BackendUnsupported, match="rand"):
            build("""\
module prog

func rand() -> i64 external

export func main() -> i64 {
entry:
    %0 = ptr.func_addr @rand
    %1 = i64.call_ptr %0
    ret %1
}
""")

    def test_the_python_runtime_is_named_as_the_reason(self):
        try:
            build("""\
module prog

func apy_list_new() -> ptr external

export func main() -> i64 {
entry:
    %0 = ptr.call @apy_list_new()
    %1 = i64.const 0
    ret %1
}
""")
        except BackendUnsupported as exc:
            assert "--backend c" in str(exc)
        else:
            harness.fail("expected BackendUnsupported")

    def test_a_function_pointer_of_a_shape_nothing_matches(self):
        """A dispatcher with no arms is still a dispatcher.

        `call_ptr` through a signature no function has cannot be a compile
        error -- the pointer is a run-time value -- so it compiles to a
        dispatcher whose every path throws."""
        artifacts = build("""\
module prog

func helper(%0: i64) -> i64 {
entry:
    ret %0
}

export func main() -> i64 {
entry:
    %0 = ptr.func_addr @helper
    %1 = f64.const 1.0
    %2 = f64.call_ptr %0(%1)
    %3 = i64.ftoi %2
    ret %3
}
""")
        assert "Prog.class" in artifacts

    def test_unsigned_64_bit_division_below_java_8(self):
        """The class-file version is not decoration: `Long.divideUnsigned`
        arrived in Java 8, so asking for an older one has to refuse."""
        source = """\
module prog

func put_int(%0: i64) -> void external

export func main() -> i64 {
entry:
    %0 = u64.const 18446744073709551615
    %1 = u64.const 3
    %2 = u64.div %0, %1
    %3 = i64.bitcast %2
    call @put_int(%3)
    %4 = i64.const 0
    ret %4
}
"""
        build(source, java_version="8")               # fine
        with harness.raises(BackendUnsupported, match="Java 8"):
            build(source, java_version="7")


class TestConstantPool:
    def test_a_long_occupies_two_entries(self):
        """The quirk that produces a class rejected with a message pointing at
        an unrelated constant."""
        pool = ConstantPool()
        first = pool.long(1)
        assert pool.utf8("x") == first + 2

    def test_constants_are_interned(self):
        pool = ConstantPool()
        assert pool.utf8("same") == pool.utf8("same")

    def test_zero_and_negative_zero_are_different_constants(self):
        pool = ConstantPool()
        assert pool.double(0.0) != pool.double(-0.0)

    @harness.cases("text", ["\0", "\xff", "hi", "é", "\U0001f600"])
    def test_modified_utf8_round_trips_through_the_jvm_rules(self, text):
        raw = _modified_utf8(text)
        assert b"\0" not in raw, "a NUL byte terminates the string early"
        # Java decodes surrogate pairs written separately; Python does not,
        # so the check is on the encoding rather than a round trip.
        assert raw == text.encode("utf-8", "surrogatepass") or "\0" in text \
            or ord(text[0]) > 0xFFFF


class TestConstantNormalisation:
    """How a value is HELD, which decides what a comparison and a print say."""

    #: Named by TYPE rather than by the type object: `_label` in the harness
    #: renders anything that is not a string or a number as its class name, so
    #: every `Type` here would produce the id "Type" and two cases would share
    #: one name.
    @harness.cases("ty,value,expected", [
        ("i1", 1, 1), ("i1", 0, 0),
        ("i8", -1, -1), ("i8", 255, -1), ("i8", 128, -128),
        ("u8", -1, 255), ("u8", 255, 255), ("u8", 256, 0),
        ("i32", -1, -1), ("u32", 0xFFFFFFFF, -1), ("u32", 1, 1),
        ("i64", -1, -1), ("u64", 2 ** 64 - 1, -1),
        ("ptr", 8, 8),
    ])
    def test_a_constant_is_normalised_to_its_type(self, ty, value, expected):
        assert _normalized(T.ALL[ty], value) == expected


class TestTheOptionsReachTheBackend:
    def test_the_backend_declares_its_flags(self):
        backend_registry.load_builtin()
        names = {o.name for o in backend_registry.get("jvm").options}
        assert names == {"java-version", "class-version", "classpath"}

    def test_configure_returns_a_new_backend(self):
        """The registry holds one shared object. A backend that stored flags on
        itself would leak them into the next compilation in the process."""
        backend_registry.load_builtin()
        original = backend_registry.get("jvm")
        configured = original.configure({"java-version": "21"},
                                        DiagnosticSink())
        assert configured is not original
        assert configured.class_version.major == 65
        assert original.class_version.major == V.DEFAULT

    def test_a_bad_value_is_an_option_error_not_a_traceback(self):
        backend_registry.load_builtin()
        with harness.raises(OptionError):
            backend_registry.get("jvm").configure(
                {"class-version": "banana"}, DiagnosticSink())

    def test_the_precedence_note_is_reported_as_a_warning(self):
        backend_registry.load_builtin()
        sink = DiagnosticSink()
        backend_registry.get("jvm").configure(
            {"class-version": "75", "java-version": "21"}, sink)
        assert sink.diagnostics
        assert sink.diagnostics[0].severity is Severity.WARNING
        assert "priority" in sink.diagnostics[0].message

    def test_a_backend_without_options_is_unaffected(self):
        backend_registry.load_builtin()
        c = backend_registry.get("c")
        assert c.options == ()
        assert c.configure({}, DiagnosticSink()) is c


class TestTheJvmTarget:
    def test_it_is_registered(self):
        assert target_registry.get("jvm").name == "jvm"
        assert target_registry.get("java").name == "jvm"

    def test_it_names_its_own_toolchain(self):
        """`cc` cannot link a class file, and the driver must not work that out
        from the target's name."""
        assert JVM_TARGET.default_toolchain == "jar"

    def test_it_produces_a_jar(self):
        assert JVM_TARGET.executable_suffix == ".jar"

    def test_every_other_target_still_defaults_to_the_c_driver(self):
        for name, t in target_registry.available().items():
            if name != "jvm":
                assert t.default_toolchain == "", name


class TestReadingAClassPath:
    """The reader half of the class-file format.

    `classfile.py` writes one and `classpath.py` reads one, and the pair is
    checked against itself: a class this compiler emitted, read back by this
    compiler, has to describe what was written. That is a stronger test than
    either half alone, and it is the one that catches a constant-pool tag whose
    width the writer and the reader disagree about.
    """

    def written_class(self) -> bytes:
        return build(SIMPLE)["Prog.class"]

    def test_it_reads_a_class_this_compiler_wrote(self):
        from asmpython.backends.jvm.classpath import read_class
        cls = read_class(self.written_class())
        assert cls.internal == "Prog"
        assert cls.superclass == "java/lang/Object"
        assert any(m.name == "main" for m in cls.methods)

    def test_a_method_descriptor_parses_into_its_parameters(self):
        from asmpython.backends.jvm.classpath import parse_parameters
        assert parse_parameters("(ILjava/lang/String;[IJ)V") == [
            "I", "Ljava/lang/String;", "[I", "J"]
        assert parse_parameters("()V") == []
        assert parse_parameters("([[Ljava/lang/Object;)I") == [
            "[[Ljava/lang/Object;"]

    def test_a_file_that_is_not_a_class_is_refused(self):
        from asmpython.backends.jvm.classpath import ClassFileError, read_class
        with harness.raises(ClassFileError):
            read_class(b"not a class file at all")

    def test_a_jar_of_unreadable_entries_does_not_fail_the_build(self, tmp_path):
        """A real jar holds things that are not classes this understands.
        Skipping one is right; failing the build over one is not."""
        import zipfile
        from asmpython.backends.jvm.classpath import ClassPath
        jar = tmp_path / "mixed.jar"
        with zipfile.ZipFile(jar, "w") as z:
            z.writestr("com/x/Broken.class", b"\xca\xfe\xba\xbe garbage")
            z.writestr("README", b"not a class")
            z.writestr("Prog.class", self.written_class())
        path = ClassPath()
        assert path.add(jar) == 1
        assert path.find("Prog") is not None


class TestJavaSymbols:
    """A symbol names an operation, and says enough to be read back.

    IR text outlives the process that wrote it: `--emit-ir` today, a build
    tomorrow. So a Java call has to be recoverable from its symbol and the
    class path alone -- which is what `lookup` does, and what makes the
    escaping scheme worth its ugliness.
    """

    def interop(self):
        from asmpython.backends.jvm.classpath import ClassPath
        from asmpython.backends.jvm.interop import Interop
        path = ClassPath()
        path._add_bytes(build(SIMPLE)["Prog.class"], "Prog.class")
        return Interop(path)

    @harness.cases("text", [
        "(I)V", "()Ljava/lang/String;", "([[Ljava/lang/Object;IJ)Z",
        "com/example/Outer$Inner", "a_b/c_d", "()V",
    ])
    def test_mangling_round_trips(self, text):
        from asmpython.backends.jvm.interop import mangle, unmangle
        wire = mangle(text)
        assert "$" not in wire, "the separator must never appear in a payload"
        assert unmangle(wire) == text

    def test_a_symbol_is_recovered_from_the_class_path_alone(self):
        io = self.interop()
        symbol = io.symbol("static", "Prog", "main", "()J")
        fresh = self.interop()                 # nothing looked up in this one
        op = fresh.lookup(symbol)
        assert op is not None
        assert (op.kind, op.owner, op.method, op.descriptor) == \
            ("static", "Prog", "main", "()J")

    def test_a_symbol_for_a_method_that_is_not_there_is_refused(self):
        """A symbol is text in a file. Emitting an invoke for a method the
        class path does not have produces a class that loads and then dies."""
        io = self.interop()
        assert io.lookup("jvm$static$Prog$nosuch$_p_rV") is None
        assert io.lookup("jvm$static$Nope$main$_p_rJ") is None

    def test_the_string_symbol_takes_an_address(self):
        io = self.interop()
        assert io.signature_of(io.string_symbol()) == (["ptr"], "i64")
