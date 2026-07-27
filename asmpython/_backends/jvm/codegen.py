"""Translate an asmpython IRModule into JVM bytecode.

Value model
-----------
asmpython's IR has three value types: ``i64``, ``f64`` and ``ptr``. This backend
maps ``i64`` and ``ptr`` both to a JVM ``long`` and ``f64`` to ``double``. A
pointer being a plain integer is the whole trick: asmpython's memory model is a
flat address space, so the runtime backs it with a ByteBuffer and an address is
just an index into it. That keeps ``load``/``store``/``gep``/``alloca``
completely straightforward, and means no generated code ever handles a JVM
reference.

Every IR value gets its own JVM local slot. The JVM allows up to 65535 locals
per frame and does its own register allocation in the JIT, so there is nothing
to gain from reusing slots here -- the x86-64 backend's linear-scan allocator
has no counterpart in this one.
"""

from __future__ import annotations

import struct

from .classfile import ITEM_DOUBLE, ITEM_LONG, ClassBuilder, MethodBuilder

DEFAULT_RUNTIME = "asmpython/jvm/Runtime"
# Overridden by --jvm-runtime. A host embedding this backend points it at its
# own class -- which may simply extend the default one, since invokestatic
# resolves inherited statics -- so host functions link without the compiler
# knowing anything about them.
RUNTIME = DEFAULT_RUNTIME

# --- opcodes used here ---------------------------------------------------
LLOAD = 0x16
DLOAD = 0x18
LSTORE = 0x37
DSTORE = 0x39
LCONST_0 = 0x09
LDC2_W = 0x14
LADD, LSUB, LMUL, LDIV, LREM, LNEG = 0x61, 0x65, 0x69, 0x6D, 0x71, 0x75
LAND, LOR, LXOR = 0x7F, 0x81, 0x83
LSHL, LSHR, LUSHR = 0x79, 0x7B, 0x7D
DADD, DSUB, DMUL, DDIV, DNEG = 0x63, 0x67, 0x6B, 0x6F, 0x77
LCMP, DCMPL = 0x94, 0x97
L2D, D2L = 0x8A, 0x8F
IFEQ, IFNE, IFLT, IFGE, IFGT, IFLE = 0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E
GOTO = 0xA7
IRETURN, LRETURN, DRETURN, RETURN = 0xAC, 0xAD, 0xAF, 0xB1
INVOKESTATIC = 0xB8
GETSTATIC, PUTSTATIC = 0xB2, 0xB3
POP2 = 0x58
ICONST_0, ICONST_1 = 0x03, 0x04
I2L = 0x85
WIDE = 0xC4
DUP = 0x59
BIPUSH, SIPUSH = 0x10, 0x11
NEWARRAY, LASTORE = 0xBC, 0x50
T_LONG = 11                      # NEWARRAY's atype for long[]

# Calls that are variadic in C and must become one array argument here.
# `printf` is the only one the lowering emits, but naming the set rather than
# the symbol keeps the special case honest about what it is.
VARIADIC_RUNTIME_CALLS = {"printf"}

POP = 0x57
ATHROW = 0xBF
LDC = 0x12

# Symbols the `java` binding module synthesises for `p.Thing()`; the class name
# rides in the symbol because a call carries no constant of its own.
from .bindings import CLASS_PREFIX, NEW_PREFIX  # noqa: E402


def _is_string_arg(value) -> bool:
    """Whether an IR value is a string pointer rather than a number.

    The IR types both as 64-bit, so this reads the declared type; getting it
    wrong picks the int constructor for a string and fails to match any.
    """
    return str(getattr(value, "type", "")) in ("str", "ptr")

# The Java class a raise arrives as. Nested in Containers, so the internal
# name uses '$'. It follows the runtime PACKAGE rather than the runtime class:
# a host-supplied runtime extends the bundled one, so the exception is still
# the bundled one's.
DEFAULT_RUNTIME_PACKAGE = "asmpython/jvm"


def error_class(runtime_package: str = DEFAULT_RUNTIME_PACKAGE) -> str:
    return runtime_package.replace(".", "/") + "/Containers$AsmPythonError"


ASMPYTHON_ERROR = error_class()

_ICMP_TO_BRANCH = {
    "icmp.eq": IFEQ,
    "icmp.ne": IFNE,
    "icmp.lt": IFLT,
    "icmp.le": IFLE,
    "icmp.gt": IFGT,
    "icmp.ge": IFGE,
}
_FCMP_TO_BRANCH = {
    "fcmp.eq": IFEQ,
    "fcmp.ne": IFNE,
    "fcmp.lt": IFLT,
    "fcmp.le": IFLE,
    "fcmp.gt": IFGT,
    "fcmp.ge": IFGE,
}
_INT_BINOP = {
    "iadd": LADD, "isub": LSUB, "imul": LMUL, "idiv": LDIV, "irem": LREM,
    "iand": LAND, "ior": LOR, "ixor": LXOR, "shl": LSHL, "shr": LSHR,
}
_FLOAT_BINOP = {"fadd": DADD, "fsub": DSUB, "fmul": DMUL, "fdiv": DDIV}


class UnsupportedIR(Exception):
    """An IR construct this backend does not implement yet."""


def _is_double(value) -> bool:
    return getattr(value, "type", None) == "f64" or str(getattr(value, "type", "")) == "f64"


class FunctionEmitter:
    """Emits one IRFunc as a static JVM method."""

    def __init__(self, cls: ClassBuilder, func, class_name: str, globals_map: dict,
                 runtime: str = DEFAULT_RUNTIME,
                 runtime_globals: "list | None" = None,
                 runtime_package: str = DEFAULT_RUNTIME_PACKAGE) -> None:
        self.error_class = error_class(runtime_package)
        self.runtime = runtime
        self.cls = cls
        self.func = func
        self.class_name = class_name
        self.globals_map = globals_map
        # Shared across every function in the module: one function may be the
        # first to touch a runtime global that several then use.
        self.runtime_globals = runtime_globals if runtime_globals is not None else []
        self.slots: dict[str, int] = {}
        self.slot_kinds: dict[int, int] = {}  # slot -> ITEM_LONG | ITEM_DOUBLE
        self.next_slot = 0
        self.method: MethodBuilder | None = None

    def slot_of(self, value) -> int:
        """Assign (or recall) the JVM local slot for an IR value.

        A long or double occupies two consecutive slots -- indexing them as one
        is the classic way to produce a class that fails verification.
        """
        name = value.name
        existing = self.slots.get(name)
        if existing is not None:
            return existing
        slot = self.next_slot
        self.slots[name] = slot
        self.slot_kinds[slot] = ITEM_DOUBLE if _is_double(value) else ITEM_LONG
        self.next_slot += 2  # every value here is long or double
        return slot

    # ---- value movement --------------------------------------------------

    def local_op(self, opcode: int, slot: int) -> None:
        """Emit a local load/store, widening the index when it needs it.

        lload/lstore take a ONE-BYTE local index. A function with more than 128
        IR values overflows that (each long takes two slots), and the index
        silently wraps to another live local -- so this is a correctness bug at
        every class-file version, not just one the newer verifier happens to
        catch. Indices from 256 need the `wide` prefix and a u2 operand.
        """
        m = self.method
        if slot < 256:
            m.u1(opcode)
            m.u1(slot)
        else:
            m.u1(WIDE)
            m.u1(opcode)
            m.u2(slot)

    def push(self, value) -> None:
        if isinstance(value, str):
            raise UnsupportedIR(f"bare string operand {value!r}")
        self.local_op(DLOAD if _is_double(value) else LLOAD, self.slot_of(value))

    def pop_into(self, value) -> None:
        self.local_op(DSTORE if _is_double(value) else LSTORE, self.slot_of(value))

    def push_long(self, raw: int) -> None:
        m = self.method
        m.u1(LDC2_W)
        m.u2(m.pool.long(raw))

    def push_double(self, raw: float) -> None:
        m = self.method
        m.u1(LDC2_W)
        m.u2(m.pool.double(raw))

    def call_runtime(self, name: str, descriptor: str) -> None:
        m = self.method
        m.u1(INVOKESTATIC)
        m.u2(m.pool.methodref(self.runtime, name, descriptor))

    def declare_runtime_global(self, name: str) -> str:
        """Add a field for a global the data section never declared.

        Safe to do mid-emission: fields are only written out at serialize time,
        and `<clinit>` is built after every function, so it sees the final set.
        `runtime_globals` records the order for that initialisation.
        """
        field = f"r{len(self.runtime_globals)}_{_java_name(name)}"
        self.globals_map[name] = field
        self.runtime_globals.append(name)
        self.cls.add_field(field, "J")
        return field

    # ---- entry -----------------------------------------------------------

    def emit(self) -> None:
        descriptor = descriptor_for(self.func)
        self.method = self.cls.method(_java_name(self.func.name), descriptor)

        for param in self.func.params:
            self.slot_of(param)

        # Assign a slot to every value up front, then zero the non-parameter
        # ones. Pre-initialising is what keeps each slot's type constant for
        # the whole method, which is what lets the StackMapTable be a single
        # repeated frame instead of a dataflow analysis (see
        # MethodBuilder.stack_map_table).
        self._preassign_slots()

        # Reserve the landing pad's scratch BEFORE zeroing, so it is written on
        # entry like every other local. A slot the verifier sees as `top` at the
        # handler cannot be claimed as a long by the stack map, and the handler
        # is reached without executing anything that would have written it.
        self.setjmp_sites = self._find_setjmp_sites()
        if self.setjmp_sites:
            self._scratch_slot()

        self._zero_non_parameters()
        body_start = self.method.here()

        for block in self.func.blocks:
            self.method.mark(block.label)
            for instr in block.instrs:
                self.emit_instr(instr)

        # A function whose last block falls through still needs a return, and
        # it has to match the declared descriptor or the verifier rejects the
        # class before main() ever runs. This epilogue usually follows a `ret`
        # or a `goto`, so it is unreachable by fallthrough and needs its own
        # stack map frame.
        self.method.frame_point()
        if descriptor.endswith(")D"):
            self.push_double(0.0)
            self.method.u1(DRETURN)
        elif descriptor.endswith(")J"):
            self.method.u1(LCONST_0)
            self.method.u1(LRETURN)
        else:
            self.method.u1(RETURN)

        self._emit_landing_pad(body_start)
        self.method.max_locals = max(self.next_slot, 2)
        self.method.frame_locals = self.frame_locals()

    # ---- exceptions ------------------------------------------------------
    #
    # asmpython lowers try/except to setjmp/longjmp, which the JVM does not
    # have. It has something strictly stronger: throw already unwinds the
    # stack, so a raise crossing frames needs nothing at all. What is left is
    # landing in the right place, and that is what this does.

    def _find_setjmp_sites(self) -> "dict[str, int]":
        """Pair each `_abi_setjmp` call with the block its handler lives in.

        The lowering emits, inside one block:

            t = call _abi_setjmp(buf)
            br.t t, handler_block, body_block

        so the block to resume at is readable straight off the branch. Landing
        on a BLOCK is what keeps this cheap -- a block already has a stack map
        frame and an empty operand stack, where resuming mid-block would need a
        frame invented at an arbitrary offset.

        Also records the id per call RESULT, so the call site can stamp it into
        the jmp_buf as it is emitted.
        """
        sites: dict[str, int] = {}
        self.setjmp_ids = {}
        for block in self.func.blocks:
            pending = None
            for instr in block.instrs:
                if (instr.op == "call" and instr.operands
                        and _unquote(instr.operands[0]) == "_abi_setjmp"):
                    pending = instr.result
                elif (pending is not None and instr.op == "br.t"
                      and instr.operands and instr.operands[0] is pending):
                    # Unique across the MODULE, not the method. Per-method
                    # numbering makes every function's first try id 1, so a
                    # function whose own try has already finished sees a
                    # caller's live handler as its own, jumps into a handler
                    # that was never entered, and loops.
                    site_id = FunctionEmitter.next_site_id
                    FunctionEmitter.next_site_id += 1
                    sites[_unquote(instr.operands[1])] = site_id
                    self.setjmp_ids[_value_key(pending)] = site_id
                    pending = None
        return sites

    def _emit_landing_pad(self, body_start: int) -> None:
        """Catch a raise and resume at the try that is currently installed.

        One handler per method, not per try: the JVM's exception table maps a
        bytecode RANGE to a target, while asmpython's handler stack is dynamic
        state, so the choice of landing site has to be made at runtime. The
        site id stored in each jmp_buf is what makes it.

        A site id that is not this method's means the live handler belongs to
        some outer frame, and the exception is rethrown so it keeps unwinding.
        Without that check a function that merely CONTAINS a try would swallow
        exceptions belonging to its callers.
        """
        if not self.setjmp_sites:
            return

        handler_top = self.globals_map.get("_runtime_handler_top")
        if handler_top is None:
            return

        m = self.method
        pad = m.here()
        m.catch(body_start, pad, pad, self.error_class)

        m.u1(POP)                                   # the exception; globals carry it

        # site = loadLong(loadLong(&_runtime_handler_top))
        m.u1(GETSTATIC)
        m.u2(m.pool.fieldref(self.class_name, handler_top, "J"))
        self.call_runtime("loadLong", "(J)J")
        self.call_runtime("loadLong", "(J)J")
        scratch = self._scratch_slot()
        self.local_op(LSTORE, scratch)

        for label, site_id in self.setjmp_sites.items():
            self.local_op(LLOAD, scratch)
            self.push_long(site_id)
            m.u1(LCMP)
            m.jump(IFEQ, label)
            m.frame_point()                          # the fallthrough after a branch

        # Not ours: keep unwinding. Rebuilt from the globals rather than
        # rethrowing the caught object, which would need a reference local and
        # break the one-shape-fits-all stack map. `_abi_rethrow` always throws,
        # but the verifier still needs a terminator after it.
        self.call_runtime("_abi_rethrow", "()V")
        descriptor = self.method.descriptor
        if descriptor.endswith(")D"):
            self.push_double(0.0)
            m.u1(DRETURN)
        elif descriptor.endswith(")J"):
            m.u1(LCONST_0)
            m.u1(LRETURN)
        else:
            m.u1(RETURN)

    def _scratch_slot(self) -> int:
        """A long slot for the landing pad, outside the IR's value slots.

        Registered in `slots` under a name no IR value can have, so the entry
        zeroing and the stack map both pick it up automatically rather than
        needing to know it exists.
        """
        if self._landing_scratch is None:
            self._landing_scratch = self.next_slot
            self.slots["__landing_site"] = self._landing_scratch
            self.slot_kinds[self._landing_scratch] = ITEM_LONG
            self.next_slot += 2
        return self._landing_scratch

    _landing_scratch = None
    setjmp_sites: dict = {}
    setjmp_ids: dict = {}
    # Module-wide, reset per module by compile_module. See _find_setjmp_sites
    # for why this cannot be per-method.
    next_site_id: int = 1

    def _preassign_slots(self) -> None:
        """Give every value defined anywhere in the function its slot now."""
        for block in self.func.blocks:
            for instr in block.instrs:
                if instr.result is not None:
                    self.slot_of(instr.result)
                for operand in instr.operands:
                    if hasattr(operand, "name") and hasattr(operand, "type"):
                        self.slot_of(operand)

    def _zero_non_parameters(self) -> None:
        """Write a zero into every slot the caller did not supply."""
        parameter_slots = {self.slots[p.name] for p in self.func.params}
        for name, slot in sorted(self.slots.items(), key=lambda kv: kv[1]):
            if slot in parameter_slots:
                continue
            if self.slot_kinds.get(slot) == ITEM_DOUBLE:
                self.push_double(0.0)
                self.local_op(DSTORE, slot)
            else:
                self.method.u1(LCONST_0)
                self.local_op(LSTORE, slot)

    def frame_locals(self) -> list:
        """Slot layout for the StackMapTable, in slot order."""
        return [(slot, self.slot_kinds[slot])
                for slot in sorted(self.slot_kinds)]

    # ---- instructions ----------------------------------------------------

    def emit_instr(self, instr) -> None:
        op = instr.op
        m = self.method

        if op == "const":
            raw = instr.operands[0]
            if _is_double(instr.result):
                self.push_double(float(raw))
            else:
                self.push_long(_as_long(raw))
            self.pop_into(instr.result)
            return

        if op in _INT_BINOP:
            self.push(instr.operands[0])
            self.push(instr.operands[1])
            if op in ("shl", "shr"):
                # JVM shift ops take an int shift count, not a long.
                m.u1(0x88)  # l2i
            m.u1(_INT_BINOP[op])
            self.pop_into(instr.result)
            return

        if op in _FLOAT_BINOP:
            self.push(instr.operands[0])
            self.push(instr.operands[1])
            m.u1(_FLOAT_BINOP[op])
            self.pop_into(instr.result)
            return

        if op == "ineg":
            self.push(instr.operands[0])
            m.u1(LNEG)
            self.pop_into(instr.result)
            return

        if op == "fneg":
            self.push(instr.operands[0])
            m.u1(DNEG)
            self.pop_into(instr.result)
            return

        if op == "inot":
            self.push(instr.operands[0])
            self.push_long(-1)
            m.u1(LXOR)
            self.pop_into(instr.result)
            return

        if op in _ICMP_TO_BRANCH or op in _FCMP_TO_BRANCH:
            self.emit_compare(instr)
            return

        if op in ("sitofp",):
            self.push(instr.operands[0])
            m.u1(L2D)
            self.pop_into(instr.result)
            return

        if op in ("fptosi",):
            self.push(instr.operands[0])
            m.u1(D2L)
            self.pop_into(instr.result)
            return

        if op in ("zext", "sext", "trunc"):
            # Everything is already a 64-bit long in this backend.
            self.push(instr.operands[0])
            self.pop_into(instr.result)
            return

        if op == "bitcast_f2i":
            self.push(instr.operands[0])
            self.call_runtime("doubleToRawLongBits", "(D)J")
            self.pop_into(instr.result)
            return

        if op == "bitcast_i2f":
            self.push(instr.operands[0])
            self.call_runtime("longBitsToDouble", "(J)D")
            self.pop_into(instr.result)
            return

        if op == "alloca":
            self.push_long(8)
            self.call_runtime("alloca", "(J)J")
            self.pop_into(instr.result)
            return

        if op == "load":
            self.push(instr.operands[0])
            if _is_double(instr.result):
                self.call_runtime("loadDouble", "(J)D")
            else:
                self.call_runtime("loadLong", "(J)J")
            self.pop_into(instr.result)
            return

        if op == "store":
            value, pointer = instr.operands[0], instr.operands[1]
            self.push(pointer)
            self.push(value)
            if _is_double(value):
                self.call_runtime("storeDouble", "(JD)V")
            else:
                self.call_runtime("storeLong", "(JJ)V")
            return

        if op == "gep":
            self.push(instr.operands[0])
            offset = instr.operands[1]
            if hasattr(offset, "name"):
                self.push(offset)
            else:
                self.push_long(_as_long(offset))
            m.u1(LADD)
            self.pop_into(instr.result)
            return

        if op == "global_addr":
            name = _unquote(instr.operands[0])
            field = self.globals_map.get(name)
            if field is None:
                # Runtime state rather than a data-section literal: the
                # exception machinery keeps `_runtime_exc_msg` and friends in
                # storage that outlives any function, which the native backend
                # gets from the runtime library's .bss. Declared on demand
                # rather than from a list, so a lowering that introduces a new
                # one does not come back as "unknown global".
                field = self.declare_runtime_global(name)
            m.u1(GETSTATIC)
            m.u2(m.pool.fieldref(self.class_name, field, "J"))
            self.pop_into(instr.result)
            return

        if op == "call":
            self.emit_call(instr)
            return

        if op == "ret":
            if instr.operands:
                self.push(instr.operands[0])
                m.u1(DRETURN if _is_double(instr.operands[0]) else LRETURN)
            else:
                m.u1(RETURN)
            return

        if op == "br":
            m.jump(GOTO, _unquote(instr.operands[0]))
            return

        if op == "br.t":
            self.push(instr.operands[0])
            m.u1(LCONST_0)
            m.u1(LCMP)
            m.jump(IFNE, _unquote(instr.operands[1]))
            m.jump(GOTO, _unquote(instr.operands[2]))
            return

        raise UnsupportedIR(f"op {op!r}")

    def emit_compare(self, instr) -> None:
        """Materialise a comparison as a 0/1 long.

        The JVM has no long comparison that yields a boolean: ``lcmp`` leaves
        -1/0/1 and you branch on that. So this emits the compare, a conditional
        jump to a "push 1" stub, and a fallthrough that pushes 0.
        """
        m = self.method
        op = instr.op
        double = op in _FCMP_TO_BRANCH
        branch = (_FCMP_TO_BRANCH if double else _ICMP_TO_BRANCH)[op]

        self.push(instr.operands[0])
        self.push(instr.operands[1])
        m.u1(DCMPL if double else LCMP)

        # Both arms store into the result slot before merging, so the operand
        # stack is EMPTY at `true_label` and at `done_label`. Leaving the value
        # on the stack across the merge would work fine for the old verifier
        # but makes every StackMapTable frame different, which is exactly the
        # analysis this backend avoids having to do.
        true_label = f"__cmp_t{m.here()}"
        done_label = f"__cmp_d{m.here()}"
        m.jump(branch, true_label)
        m.u1(LCONST_0)
        self.pop_into(instr.result)
        m.jump(GOTO, done_label)
        m.mark(true_label)
        m.u1(ICONST_1)
        m.u1(I2L)
        self.pop_into(instr.result)
        m.mark(done_label)

    def emit_call(self, instr) -> None:
        m = self.method
        target = _unquote(instr.operands[0])
        args = instr.operands[1:]

        if target in VARIADIC_RUNTIME_CALLS and target not in self.cls_functions:
            self.emit_variadic_call(target, args)
            return

        if target == "_abi_setjmp":
            self.emit_setjmp(instr, args)
            return

        if target.startswith(NEW_PREFIX):
            self.emit_named_construction(instr, target[len(NEW_PREFIX):], args)
            return

        if target.startswith(CLASS_PREFIX):
            self.emit_named_class(instr, target[len(CLASS_PREFIX):])
            return

        for arg in args:
            self.push(arg)

        callee = self.cls_functions.get(target)
        if callee is not None:
            # Use the callee's DECLARED descriptor. Deriving it from the
            # argument values instead works right up until an int flows into a
            # float parameter, and then the class fails verification.
            signature = descriptor_for(callee)
            owner, name = self.class_name, _java_name(target)
        else:
            returns_double = instr.result is not None and _is_double(instr.result)
            signature = "(" + "".join("D" if _is_double(a) else "J" for a in args) + ")"
            signature += "D" if returns_double else ("J" if instr.result is not None else "V")
            owner, name = self.runtime, _java_name(target)

        m.u1(INVOKESTATIC)
        m.u2(m.pool.methodref(owner, name, signature))

        if instr.result is not None:
            self.pop_into(instr.result)
        elif callee is not None and not signature.endswith(")V"):
            # A user function always returns a long; discard it when the call
            # site ignores the result, or the operand stack never unwinds.
            m.u1(POP2)

    def emit_named_class(self, instr, class_name: str) -> None:
        """`import java.<pkg> as p; X = p.Thing` -> a handle to the class."""
        m = self.method
        m.u1(LDC)
        m.u1(m.pool.string(class_name) & 0xFF)
        self.call_runtime("jclass_named", "(Ljava/lang/String;)J")
        if instr.result is not None:
            self.pop_into(instr.result)
        else:
            m.u1(POP2)

    def emit_named_construction(self, instr, class_name: str, args) -> None:
        """`import java.<pkg> as p; p.Thing(...)` -> construct that class.

        The class name arrives inside the SYMBOL rather than as an argument,
        because the frontend emits `call <name>` and nothing else -- it has no
        way to attach a constant to a call and no reason to know one is wanted.
        Splitting it back out here keeps the import sugar on this side of the
        compiler.

        Pushed with `ldc` as a real java.lang.String, not interned into the
        heap: the name is known at compile time, so a heap copy per call would
        be work with nothing to show for it.
        """
        m = self.method
        m.u1(LDC)
        m.u1(m.pool.string(class_name) & 0xFF)

        if not args:
            self.call_runtime("jnew_named", "(Ljava/lang/String;)J")
        else:
            # Words and kinds as two long[], the same shape printf uses. One
            # path for every arity beats a method per argument combination.
            self.push_word_array(args)
            self.push_kind_array(args)
            self.call_runtime("jnew_named_v", "(Ljava/lang/String;[J[J)J")

        if instr.result is not None:
            self.pop_into(instr.result)
        else:
            m.u1(POP2)

    def push_word_array(self, args) -> None:
        """A `long[]` of the argument words, doubles bit-cast to their bits."""
        m = self.method
        self.push_int(len(args))
        m.u1(NEWARRAY)
        m.u1(T_LONG)
        for index, arg in enumerate(args):
            m.u1(DUP)
            self.push_int(index)
            self.push(arg)
            if _is_double(arg):
                self.call_runtime("doubleToRawLongBits", "(D)J")
            m.u1(LASTORE)

    def push_kind_array(self, args) -> None:
        """A `long[]` saying how to read each word: 1 = string, 0 = number."""
        m = self.method
        self.push_int(len(args))
        m.u1(NEWARRAY)
        m.u1(T_LONG)
        for index, arg in enumerate(args):
            m.u1(DUP)
            self.push_int(index)
            self.push_long(1 if _is_string_arg(arg) else 0)
            m.u1(LASTORE)

    def emit_setjmp(self, instr, args) -> None:
        """`setjmp(buf)`: stamp this site's id into the buffer, return 0.

        There is no saved machine context to restore, because the JVM's throw
        already unwinds for us. All the buffer has to carry is WHICH try
        installed it, so the landing pad can resume at the matching block.

        Returning 0 unconditionally is correct: the nonzero "returned from a
        longjmp" case never comes back through here, it is entered by the
        landing pad jumping straight at the handler block.
        """
        m = self.method
        site_id = self.setjmp_ids.get(_value_key(instr.result))
        if site_id is None:
            # A setjmp whose branch was not found: refuse rather than emit code
            # that silently never catches anything.
            raise UnsupportedIR("_abi_setjmp with no matching conditional branch")

        self.push(args[0])
        self.push_long(site_id)
        self.call_runtime("storeLong", "(JJ)V")

        m.u1(LCONST_0)
        self.pop_into(instr.result)

    def emit_variadic_call(self, target: str, args) -> None:
        """A C-variadic runtime call, as `(format, long[] rest)`.

        Fixed-arity overloads cannot express this. The lowering emits printf
        with however many arguments the statement happens to have, so any set
        of overloads is one `print` away from a NoSuchMethodError -- and a
        float argument pushes a `D`, which would need an overload per position
        as well as per count.

        Packing the tail into a `long[]` removes both problems at once: a
        double is bit-cast to its 64-bit word on the way in, which is exactly
        what a C varargs call passes and what the native runtime reads.
        """
        m = self.method
        rest = list(args[1:])

        if args:
            self.push(args[0])
        else:
            self.push_long(0)

        self.push_int(len(rest))
        m.u1(NEWARRAY)
        m.u1(T_LONG)
        for index, arg in enumerate(rest):
            m.u1(DUP)
            self.push_int(index)
            self.push(arg)
            if _is_double(arg):
                # The array holds raw WORDS, so this must reinterpret the bits.
                # D2L would convert numerically -- 3.5 would arrive as 3 -- and
                # the runtime's %f, which bit-casts back, would then print
                # garbage rather than the number.
                self.call_runtime("doubleToRawLongBits", "(D)J")
            m.u1(LASTORE)

        self.call_runtime(target, "(J[J)V")

    def push_int(self, value: int) -> None:
        """An int on the stack, for an array length or index."""
        m = self.method
        if value == 0:
            m.u1(ICONST_0)
        elif value == 1:
            m.u1(ICONST_1)
        elif -128 <= value <= 127:
            m.u1(BIPUSH)
            m.u1(value & 0xFF)
        else:
            m.u1(SIPUSH)
            m.u2(value & 0xFFFF)

    cls_functions: dict = {}


def descriptor_for(func) -> str:
    """The JVM descriptor for an IRFunc.

    Parameters come from their declared TYPES, not just their count: a float
    parameter is a `D`, and getting that wrong fails verification with
    "Register pair contains wrong type" rather than anything pointing at the
    real mistake.

    The RETURN type is read off the function's `ret` instructions rather than
    `func.ret_type`, because that field is not reliable -- a function returning
    a float is declared `i64` while its `ret` operand and every call site agree
    it is `f64`. The x86-64 backend never notices, since both are one 64-bit
    register there; on the JVM they are different types and different opcodes.
    """
    params = "".join("D" if _is_double(p) else "J" for p in func.params)

    returns_value = False
    returns_double = False
    for block in func.blocks:
        for instr in block.instrs:
            if instr.op == "ret" and instr.operands:
                returns_value = True
                if _is_double(instr.operands[0]):
                    returns_double = True
    if returns_double:
        result = "D"
    elif returns_value or func.ret_type is not None:
        result = "J"
    else:
        result = "V"
    return f"({params}){result}"


def _value_key(value) -> str:
    """A stable identity for an IR value, for keying per-site tables."""
    return getattr(value, "name", None) or str(value)


def _java_name(symbol: str) -> str:
    """Make an asmpython symbol a legal JVM method name."""
    return symbol.replace(".", "_").replace("$", "_")


def _unquote(operand) -> str:
    text = operand if isinstance(operand, str) else str(operand)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _as_long(raw) -> int:
    if isinstance(raw, bool):
        return 1 if raw else 0
    if isinstance(raw, int):
        return raw
    text = str(raw)
    try:
        return int(text, 0)
    except ValueError:
        return struct.unpack(">q", struct.pack(">d", float(text)))[0]
