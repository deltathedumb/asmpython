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

RUNTIME = "asmpython/jvm/Runtime"

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

    def __init__(self, cls: ClassBuilder, func, class_name: str, globals_map: dict) -> None:
        self.cls = cls
        self.func = func
        self.class_name = class_name
        self.globals_map = globals_map
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
        m.u2(m.pool.methodref(RUNTIME, name, descriptor))

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
        self._zero_non_parameters()

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
        self.method.max_locals = max(self.next_slot, 2)
        self.method.frame_locals = self.frame_locals()

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
                raise UnsupportedIR(f"unknown global {name!r}")
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
            owner, name = RUNTIME, _java_name(target)

        m.u1(INVOKESTATIC)
        m.u2(m.pool.methodref(owner, name, signature))

        if instr.result is not None:
            self.pop_into(instr.result)
        elif callee is not None and not signature.endswith(")V"):
            # A user function always returns a long; discard it when the call
            # site ignores the result, or the operand stack never unwinds.
            m.u1(POP2)

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
