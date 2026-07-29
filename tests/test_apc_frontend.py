"""Low-level tests for the APC frontend.

These assert on the *emitted IR* -- op sequences, operand shapes, block
structure -- rather than on program output, so a lowering regression is
reported at the instruction that changed. The last two cases carry the module
through ``validate_ir`` and the real x86-64 backend, which is what catches
malformed IR that reads fine but cannot be encoded.
"""

from __future__ import annotations

import pathlib
import unittest

from asmpython._backends.x86_64 import __module_backend__ as x86_backend
from asmpython._compiler.ir import IRModule, Visibility
from asmpython._compiler.ir_verify import validate_ir
from asmpython._frontends import get_frontend
from asmpython._frontends.apc import APCError, emit_module, parse


def build(src: str) -> IRModule:
    return emit_module(parse(src), src)


def func(mod: IRModule, name: str):
    return next(f for f in mod.funcs if f.name == name)


def ops(fn, block: int | None = None) -> list[str]:
    blocks = fn.blocks if block is None else [fn.blocks[block]]
    return [i.op for b in blocks for i in b.instrs]


def find(fn, op: str):
    for b in fn.blocks:
        for i in b.instrs:
            if i.op == op:
                return i
    return None


class RegistrationTests(unittest.TestCase):
    def test_frontend_is_registered_under_its_name_and_aliases(self) -> None:
        impl = get_frontend("apc")
        self.assertIsNotNone(impl)
        self.assertEqual(impl.name, "apc")
        self.assertEqual(impl.source_extensions, (".apc",))
        self.assertIs(get_frontend("APC"), impl)
        self.assertIs(get_frontend("asmpython-c"), impl)


class LayoutTests(unittest.TestCase):
    SRC = """
    layout Header {
        magic: bytes[2]
        flags: bytes[1]
        len:   bytes[4] = 4
        crc:   bytes[4]
    }
    func read_len(h: Header) { ret h.len as i64 }: i64
    func read_magic(h: Header) { ret h.magic as i64 }: i64
    """

    def test_explicit_offset_moves_the_cursor_and_later_fields_follow(self) -> None:
        mod = build(self.SRC)
        # `len` was placed at 4 explicitly (the format pads byte 3), so `crc`
        # follows at 8 rather than at the packed position 7.
        self.assertEqual(find(func(mod, "read_len"), "gep").operands[1], 4)

    def test_auto_fill_packs_without_alignment_padding(self) -> None:
        mod = build(self.SRC)
        # `flags` packs directly after the 2-byte `magic`; no padding invented.
        self.assertEqual(find(func(mod, "read_magic"), "gep").operands[1], 0)

    def test_field_width_drives_the_load_type(self) -> None:
        mod = build(self.SRC)
        load = find(func(mod, "read_len"), "load")
        self.assertEqual(load.result.type.name, "u32")   # bytes[4] -> u32

    def test_field_access_without_a_layout_is_rejected(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build("func f(p: ptr) { ret p.len as i64 }: i64")
        self.assertIn("needs a layout", str(ctx.exception))


class EnumTests(unittest.TestCase):
    def test_symbolic_members_auto_increment(self) -> None:
        mod = build("""
        enum Status { OK, NA, ERR }
        func f() { ret Status::ERR }: i64
        """)
        self.assertEqual(find(func(mod, "f"), "const").operands[0], 2)

    def test_pinned_values_are_used_verbatim(self) -> None:
        mod = build("""
        enum Wire[u8] { Lo = 0, Hi = 7 }
        func f() { ret Wire::Hi }: i64
        """)
        self.assertEqual(find(func(mod, "f"), "const").operands[0], 7)

    def test_unknown_member_is_reported_with_a_position(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build("enum S { OK }\nfunc f() { ret S::NOPE }: i64")
        self.assertIn("no member", str(ctx.exception))
        self.assertEqual(ctx.exception.line, 2)


class SlotPromotionTests(unittest.TestCase):
    """Assign-once locals are emitted as SSA values, not memory slots.

    That matters because `mem2reg` -- the pass that would otherwise recover
    them -- is deliberately outside the o1/o2 presets pending a register
    allocator liveness fix, so anything left in a slot stays in memory.
    """

    def test_assign_once_local_needs_no_alloca(self) -> None:
        mod = build("func f(a: i64) { const b = a + 1\n ret b }: i64")
        self.assertNotIn("alloca", ops(func(mod, "f")))

    def test_reassigned_local_gets_a_slot(self) -> None:
        mod = build("func f(a: i64) { let b = a\n b = b + 1\n ret b }: i64")
        self.assertIn("alloca", ops(func(mod, "f")))

    def test_address_taken_local_gets_a_slot(self) -> None:
        mod = build("func f() { const b = 1\n ret &b as i64 }: i64")
        self.assertIn("alloca", ops(func(mod, "f")))

    def test_assigning_an_ssa_binding_is_an_error(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build("func f() { const b = 1\n b = 2\n ret b }: i64")
        self.assertIn("immutable", str(ctx.exception))


class SignednessTests(unittest.TestCase):
    def test_division_picks_udiv_for_unsigned_operands(self) -> None:
        mod = build("func f(a: u32, b: u32) { ret a / b }: u32")
        self.assertIn("udiv", ops(func(mod, "f")))

    def test_division_picks_idiv_for_signed_operands(self) -> None:
        mod = build("func f(a: i32, b: i32) { ret a / b }: i32")
        self.assertIn("idiv", ops(func(mod, "f")))

    def test_right_shift_picks_shr_for_unsigned_and_sar_for_signed(self) -> None:
        self.assertIn("shr", ops(func(build("func f(a: u32) { ret a >> 1 }: u32"), "f")))
        self.assertIn("sar", ops(func(build("func f(a: i32) { ret a >> 1 }: i32"), "f")))

    def test_comparison_picks_unsigned_predicate_for_unsigned_operands(self) -> None:
        mod = build("func f(a: u32, b: u32) { ret a < b }: i64")
        self.assertIn("icmp.ult", ops(func(mod, "f")))

    def test_widening_uses_zext_for_unsigned_and_sext_for_signed(self) -> None:
        self.assertIn("zext", ops(func(build("func f(a: u8) { ret a as i64 }: i64"), "f")))
        self.assertIn("sext", ops(func(build("func f(a: i8) { ret a as i64 }: i64"), "f")))

    def test_narrowing_uses_trunc(self) -> None:
        mod = build("func f(a: i64) { ret a as i32 }: i32")
        self.assertIn("trunc", ops(func(mod, "f")))


class ControlFlowTests(unittest.TestCase):
    def test_every_block_ends_in_a_terminator(self) -> None:
        mod = build("""
        func f(n: i64) {
            let t = 0
            for (i = 0..n) {
                if (i > 2) { t = t + i } else { t = t - 1 }
            }
            while (t < 0) { t = t + 1 }
            ret t
        }: i64
        """)
        for block in func(mod, "f").blocks:
            self.assertTrue(block.instrs, f"{block.label} is empty")
            self.assertIn(block.instrs[-1].op, ("br", "br.t", "ret"), block.label)

    def test_branch_targets_all_name_real_blocks(self) -> None:
        mod = build("func f(n: i64) { let t = 0\n if (n) { t = 1 }\n ret t }: i64")
        fn = func(mod, "f")
        labels = {b.label for b in fn.blocks}
        for block in fn.blocks:
            last = block.instrs[-1]
            if last.op == "br":
                self.assertIn(last.operands[0], labels)
            elif last.op == "br.t":
                self.assertIn(last.operands[1], labels)
                self.assertIn(last.operands[2], labels)

    def test_break_outside_a_loop_is_rejected(self) -> None:
        with self.assertRaises(APCError):
            build("func f() { break\n ret 0 }: i64")


class ModuleShapeTests(unittest.TestCase):
    def test_string_literal_becomes_a_global_and_global_addr(self) -> None:
        mod = build('extern func puts(s: ptr): i32\nfunc f() { puts("hi") \nret 0 }: i64')
        self.assertEqual([g.value for g in mod.data], ["hi"])
        self.assertIn("global_addr", ops(func(mod, "f")))

    def test_extern_declares_a_signature_without_emitting_a_body(self) -> None:
        mod = build("extern func putchar(c: i32): i32\nfunc f() { putchar(65)\n ret 0 }: i64")
        self.assertEqual([f.name for f in mod.funcs], ["f"])
        self.assertEqual(find(func(mod, "f"), "call").operands[0], "putchar")

    def test_export_publishes_the_symbol_and_marks_it_public(self) -> None:
        mod = build("func kernel() { ret 1 }: i64\nexport kernel")
        self.assertEqual(mod.exports, ["kernel"])
        self.assertEqual(func(mod, "kernel").visibility, Visibility.PUBLIC)

class TypeDeclTests(unittest.TestCase):
    SRC = """
    type RGB {
        func constructor(red: i32, green: i32, blue: i32) {
            pub const Parent.Red:   i32 = red
            pub const Parent.Green: i32 = green
            pub const Parent.Blue:  i32 = blue
        }: none

        func hash() {
            ret Parent.Red + Parent.Green + Parent.Blue
        }: i32

        func plain add(x: i32, y: i32) { ret x + y }: i32
    }
    """

    def test_methods_are_mangled_onto_the_type(self) -> None:
        mod = build(self.SRC)
        names = {f.name for f in mod.funcs}
        self.assertEqual(names, {"RGB__constructor", "RGB__hash", "RGB__add"})

    def test_instance_method_takes_the_receiver_as_parameter_zero(self) -> None:
        mod = build(self.SRC)
        params = func(mod, "RGB__hash").params
        self.assertEqual(params[0].name, "Parent")
        self.assertEqual(params[0].type.name, "ptr")

    def test_plain_method_has_no_receiver(self) -> None:
        mod = build(self.SRC)
        self.assertEqual([p.name for p in func(mod, "RGB__add").params], ["x", "y"])

    def test_constructor_assignments_become_the_layout(self) -> None:
        mod = build(self.SRC)
        # Three i32 fields pack at 0/4/8 with natural alignment.
        offsets = [i.operands[1] for i in func(mod, "RGB__constructor").blocks[0].instrs
                   if i.op == "gep"]
        self.assertEqual(offsets, [0, 4, 8])

    def test_field_read_uses_the_declared_type_not_a_raw_width(self) -> None:
        mod = build(self.SRC)
        load = find(func(mod, "RGB__hash"), "load")
        self.assertEqual(load.result.type.name, "i32")   # declared i32, signed

    def test_field_declared_inside_control_flow_is_rejected(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build("""
            type Bad {
                func constructor(x: i64) {
                    if (x) { pub const Parent.Maybe: i64 = 1 }
                }: none
            }
            """)
        self.assertIn("must not depend on a runtime value", str(ctx.exception))

    def test_instance_method_call_passes_the_receiver(self) -> None:
        mod = build(self.SRC + """
        func use() {
            const c = RGB(1, 2, 3)
            ret c::hash() as i64
        }: i64
        """)
        call = [i for i in func(mod, "use").blocks[0].instrs
                if i.op == "call" and i.operands[0] == "RGB__hash"]
        self.assertEqual(len(call), 1)
        self.assertEqual(len(call[0].operands), 2)      # symbol + receiver

    def test_plain_method_is_callable_on_the_type(self) -> None:
        mod = build(self.SRC + "func use() { ret RGB::add(1, 2) as i64 }: i64")
        self.assertIsNotNone(
            next((i for i in func(mod, "use").blocks[0].instrs
                  if i.op == "call" and i.operands[0] == "RGB__add"), None))

    def test_calling_an_instance_method_on_the_type_is_rejected(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build(self.SRC + "func use() { ret RGB::hash() as i64 }: i64")
        self.assertIn("instance method", str(ctx.exception))


class StdFramebufTests(unittest.TestCase):
    SRC = """
    import std::framebuf

    func main() {
        const frame = framebuf::new(4, 4)
        frame::clear(framebuf::rgb(10, 20, 30))
        frame::putPixel(1, 1, framebuf::rgb(255, 0, 0))
        const c = frame::getPixel(1, 1)
        framebuf::destroy(frame)
        ret framebuf::red(c)
    }: i64
    """

    def test_import_pulls_the_module_in_with_namespaced_symbols(self) -> None:
        mod = build(self.SRC)
        names = {f.name for f in mod.funcs}
        self.assertIn("framebuf__new", names)          # module function
        self.assertIn("FrameBuffer__putPixel", names)  # method of its type

    def test_module_is_emitted_once(self) -> None:
        """A namespaced type is registered under two keys mapping to one
        object; emitting per key would duplicate every method."""
        names = [f.name for f in build(self.SRC).funcs]
        self.assertEqual(len(names), len(set(names)))

    def test_type_resolves_qualified_and_plain(self) -> None:
        mod = build("""
        import std::framebuf
        func a(f: framebuf::FrameBuffer) { ret f::width() }: i64
        func b(f: FrameBuffer) { ret f::height() }: i64
        """)
        self.assertEqual(len(func(mod, "a").params), 1)
        self.assertEqual(len(func(mod, "b").params), 1)

    def test_unknown_module_is_reported(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build("import nope::thing")
        self.assertIn("only 'std::*'", str(ctx.exception))

    def test_framebuf_module_compiles_and_verifies(self) -> None:
        mod = build(self.SRC)
        validate_ir(mod)
        out = x86_backend.compile(mod, {"target_os": "windows", "abi": "win64"})
        self.assertTrue(next(iter(out.values())))


class StdInputTests(unittest.TestCase):
    SRC = """
    import std::input

    func main() {
        let ev: Event
        if (input::poll(Device::Keyboard, ev)) {
            ret ev.code as i64
        }
        ret 0
    }: i64
    """

    def test_poll_is_one_entry_point_over_every_device(self) -> None:
        mod = build(self.SRC)
        names = {f.name for f in mod.funcs}
        self.assertIn("input__poll", names)
        for backend in ("Keyboard", "Mouse", "Controller"):
            self.assertIn(f"input__poll{backend}", names)

    def test_layout_local_evaluates_to_its_address_not_a_load(self) -> None:
        """`let ev: Event` names storage. Loading it would read the first
        field and then treat that value as a pointer."""
        mod = build(self.SRC)
        entry = func(mod, "main").blocks[0]
        alloca = next(i for i in entry.instrs if i.op == "alloca")
        call = next(i for i in entry.instrs
                    if i.op == "call" and i.operands[0] == "input__poll")
        self.assertIs(call.operands[2], alloca.result)

    def test_event_layout_packs_six_four_byte_fields(self) -> None:
        mod = build("""
        import std::input
        func f(e: Event) { ret e.y as i64 }: i64
        """)
        self.assertEqual(find(func(mod, "f"), "gep").operands[1], 20)

    def test_intra_module_call_resolves_to_the_module_symbol(self) -> None:
        """`clear(ev)` inside std::input is `input::clear`, emitting as
        `input__clear` -- a bare `clear` would be an undefined symbol."""
        mod = build(self.SRC)
        targets = {i.operands[0] for f in mod.funcs for b in f.blocks
                   for i in b.instrs if i.op == "call"}
        self.assertIn("input__clear", targets)
        self.assertNotIn("clear", targets)

    def test_input_module_compiles_and_verifies(self) -> None:
        mod = build(self.SRC)
        validate_ir(mod)
        out = x86_backend.compile(mod, {"target_os": "windows", "abi": "win64"})
        self.assertTrue(next(iter(out.values())))


class NativeLibraryDeclarationTests(unittest.TestCase):
    """A std module declares the OS libraries it calls into.

    The backends' builtin symbol tables must stay frontend-agnostic -- a
    linker has no business knowing `std::framebuf` exists -- so each module
    ships a sibling `.libs` file that goes into the same registry the driver
    already consults for `--link-library`.
    """

    def setUp(self) -> None:
        from asmpython._compiler import native_libraries as native

        self._saved = native.active_registry()
        native.set_active_registry(native.NativeLibraryRegistry())

    def tearDown(self) -> None:
        from asmpython._compiler import native_libraries as native

        native.set_active_registry(self._saved)

    def _symbol_map(self, src: str) -> dict:
        from asmpython._compiler import native_libraries as native

        build(src)
        return native.active_registry().symbol_map("windows")

    def test_importing_framebuf_declares_its_windowing_libraries(self) -> None:
        symbols = self._symbol_map("import std::framebuf")
        self.assertEqual(symbols.get("CreateWindowExA"), "user32.dll")
        self.assertEqual(symbols.get("StretchDIBits"), "gdi32.dll")
        self.assertEqual(symbols.get("GetModuleHandleA"), "kernel32.dll")
        self.assertEqual(symbols.get("fwrite"), "msvcrt.dll")

    def test_importing_input_declares_its_console_symbols(self) -> None:
        symbols = self._symbol_map("import std::input")
        self.assertEqual(symbols.get("_kbhit"), "msvcrt.dll")
        self.assertEqual(symbols.get("_getch"), "msvcrt.dll")

    def test_backend_symbol_tables_stay_frontend_agnostic(self) -> None:
        from asmpython._backends.x86_64 import pe_linker

        for symbol in ("CreateWindowExA", "StretchDIBits", "_kbhit"):
            self.assertNotIn(symbol, pe_linker._DLL_FOR_SYMBOL, symbol)


class WindowApiTests(unittest.TestCase):
    SRC = """
    import std::framebuf

    func main() {
        const frame = framebuf::new(64, 48)
        frame::open("demo")
        frame::setTitle("changed")
        frame::setBorder(0)
        while (frame::pump()) {
            frame::present()
        }
        frame::closeWindow()
        framebuf::destroy(frame)
        ret 0
    }: i64
    """

    def test_window_methods_are_emitted(self) -> None:
        names = {f.name for f in build(self.SRC).funcs}
        for method in ("open", "present", "pump", "setTitle", "setBorder",
                       "bordered", "setPosition", "isOpen", "closeWindow"):
            self.assertIn(f"FrameBuffer__{method}", names)

    def test_window_procedure_is_a_function_address_not_a_call(self) -> None:
        """`wc.lpfnWndProc = DefWindowProcA` takes the symbol's address; the
        import thunk is a callable address, so an OS callback slot can be
        filled without a function-pointer type in the language."""
        mod = build(self.SRC)
        opener = func(mod, "FrameBuffer__open")
        addrs = {i.operands[0] for b in opener.blocks for i in b.instrs
                 if i.op == "global_addr"}
        self.assertIn("DefWindowProcA", addrs)

    def test_window_module_compiles_and_verifies(self) -> None:
        mod = build(self.SRC)
        validate_ir(mod)
        out = x86_backend.compile(mod, {"target_os": "windows", "abi": "win64"})
        self.assertTrue(next(iter(out.values())))


class ConstantFoldingTests(unittest.TestCase):
    def test_module_constants_fold_arithmetic(self) -> None:
        mod = build("""
        const A: int = 0 - 16
        const B: int = 0xFF000000 | 0xFF
        const C: int = A + 1
        func f() { ret C }: i64
        """)
        self.assertEqual(find(func(mod, "f"), "const").operands[0], -15)

    def test_string_constant_becomes_a_rodata_address(self) -> None:
        mod = build('const NAME: string = "hi"\nfunc f() { ret NAME as i64 }: i64')
        self.assertEqual([g.value for g in mod.data], ["hi"])
        self.assertIn("global_addr", ops(func(mod, "f")))

    def test_unfoldable_module_constant_is_rejected(self) -> None:
        with self.assertRaises(APCError) as ctx:
            build("extern func f(): i64\nconst X: int = f()")
        self.assertIn("must fold to a constant", str(ctx.exception))


class StrengthReductionTests(unittest.TestCase):
    """Powers of two off the hot path.

    Done in the frontend rather than left to `peephole` because the o2 preset
    is not usable here yet -- `licm,sink` miscompiles -- so a build with no
    passes at all still wants an integer divide replaced.
    """

    def test_multiply_becomes_a_shift(self) -> None:
        mod = build("func f(a: i64) { ret a * 8 }: i64")
        self.assertIn("shl", ops(func(mod, "f")))
        self.assertNotIn("imul", ops(func(mod, "f")))

    def test_unsigned_divide_and_modulo_become_shift_and_mask(self) -> None:
        div = ops(func(build("func f(a: u32) { ret a / 16 }: u32"), "f"))
        rem = ops(func(build("func f(a: u32) { ret a % 16 }: u32"), "f"))
        self.assertIn("shr", div)
        self.assertNotIn("udiv", div)
        self.assertIn("iand", rem)
        self.assertNotIn("urem", rem)

    def test_signed_modulo_biases_instead_of_dividing(self) -> None:
        """A bare mask is wrong for negatives, so the sign bias must be
        emitted -- and `irem` must still be gone."""
        emitted = ops(func(build("func f(a: i64) { ret a % 256 }: i64"), "f"))
        self.assertNotIn("irem", emitted)
        self.assertIn("sar", emitted)     # sign mask
        self.assertIn("iand", emitted)
        self.assertIn("isub", emitted)    # unbias

    def test_signed_divide_biases_instead_of_dividing(self) -> None:
        emitted = ops(func(build("func f(a: i64) { ret a / 4 }: i64"), "f"))
        self.assertNotIn("idiv", emitted)
        self.assertIn("sar", emitted)

    def test_non_power_of_two_is_left_alone(self) -> None:
        self.assertIn("idiv", ops(func(build("func f(a: i64) { ret a / 3 }: i64"), "f")))
        self.assertIn("imul", ops(func(build("func f(a: i64) { ret a * 3 }: i64"), "f")))

    def test_float_operands_are_untouched(self) -> None:
        emitted = ops(func(build("func f(a: f64) { ret a / 4.0 }: f64"), "f"))
        self.assertIn("fdiv", emitted)


class ReceiverBindingTests(unittest.TestCase):
    """A method's declared parameters must bind past the receiver.

    Zipping `decl.params` against the emitted list -- which carries `Parent`
    at index 0 -- shifts every name one slot early, so the first declared
    parameter silently reads the receiver pointer. It compiles, links, and
    produces wrong values rather than crashing.
    """

    SRC = """
    type Box {
        func constructor(w: i64, h: i64) {
            pub const Parent.W: i64 = w
            pub const Parent.H: i64 = h
        }: none
        func area() { ret Parent.W * Parent.H }: i64
    }
    """

    def test_receiver_is_parameter_zero_and_names_follow(self) -> None:
        ctor = func(build(self.SRC), "Box__constructor")
        self.assertEqual([p.name for p in ctor.params], ["Parent", "w", "h"])

    def test_each_field_stores_its_own_parameter(self) -> None:
        ctor = func(build(self.SRC), "Box__constructor")
        stores = [i for i in ctor.blocks[0].instrs if i.op == "store"]
        self.assertEqual([s.operands[0].name for s in stores], ["w", "h"])


class BackendIntegrationTests(unittest.TestCase):
    SRC = """
    const POLY: u32 = 0xEDB88320

    layout Header {
        magic: bytes[2]
        flags: bytes[1]
        len:   bytes[4] = 4
        crc:   bytes[4]
    }

    enum Status { OK, NA, ERR }

    func crc_byte(crc: u32, b: u8) {
        let acc = crc ^ (b as u32)
        for (i = 0..8) {
            if (acc & 1) { acc = (acc >> 1) ^ POLY } else { acc = acc >> 1 }
        }
        ret acc
    }: u32

    func header_len(h: Header) { ret h.len as i64 }: i64

    func main() {
        let total = 0
        for (i = 0..10) { total = total + i }
        ret total
    }: i64

    export main
    """

    def test_emitted_ir_passes_the_neutral_verifier(self) -> None:
        validate_ir(build(self.SRC))

    def test_bundled_sample_compiles_end_to_end(self) -> None:
        """The shipped sample is a real program, not aspirational syntax."""
        sample = (pathlib.Path(__file__).resolve().parents[1]
                  / "editors" / "vscode" / "apc-syntax" / "samples" / "packets.apc")
        self.assertTrue(sample.is_file(), sample)
        mod = build(sample.read_text(encoding="utf-8"))
        validate_ir(mod)
        self.assertIn("main", {f.name for f in mod.funcs})
        out = x86_backend.compile(mod, {"target_os": "windows", "abi": "win64"})
        self.assertTrue(next(iter(out.values())))

    def test_interned_strings_avoid_leading_dot_symbol_names(self) -> None:
        """A leading '.' collides with COFF section naming and links as an
        undefined symbol rather than a defined one."""
        mod = build('extern func puts(s: ptr): i32\nfunc f() { puts("x")\n ret 0 }: i64')
        for g in mod.data:
            self.assertFalse(g.name.startswith("."), g.name)

    def test_module_compiles_on_both_abis(self) -> None:
        mod = build(self.SRC)
        for abi, target_os in (("sysv", "linux"), ("win64", "windows")):
            with self.subTest(abi=abi):
                out = x86_backend.compile(mod, {"target_os": target_os, "abi": abi})
                self.assertTrue(out)
                self.assertTrue(next(iter(out.values())))


if __name__ == "__main__":
    unittest.main()
