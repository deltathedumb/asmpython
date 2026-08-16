"""Typed AST -> IR.

Runs only after analysis reported no errors, so it may assume every expression
has a real type and every name resolves. That assumption is what keeps this
file about CODE GENERATION rather than about validation -- there is not a
single "if this is None" here, and there should never be one.

WHERE PYTHON AND THE MACHINE DISAGREE

Three places, and handling them is most of what this file does:

  `//` and `%`   Python floors toward negative infinity and takes the sign of
                 the divisor. `Op.DIV` truncates toward zero and `Op.REM` takes
                 the sign of the dividend, like C and like every machine. Both
                 are corrected with a branch on "signs differ and the remainder
                 is non-zero".

  `and` / `or`   Yield an OPERAND, not a bool, and short-circuit. Lowered to
                 branches writing one shared result register -- which is
                 straightforward precisely because registers are mutable. Under
                 SSA each would need a phi.

  chained `<`    `a < b < c` evaluates `b` once. Lowered as nested branches, not
                 as two independent comparisons over a re-evaluated middle.

Paying that cost once here is the alternative to every backend owing Python's
semantics, and it is the entire argument for keeping the IR small.
"""
from __future__ import annotations

import ast

from ...diagnostics import SourceFile, Span
from ...ir import Builder, Function, Module, types as T
from ...ir.module import Global, Instruction, Linkage
from ...ir.opcodes import Op
from .analysis import (
    BOOL, ENTRY_NAME, FLOAT, INT, MACHINE, MEMORY_INTRINSICS, NONE, OBJ,
    OBJECT_RUNTIME, PLATFORM, FunctionInfo, SemType,
    TO_IR, sem_type,
    const_int, int_literal, span_of,
)
from ...link.objects import signatures as object_signatures
from .dynamic import DynamicLowering
from .methods import DYN_METHOD_TABLE

_CMP_OPS = {
    ast.Eq: Op.EQ, ast.NotEq: Op.NE, ast.Lt: Op.LT,
    ast.LtE: Op.LE, ast.Gt: Op.GT, ast.GtE: Op.GE,
}

#: Runtime functions the frontend may call. A frontend decides its own runtime;
#: the IR has no I/O opcodes, so printing is a call like any other.
#:
#: `put_*` write a value with NO newline, and `putchar` supplies the space
#: between arguments and the newline at the end. Python's `print(1, 2)` is one
#: line reading `1 2`, and `print()` is an empty line; a runtime whose only
#: primitive appends a newline can express neither, and the frontend produced
#: two separate lines for the first and no output at all for the second.
_RUNTIME = {
    "put_int": ([T.I64], T.VOID),
    "put_float": ([T.F64], T.VOID),
    #: `bool` and `None` are integers to the machine and distinct types to
    #: Python. Printing them through `put_int` is not a wrong value, it is a
    #: wrong RENDERING -- `True` came out as `1` and `None` as `0` -- and the
    #: only place that distinction still exists at this point is the static
    #: type, so the choice of writer has to be made here.
    "put_bool": ([T.I64], T.VOID),
    "put_none": ([], T.VOID),
    "putchar": ([T.I64], T.I64),
    #: `x ** n` for a non-negative integral n. NOT libm's `pow`: mingw's is a
    #: ulp off where CPython's is correctly rounded, so every float `**` in a
    #: compiled program printed a different last digit. See `py_pow_int` in
    #: link/runtime.py.
    "py_pow_int": ([T.F64, T.I64], T.F64),
}

_SPACE, _NEWLINE = 32, 10

#: The symbol that turns UTF-8 bytes in memory into a Java `String`. Matches
#: `backends/jvm/interop.STRING_SYMBOL`: the frontend names it, the backend
#: defines it, and neither builds the other's spelling.
JAVA_STRING = "jvm$str"

#: The object runtime, as IR signatures -- READ OUT OF THE C, not listed here.
#:
#: The frontend must declare each runtime symbol it calls with the right
#: signature, and a hand-kept list drifted three separate times in one
#: afternoon. Each time the same way: a symbol added to the runtime, not added
#: here, and the failure surfacing as `call to unknown function` from the IR
#: verifier or as a link error -- neither of which names the list that was not
#: updated. `signatures()` parses the definitions instead, so the two cannot
#: disagree.
#:
#: Declarations nothing calls are dropped in `run()`, so declaring all of them
#: costs nothing in the output.
_IR_TYPE_BY_NAME = {"ptr": T.PTR, "i64": T.I64, "f64": T.F64, "void": T.VOID}
_OBJECT_RUNTIME = {
    name: ([_IR_TYPE_BY_NAME[a] for a in args], _IR_TYPE_BY_NAME[ret])
    for name, (args, ret) in object_signatures().items()
}

#: The platform floor, as IR signatures. Declared alongside `_RUNTIME` and
#: dropped in `run()` if nothing calls it, so a program that never asks the
#: platform for anything acquires no dependency on it.
_PLATFORM_IR = {name: ([TO_IR[a] for a in args], TO_IR[ret])
                for name, (args, ret) in PLATFORM.items()}

#: `int(x)`/`float(x)`/`bool(x)`. Lowered as coercions, not calls -- there is
#: nothing to call, and emitting a call would make every backend depend on a
#: runtime for what is one instruction or none.
_CONVERSIONS = {"int": INT, "float": FLOAT, "bool": BOOL}


def _is_plain_symbol(name: str) -> bool:
    """Whether a function's KEY can be its IR symbol unchanged.

    Module-level `def`s keep their bare name, which is what every existing
    test and every reader expects to find in the IR. Anything nested has a key
    built from its qualified name and needs sanitising.
    """
    return name.isidentifier()


class Lowerer(DynamicLowering):
    def __init__(self, functions: dict[str, FunctionInfo],
                 source: "SourceFile", analyzer=None, *,
                 library: bool = False) -> None:
        self.infos = functions
        self.library = library
        #: Java string literal -> the global holding its UTF-8 bytes. One
        #: global per distinct literal, so `setName("stone")` twice costs one.
        self.java_strings: dict[str, str] = {}
        #: `reserve` names already given a global. See `_memory`.
        self.reserved: set[str] = set()
        self.source = source
        #: What the `class` statements bound, and the two node-id indexes that
        #: get from a statement back to what analysis registered for it.
        self.classes = getattr(analyzer, "classes", {})
        self.class_of_node = getattr(analyzer, "class_of_node", {})
        self.def_keys = getattr(analyzer, "def_of_node", {})
        self.exc_classes = getattr(analyzer, "exc_classes", {})
        #: Module-level names more than one `def` binds. A call to one has to
        #: go through the NAME, because which body it reaches depends on where
        #: the call sits rather than on anything visible at the call site.
        self.rebound = getattr(analyzer, "rebound", set())
        #: Calls whose argument count is provably wrong. Lowered through the
        #: value path so the RUNTIME reports them -- Python's answer is a
        #: TypeError a program may catch, and the direct path would be a C
        #: compile error instead.
        self.late_arity = getattr(analyzer, "late_arity", set())
        source_name = source.path.stem if source.path else "module"
        #: The IR symbol per Python function name. The entry point is called
        #: `main` in the IR -- backends rename that to `ENTRY_SYMBOL` and the C
        #: runtime calls it -- so when the module's top-level statements are
        #: the entry, a user function also spelled `main` has to move aside.
        #: Emitting both under one name is two definitions of the entry symbol,
        #: which surfaces as a C compiler error about a function nobody wrote.
        #:
        #: A nested `def` or a method has a key no C identifier can be
        #: (`outer.<locals>.inner`, `Point.move`), so it is sanitised and made
        #: unique -- the SYMBOL must be unique, and two classes may each have a
        #: `move`.
        self.symbols = {name: name for name in functions}
        taken = {n for n in functions if _is_plain_symbol(n)}
        for name in functions:
            # ENTRY_NAME is `<module>`, which is not an identifier either --
            # but it has its own symbol, assigned below, and sanitising it
            # here would rename the entry point to `pyf___module_` and leave
            # the program with no `main`.
            if _is_plain_symbol(name) or name == ENTRY_NAME:
                continue
            base = "pyf_" + "".join(
                c if c.isalnum() or c == "_" else "_" for c in name)
            candidate, n = base, 1
            while candidate in taken:
                n += 1
                candidate = f"{base}_{n}"
            taken.add(candidate)
            self.symbols[name] = candidate
        if ENTRY_NAME in functions:
            self.symbols[ENTRY_NAME] = "main"
            if "main" in functions:
                self.symbols["main"] = "py_main"
        #: Interned string literal data, keyed by the bytes. See
        #: `DynamicLowering._dyn_str_literal` for why identity matters.
        self._strings: dict[bytes, str] = {}
        #: Bytes literals, interned by content like the str ones.
        self._bytes: dict[bytes, str] = {}
        #: Builtin name -> the symbol of the thunk that wraps it, for builtins
        #: used as VALUES. One per builtin per module, emitted on first use.
        self._builtin_thunks: dict[str, str] = {}
        self._pending_thunks: list = []
        #: PEP 649 annotation thunks, as (symbol, [(name, expr)]). Emitted
        #: alongside the builtin ones -- see `_dyn_emit_annotate_thunks`.
        self._pending_annotations: list = []
        #: Runtime symbols wrapped as callable values -- `math.sqrt`. Same
        #: shape as the builtin thunks, with an arity that is not always one.
        self._native_thunks: dict[str, str] = {}
        self._pending_natives: list = []
        #: While lowering a generator's STEP function: the register holding
        #: the generator, and the slot each local lives in. Reads and writes of
        #: a local go through the object rather than a register, because a
        #: register does not survive the return a `yield` compiles to.
        self._gen: tuple | None = None
        #: Comprehension targets, standing in for their enclosing binding
        #: while the comprehension runs -- see `_dyn_comprehension`.
        self._shadow: dict[str, int] = {}
        #: NAMES THE CLASS BODY BEING LOWERED HAS ALREADY BOUND. A class body
        #: is a scope that runs top to bottom, so `y = x + 1` and `@v.setter`
        #: both read something the body itself produced. Empty everywhere
        #: else, and saved/restored around a nested class.
        self._class_scope: dict[str, int] = {}
        #: PEP 657. One (function, span) per statement lowered, in lowering
        #: order -- which is source order. The index into this list is what
        #: `apy_at` stores, and `apy_code_of` reads the rows back to answer
        #: `co_positions()`.
        #:
        #: EMPTY UNLESS THE PROGRAM ASKS. Recording costs a call per statement
        #: at run time, and a program that never looks at a traceback should
        #: not pay it -- see `_wants_positions`.
        self._positions: list = []
        #: Memoised answer to `_wants_positions`, which reads the
        #: whole source and is asked once per statement.
        self._positions_wanted = None
        #: While a class body's GENERAL statements are lowered: the namespace
        #: they bind into, the class's own name (for private-name mangling),
        #: and which names go there. None outside one. See `_dyn_class`.
        self._class_binds: tuple | None = None
        #: THE EXCEPTION EACH ENCLOSING `except` BODY CAUGHT, innermost last.
        #: A bare `raise` re-raises the last of them: entering a handler
        #: clears the error flag, so there is nothing left to propagate by
        #: itself.
        self._handling: list = []
        #: `import math` twice binds the SAME namespace, so a module is built
        #: once per program: `import math as m` then `math is m` is True.
        self._modules: dict[str, int] = {}
        #: Module-level names, as IR globals. One pointer-sized cell each,
        #: zero-initialised -- and zero is not a valid runtime value, so
        #: "never assigned" is distinguishable from "assigned None", which is
        #: what makes a read before assignment a NameError rather than a
        #: silent None. See `_dyn_global_read`.
        entry = functions.get(ENTRY_NAME)
        self.module_names = (
            set(entry.locals) | getattr(analyzer, "module_names", set())
            if entry is not None else set())
        self.module = Module(name=source_name)
        self.module.metadata["frontend"] = "python"
        self.module.metadata["source"] = source.name

    def default_symbol(self, info, index: int) -> str:
        """The cell holding one default value of one function.

        Built from the IR SYMBOL, not from the Python name: a method's key is
        `Point.__init__`, and `gd_Point.__init___0` is not an identifier any C
        compiler or assembler will accept.
        """
        return f"gd_{self.symbols[info.name]}_{index}"

    def global_symbol(self, name: str) -> str:
        """The IR global backing a module-level Python name.

        Prefixed, because a module-level `main` or `print` would otherwise
        collide with a function symbol in the same namespace.
        """
        return "gv_" + name

    def run(self) -> Module:
        # Java calls the program made, declared as externals. Collected from
        # every function before any body is lowered, because a declaration has
        # to be in the module before a call can name it.
        for symbol, (params, ret) in sorted(self._java_externals().items()):
            fn = Function(symbol, ret, external=True, linkage=Linkage.IMPORT)
            for i, ty in enumerate(params):
                fn.params.append(i)
                fn.registers[i] = ty
            self.module.functions.append(fn)

        for name, (params, ret) in {**_RUNTIME, **_PLATFORM_IR,
                                    **_OBJECT_RUNTIME}.items():
            fn = Function(name, ret, external=True, linkage=Linkage.IMPORT)
            for i, ty in enumerate(params):
                fn.params.append(i)
                fn.registers[i] = ty
            self.module.functions.append(fn)

        for name in sorted(self.module_names):
            self.module.globals.append(
                Global(name=self.global_symbol(name), size=8))
        # One cell per default value. They are evaluated ONCE, in the entry,
        # not at each call -- `def f(xs=[])` shares a single list across every
        # call that omits the argument, and a program that appends to it sees
        # the growth. Evaluating at the call site would give a fresh list each
        # time, which reads as more sensible and is a different language.
        for info in self.infos.values():
            for i in range(len(info.defaults)):
                self.module.globals.append(
                    Global(name=self.default_symbol(info, i), size=8))

        for info in self.infos.values():
            self.module.functions.append(self._function(info))
        # AFTER the real functions, for the reason the thunks are: what goes
        # in it is discovered while lowering them.
        self._dyn_emit_positions()
        # AFTER the real functions: using a builtin as a value is what creates
        # the need for a thunk, and that is discovered while lowering them.
        self._dyn_emit_thunks()
        self._dyn_emit_annotate_thunks()
        self._dyn_emit_natives()

        # Drop runtime declarations nothing called. A module that declares an
        # import it never uses is not merely untidy: the link stage decides
        # whether to pull the runtime in by looking at what is declared, so a
        # program that only does arithmetic would still acquire a dependency
        # on stdio -- and "no runtime dependencies" would quietly stop being
        # true for every program.
        called = {ins.sym for fn in self.module.functions for b in fn.blocks
                  for ins in b.instructions if ins.op is Op.CALL}
        self.module.functions = [
            f for f in self.module.functions
            if not (f.external and (f.name in _RUNTIME or f.name in _PLATFORM_IR
                                    or f.name in _OBJECT_RUNTIME)
                    and f.name not in called)]
        return self.module

    # ── one function ────────────────────────────────────────────────────────
    def _function(self, info: FunctionInfo) -> Function:
        is_entry = (info.name == ENTRY_NAME
                    or (ENTRY_NAME not in self.infos and info.name == "main"))
        # A RUNTIME MODULE EXPORTS EVERYTHING. Its functions exist to be called
        # from a program compiled separately -- and for a machine backend that
        # links against the C object runtime, `apy_from_int` has to be a real
        # global symbol or the C's callers cannot reach it.
        fn = Function(self.symbols[info.name], TO_IR[info.ret],
                      linkage=Linkage.EXPORT if (is_entry or self.library)
                      else Linkage.INTERNAL)
        self.info, self.fn = info, fn
        self.b = Builder(fn)
        #: (continue-target, break-target) per enclosing loop. `continue` in a
        #: `for` must reach the increment, not the test, so the two targets
        #: differ and both are recorded rather than recomputed.
        self.loops: list[tuple[int, int]] = []
        #: `finally` bodies enclosing the statement being
        #: lowered. A `return` runs them before it returns.
        self.finallys: list = []
        #: The block an exception should jump to, innermost last. Empty means
        #: no `try` is open and an error ends the process.
        self.handlers: list[str] = []

        # THE CLOSURE ENVIRONMENT, first and before every declared parameter.
        # It is the function OBJECT the call came through, which is the only
        # thing that knows which boxes this particular closure captured: two
        # values made by two calls to one enclosing `def` share their code and
        # differ in their cells. Every dynamic function has one even when it
        # captures nothing, because `apy_call` reaches functions it cannot see
        # the definition of and a convention that varied per function would
        # have to be recorded in the value and checked on every call.
        if info.takes_env:
            self.env = fn.new_register(T.PTR)
            fn.params.append(self.env)
        else:
            self.env = None
        for sym in info.params:
            reg = fn.new_register(TO_IR[sym.type])
            fn.params.append(reg)
            sym.register = reg
        if info.vararg:
            # `*rest` takes a real argument slot -- the caller builds the tuple
            # -- but it is not in `params`, because the arity checks count
            # those and `*rest` accepts any number.
            sym = info.locals[info.vararg]
            reg = fn.new_register(T.PTR)
            fn.params.append(reg)
            sym.register = reg
        if info.kwarg:
            # `**kw` is LAST, after `*rest`, and its slot is always filled --
            # the caller passes an empty dict when the call named nothing, so
            # the body can read it without a test.
            sym = info.locals[info.kwarg]
            reg = fn.new_register(T.PTR)
            fn.params.append(reg)
            sym.register = reg

        self.b.switch_to(self.b.new_block("entry"))
        # Every local gets its register up front, so an assignment inside a
        # branch writes the same register the join reads. Allocating lazily is
        # what would need phis.
        #
        # A name the module owns is skipped: its storage is a global, so that
        # a function can reach it. Giving it a register too would make the
        # entry write one copy and every function read another.
        owned = self.module_names if is_entry else info.module_writes
        for name, sym in info.locals.items():
            if sym.register is None and name not in owned:
                sym.register = self.b.reg(TO_IR[sym.type])
        # A local the analysis could not prove starts as a NULL, which is the
        # runtime's "no value" and never a legitimate one -- so a read can
        # tell "not assigned yet" from "assigned None". Only those: a proved
        # local stays a plain register with no initialiser and no check.
        # `locals()` reads them ALL, including ones no ordinary read could
        # reach unassigned -- and an uninitialised register is not merely
        # unreadable, it is IR the verifier rejects. A parameter is excluded
        # because its register already holds the argument.
        starts_null = set(info.maybe_unbound)
        if info.reads_all_locals:
            starts_null |= {n for n, s in info.locals.items() if not s.is_param}
        for name in starts_null:
            sym = info.locals.get(name)
            if sym is not None and sym.register is not None \
                    and name not in owned:
                self.b.emit(Instruction(Op.COPY, TO_IR[sym.type],
                                        dst=sym.register,
                                        args=[self.b.const(TO_IR[sym.type],
                                                           0)]))

        # Defaults are evaluated where the `def` runs, which for every `def`
        # here is module level -- so the entry evaluates all of them, once,
        # before anything else. See `_dyn_init_defaults`.
        if is_entry:
            # THE CANONICAL BUILTIN TYPES, before any user statement. `type(x)`
            # answers the object the runtime has registered for that kind, and
            # `int` mentioned as a value registers it -- so `type(1) is int`
            # depended on which of the two the program reached FIRST, and left
            # to right it is `type(1)`. Building them here removes the order
            # rather than picking a winner, which is what an earlier attempt at
            # this got wrong.
            if self._wants_positions:
                # THE POSITION TABLE IS BUILT FIRST, before any statement can
                # fail. The function that fills it is emitted after every
                # other -- the rows are discovered while lowering them -- and
                # is reached by name, which the IR allows because a call names
                # a symbol rather than a definition.
                self.b.call(T.VOID, "pyf__positions", [])
            self._dyn_register_builtin_types()
            # Every module-level `def` becomes a value here, which is also
            # where its defaults are evaluated -- a module-level `def` is
            # lifted out of the entry's body by analysis, so there is no
            # statement of its own for either to happen at.
            self._dyn_init_module_defs()

        if info.is_generator:
            # A GENERATOR IS TWO FUNCTIONS. This one is the constructor: it
            # allocates the object, stores the arguments into its frame, and
            # returns having run none of the body. The step function is
            # emitted alongside and holds the body.
            self._dyn_generator(info, fn)
        elif info.dynamic:
            self._dyn_open_cells()
            self._dyn_stmts(info.node.body)
        else:
            self._stmts(info.node.body)

        if self.b.current.terminator is None:
            if info.dynamic and not is_entry:
                # Falling off the end of a Python function returns None, and
                # None is a VALUE. A zero pointer is not: the runtime reserves
                # it for "an error was set", so returning it here made every
                # function without an explicit `return` hand its caller a null
                # that the next operation reported as a failure -- with the
                # message of whatever had failed last, or none at all.
                self.b.ret(self.b.call(T.PTR, "apy_none", []))
            elif info.ret is NONE:
                self.b.ret()
            else:
                self.b.ret(self.b.const(TO_IR[info.ret], 0))
        return fn

    # ── statements ──────────────────────────────────────────────────────────
    def _stmts(self, body: list) -> None:
        """Lower a list of statements, stopping at the first terminator.

        Python allows dead code after `return`, `break` and `continue`, and a
        terminated block cannot take another instruction -- the builder
        refuses, correctly, because appending after a `ret` would produce IR
        the verifier rejects. Lowering on regardless raised
        `RuntimeError: block already ends in 'ret'` at the user, for a program
        CPython runs without complaint.

        Dropping the statements is what every compiler does with them, and it
        is what Python effectively does too.
        """
        for stmt in body:
            if self.b.current.terminator is not None:
                return
            self._stmt(stmt)

    def _stmt(self, node) -> None:
        self.b.span = self._span(node)
        match node:
            case ast.Expr(value=ast.Call(func=ast.Name(id="store"))) if (
                    "store" not in self.infos):
                # `store(...)` IN STATEMENT POSITION yields nothing, and going
                # through `_expr` would make it yield a constant nobody reads.
                # That matters here and nowhere else: a runtime written in this
                # subset is mostly stores.
                self._store_to_memory(node.value)
            case ast.Expr():
                if not isinstance(node.value, ast.Constant):
                    self._expr(node.value)
            case ast.Assign(targets=[ast.Name(id=name)]):
                self._store(name, node.value)
            case ast.AnnAssign(target=ast.Name(id=name)):
                if node.value is not None:
                    self._store(name, node.value)
            case ast.AugAssign(target=ast.Name(id=name)):
                synthetic = ast.BinOp(left=ast.Name(id=name, ctx=ast.Load()),
                                      op=node.op, right=node.value)
                ast.copy_location(synthetic, node)
                # The synthetic node was never analysed, so give it the types
                # analysis recorded for its parts.
                self.info.expr_types[id(synthetic.left)] = \
                    self.info.locals[name].type
                self.info.expr_types[id(synthetic)] = self.info.locals[name].type
                self._store(name, synthetic)
            case ast.Return():
                if node.value is None:
                    self.b.ret()
                else:
                    value = self._coerce(self._expr(node.value),
                                         self._type_of(node.value), self.info.ret)
                    self.b.ret(value)
            case ast.If():
                self._if(node)
            case ast.While():
                self._while(node)
            case ast.For(target=ast.Name(id=name)):
                self._for(node, name)
            case ast.Break():
                self.b.jump(self.loops[-1][1])
            case ast.Continue():
                self.b.jump(self.loops[-1][0])
            case ast.Pass():
                pass
            case _:
                # Analysis accepted it and lowering does not know it. Silently
                # dropping the statement is the one outcome to rule out: the
                # program compiles, runs, and quietly does less than it says.
                raise AssertionError(
                    f"lowering reached statement {type(node).__name__}; "
                    f"analysis accepted something lowering does not handle")

    def _store(self, name: str, value_node) -> None:
        sym = self.info.locals[name]
        value = self._coerce(self._expr(value_node),
                             self._type_of(value_node), sym.type)
        self.b.copy(sym.register, value)

    def _if(self, node: ast.If) -> None:
        cond = self._truth(node.test)
        then_b = self.b.new_block("then")
        else_b = self.b.new_block("else") if node.orelse else None
        join = self.b.new_block("endif")
        # `else_b or join` was wrong, and quietly. A Block defines __len__, so
        # a freshly created one is EMPTY and therefore FALSY -- the false edge
        # went to the join every time and the else branch was never reached.
        # Every `if`/`else` in the language compiled with its else body
        # unreachable, and no test noticed because every test program was
        # written in the early-return style with no else at all.
        self.b.branch(cond, then_b, join if else_b is None else else_b)

        self.b.switch_to(then_b)
        self._stmts(node.body)
        if self.b.current.terminator is None:
            self.b.jump(join)

        if else_b is not None:
            self.b.switch_to(else_b)
            self._stmts(node.orelse)
            if self.b.current.terminator is None:
                self.b.jump(join)

        self.b.switch_to(join)

    def _while(self, node: ast.While) -> None:
        head, body, done = (self.b.new_block("while"), self.b.new_block("do"),
                            self.b.new_block("endwhile"))
        self.b.jump(head)
        self.b.switch_to(head)
        self.b.branch(self._truth(node.test), body, done)
        self.b.switch_to(body)
        self.loops.append((head, done))
        self._stmts(node.body)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(head)
        self.b.switch_to(done)

    def _for(self, node: ast.For, name: str) -> None:
        call = node.iter
        args = [self._expr(a) for a in call.args]
        if len(args) == 1:
            start, stop, step = self.b.const(T.I64, 0), args[0], self.b.const(T.I64, 1)
        elif len(args) == 2:
            start, stop, step = args[0], args[1], self.b.const(T.I64, 1)
        else:
            start, stop, step = args

        var = self.info.locals[name].register
        self.b.copy(var, start)
        # Created in the order they are emitted, and the increment gets its own
        # block: `continue` has to reach the step, not the test, or the loop
        # never advances and hangs.
        head, body, step_b, done = (self.b.new_block("for"),
                                    self.b.new_block("forbody"),
                                    self.b.new_block("forstep"),
                                    self.b.new_block("endfor"))
        self.b.jump(head)
        self.b.switch_to(head)
        # `range` counts up or down depending on the sign of the step, and the
        # test differs. With a constant step the comparison is chosen here; a
        # runtime step would need both tests and a branch, which this subset
        # does not accept.
        # Analysis guarantees a literal step, so the sign -- and therefore the
        # loop test -- is known here.
        descending = (len(args) == 3
                      and (int_literal(node.iter.args[2]) or 0) < 0)
        self.b.branch(self.b.cmp(Op.GT if descending else Op.LT, T.I64,
                                 var, stop), body, done)
        self.b.switch_to(body)
        self.loops.append((step_b, done))
        self._stmts(node.body)
        self.loops.pop()
        if self.b.current.terminator is None:
            self.b.jump(step_b)
        self.b.switch_to(step_b)
        self.b.copy(var, self.b.add(T.I64, var, step))
        self.b.jump(head)
        self.b.switch_to(done)

    # ── expressions ─────────────────────────────────────────────────────────
    def _expr(self, node) -> int:
        self.b.span = self._span(node)
        match node:
            case ast.Constant(value=bool() as v):
                return self.b.const(T.I1, int(v))
            # A LITERAL IS MATERIALISED AT THE WIDTH ANALYSIS GAVE IT. `5` is
            # ordinarily `int`, but next to a machine type it adapts (see
            # `_adapt_literal`), and emitting it as i64 anyway produced a store
            # of an i64 into a u8 -- caught by the verifier, and reported
            # against the store rather than against the literal.
            case ast.Constant(value=int() as v):
                ty = self._type_of(node)
                return self.b.const(TO_IR[ty] if ty.is_machine else T.I64, v)
            case ast.Constant(value=float() as v):
                ty = self._type_of(node)
                return self.b.const(TO_IR[ty] if ty.is_machine else T.F64, v)
            case ast.Name(id=name):
                return self.info.locals[name].register
            case ast.UnaryOp(op=ast.Not()):
                cond = self._truth(node.operand)
                out = self.b.reg(T.I1)
                self.b.emit(Instruction(Op.XOR, T.I1, dst=out,
                                        args=[cond, self.b.const(T.I1, 1)]))
                return out
            case ast.UnaryOp(op=ast.USub()):
                ty = TO_IR[self._type_of(node.operand)]
                out = self.b.reg(ty)
                self.b.emit(Instruction(Op.NEG, ty, dst=out,
                                        args=[self._expr(node.operand)]))
                return out
            case ast.UnaryOp(op=ast.UAdd()):
                return self._expr(node.operand)
            case ast.UnaryOp(op=ast.Invert()):
                out = self.b.reg(T.I64)
                self.b.emit(Instruction(Op.NOT, T.I64, dst=out,
                                        args=[self._expr(node.operand)]))
                return out
            case ast.BinOp():
                return self._binop(node)
            case ast.BoolOp():
                return self._boolop(node)
            case ast.Compare():
                return self._compare(node)
            case ast.Call():
                return self._call(node)
            case ast.IfExp():
                return self._ifexp(node)
        raise AssertionError(f"lowering reached {type(node).__name__}; "
                             f"analysis should have rejected it")

    def _binop(self, node: ast.BinOp) -> int:
        result = self._type_of(node)
        operand = FLOAT if FLOAT in (self._type_of(node.left),
                                     self._type_of(node.right)) else result
        # `/` is float-valued in Python, so it forces both sides to float --
        # but a MACHINE width is already exactly the type asked for, and
        # analysis refused `/` on the integer ones. Widening an `f32` division
        # to `f64` here would quietly give the wrong result type back.
        if isinstance(node.op, ast.Div) and not operand.is_machine:
            operand = FLOAT
        ty = TO_IR[operand]
        a = self._coerce(self._expr(node.left), self._type_of(node.left), operand)
        b = self._coerce(self._expr(node.right), self._type_of(node.right), operand)

        if isinstance(node.op, ast.FloorDiv):
            return self._floor_div(a, b, ty)
        if isinstance(node.op, ast.Mod):
            return self._floor_mod(a, b, ty)
        if isinstance(node.op, ast.Pow):
            return self._pow(a, node.right.value, ty)
        op = {
            ast.Add: Op.ADD, ast.Sub: Op.SUB, ast.Mult: Op.MUL,
            ast.Div: Op.DIV, ast.BitAnd: Op.AND, ast.BitOr: Op.OR,
            ast.BitXor: Op.XOR, ast.LShift: Op.SHL, ast.RShift: Op.SHR,
        }[type(node.op)]
        out = self.b.reg(ty)
        self.b.emit(Instruction(op, ty, dst=out, args=[a, b]))
        return out

    def _pow(self, base: int, exponent: int, ty: T.Type) -> int:
        """`x ** n` for a non-negative literal n.

        INTEGERS expand by squaring: a compile-time expansion with no loop, no
        runtime call and no branch, and `x ** 8` is three multiplications
        rather than eight. Integer multiplication is exact, so the expansion
        is exactly what Python computes.

        FLOATS call libm's `pow`, because that is what CPython's `**` calls.
        Expanding them the same way rounds once per multiplication where pow
        rounds once in total, and `26.3747 ** 3` comes out one bit low --
        invisible until it is multiplied by something large, and then visible
        as a differing digit in printed output.

        The exponent being known is what makes `x ** 0` correct with no
        runtime test: it is the constant 1.
        """
        if ty.is_float:
            return self.b.call(ty, "py_pow_int",
                               [base, self.b.const(T.I64, exponent)])
        if exponent == 0:
            return self.b.const(ty, 1.0 if ty.is_float else 1)
        if exponent == 1:
            return base
        result: int | None = None
        square = base
        while exponent:
            if exponent & 1:
                if result is None:
                    result = square
                else:
                    out = self.b.reg(ty)
                    self.b.emit(Instruction(Op.MUL, ty, dst=out,
                                            args=[result, square]))
                    result = out
            exponent >>= 1
            if exponent:
                out = self.b.reg(ty)
                self.b.emit(Instruction(Op.MUL, ty, dst=out,
                                        args=[square, square]))
                square = out
        assert result is not None
        return result

    def _floor_div(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `//`. See the module docstring.

        DIV truncates toward zero; floor is one less exactly when the signs
        differ and the division is inexact.
        """
        if ty.is_float:
            return self._floor_div_float(a, b, ty)
        q = self.b.reg(ty)
        self.b.emit(Instruction(Op.DIV, ty, dst=q, args=[a, b]))
        out = self.b.reg(ty)
        self.b.copy(out, q)
        fix, join = self.b.new_block("floordiv"), self.b.new_block("endfloordiv")
        self.b.branch(self._needs_floor_fix(a, b, ty), fix, join)
        self.b.switch_to(fix)
        adjusted = self.b.reg(ty)
        self.b.emit(Instruction(Op.SUB, ty, dst=adjusted,
                                args=[q, self.b.const(ty, 1)]))
        self.b.copy(out, adjusted)
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _floor_div_float(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `//` on floats: the FLOOR of the quotient, not the quotient.

        `7.5 // 2.0` is 3.0 and `-7.5 // 2.0` is -4.0. Emitting a plain
        division here gave 3.75 and -3.75 -- an answer close enough to look
        right in a spot check.

        There is no floor opcode, and adding one would put a Python-shaped
        operation into a language-neutral IR and owe it to every backend. It
        is built instead from truncation, exactly as the integer case is built
        from truncating division:

            q = a / b
            if |q| < 2^52:            # otherwise q is already integral
                t = float(int(q))     # truncates toward zero
                if t > q: t -= 1      # only when q is negative and inexact
                q = t

        The magnitude guard is not defensive padding: `int(q)` overflows above
        2^63, and every float at or above 2^52 is already a whole number, so
        the guard is both necessary and exactly free.
        """
        q = self.b.reg(ty)
        self.b.emit(Instruction(Op.DIV, ty, dst=q, args=[a, b]))
        out = self.b.reg(ty)
        self.b.copy(out, q)

        limit = self.b.const(ty, float(1 << 52))
        neg_limit = self.b.const(ty, -float(1 << 52))
        small = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.AND, T.I1, dst=small, args=[
            self.b.cmp(Op.LT, ty, q, limit),
            self.b.cmp(Op.GT, ty, q, neg_limit)]))

        trunc_b = self.b.new_block("floorf")
        inexact_b = self.b.new_block("floorinexact")
        fix_b = self.b.new_block("floorfix")
        join = self.b.new_block("endfloorf")
        self.b.branch(small, trunc_b, join)

        self.b.switch_to(trunc_b)
        as_int = self.b.reg(T.I64)
        self.b.emit(Instruction(Op.FTOI, T.I64, dst=as_int, args=[q]))
        truncated = self.b.reg(ty)
        self.b.emit(Instruction(Op.ITOF, ty, dst=truncated, args=[as_int]))
        # An exact quotient IS the answer, and `out` already holds it. Taking
        # the truncated value instead would be numerically equal and lose the
        # sign of zero: `0.0 // -9.2` is -0.0 in Python, and round-tripping it
        # through an integer gives +0.0. They compare equal, print
        # differently, and divide to opposite infinities.
        self.b.branch(self.b.cmp(Op.EQ, ty, truncated, q), join, inexact_b)

        self.b.switch_to(inexact_b)
        self.b.copy(out, truncated)
        self.b.branch(self.b.cmp(Op.GT, ty, truncated, q), fix_b, join)

        self.b.switch_to(fix_b)
        lowered = self.b.reg(ty)
        self.b.emit(Instruction(Op.SUB, ty, dst=lowered,
                                args=[truncated, self.b.const(ty, 1.0)]))
        self.b.copy(out, lowered)
        self.b.jump(join)

        self.b.switch_to(join)
        return out

    def _floor_mod(self, a: int, b: int, ty: T.Type) -> int:
        """Python's `%` takes the sign of the DIVISOR; REM takes the dividend's."""
        r = self.b.reg(ty)
        self.b.emit(Instruction(Op.REM, ty, dst=r, args=[a, b]))
        # Floats need the SAME correction. `Op.REM` is C's fmod for them, which
        # takes the sign of the dividend, and Python takes the sign of the
        # divisor exactly as it does for ints: `-7.5 % 2.0` is 0.5, not -1.5.
        # Returning early here for floats was wrong on every negative dividend
        # and right on every test, because no test used one.
        out = self.b.reg(ty)
        self.b.copy(out, r)

        if not ty.is_float:
            fix = self.b.new_block("floormod")
            join = self.b.new_block("endfloormod")
            self.b.branch(self._signs_differ(r, b, ty), fix, join)
            self.b.switch_to(fix)
            adjusted = self.b.reg(ty)
            self.b.emit(Instruction(Op.ADD, ty, dst=adjusted, args=[r, b]))
            self.b.copy(out, adjusted)
            self.b.jump(join)
            self.b.switch_to(join)
            return out

        # A float remainder of zero still carries a sign, and Python gives it
        # the DIVISOR's: `0.0 % -6.2` is -0.0, where fmod returns +0.0. It
        # reads like a curiosity until the result is multiplied by something
        # negative, at which point the answer differs in a printed digit --
        # which is exactly how this was found.
        #
        # `0.0 * b` is copysign(0, b) for any finite non-zero b, and b cannot
        # be zero here: dividing by it already happened.
        zero_b = self.b.new_block("modzero")
        nonzero_b = self.b.new_block("modnonzero")
        fix = self.b.new_block("floormod")
        join = self.b.new_block("endfloormod")
        self.b.branch(self.b.cmp(Op.EQ, ty, r, self.b.const(ty, 0.0)),
                      zero_b, nonzero_b)

        self.b.switch_to(zero_b)
        signed_zero = self.b.reg(ty)
        self.b.emit(Instruction(Op.MUL, ty, dst=signed_zero,
                                args=[self.b.const(ty, 0.0), b]))
        self.b.copy(out, signed_zero)
        self.b.jump(join)

        self.b.switch_to(nonzero_b)
        self.b.branch(self._signs_differ_raw(r, b, ty), fix, join)

        self.b.switch_to(fix)
        adjusted = self.b.reg(ty)
        self.b.emit(Instruction(Op.ADD, ty, dst=adjusted, args=[r, b]))
        self.b.copy(out, adjusted)
        self.b.jump(join)

        self.b.switch_to(join)
        return out

    def _needs_floor_fix(self, a: int, b: int, ty: T.Type) -> int:
        rem = self.b.reg(ty)
        self.b.emit(Instruction(Op.REM, ty, dst=rem, args=[a, b]))
        inexact = self.b.cmp(Op.NE, ty, rem, self.b.const(ty, 0))
        differ = self._signs_differ_raw(a, b, ty)
        out = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.AND, T.I1, dst=out, args=[differ, inexact]))
        return out

    def _signs_differ(self, r: int, b: int, ty: T.Type) -> int:
        nonzero = self.b.cmp(Op.NE, ty, r, self.b.const(ty, 0))
        differ = self._signs_differ_raw(r, b, ty)
        out = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.AND, T.I1, dst=out, args=[differ, nonzero]))
        return out

    def _signs_differ_raw(self, x: int, y: int, ty: T.Type) -> int:
        zero = self.b.const(ty, 0)
        xn = self.b.cmp(Op.LT, ty, x, zero)
        yn = self.b.cmp(Op.LT, ty, y, zero)
        out = self.b.reg(T.I1)
        self.b.emit(Instruction(Op.XOR, T.I1, dst=out, args=[xn, yn]))
        return out

    def _boolop(self, node: ast.BoolOp) -> int:
        """`and`/`or` yield an operand and short-circuit."""
        result_ty = TO_IR[self._type_of(node)]
        out = self.b.reg(result_ty)
        first = self._coerce(self._expr(node.values[0]),
                             self._type_of(node.values[0]),
                             self._type_of(node))
        self.b.copy(out, first)
        done = self.b.new_block("endbool")
        for operand in node.values[1:]:
            cont = self.b.new_block("boolnext")
            cond = self._truth_of(out, self._type_of(node))
            if isinstance(node.op, ast.And):
                self.b.branch(cond, cont, done)
            else:
                self.b.branch(cond, done, cont)
            self.b.switch_to(cont)
            value = self._coerce(self._expr(operand), self._type_of(operand),
                                 self._type_of(node))
            self.b.copy(out, value)
            self.b.jump(done)
            self.b.switch_to(done)
            done = self.b.new_block("endbool") if operand is not node.values[-1] else done
        return out

    def _compare(self, node: ast.Compare) -> int:
        """Chained comparison, evaluating each operand once."""
        out = self.b.reg(T.I1)
        self.b.copy(out, self.b.const(T.I1, 0))
        join = self.b.new_block("endcmp")

        left_v, left_t = self._expr(node.left), self._type_of(node.left)
        for i, (op_node, right_node) in enumerate(zip(node.ops, node.comparators)):
            right_v, right_t = self._expr(right_node), self._type_of(right_node)
            # A MACHINE WIDTH COMPARES AT ITS OWN WIDTH. Falling through to
            # `INT` compared a `u64` as a signed i64, so every address above
            # 2^63 sorted below zero -- and the coercion that got it there was
            # a same-width truncation the verifier had no reason to object to.
            if left_t.is_machine or right_t.is_machine:
                operand = left_t if left_t.is_machine else right_t
            else:
                operand = FLOAT if FLOAT in (left_t, right_t) else INT
            ty = TO_IR[operand]
            res = self.b.cmp(_CMP_OPS[type(op_node)], ty,
                             self._coerce(left_v, left_t, operand),
                             self._coerce(right_v, right_t, operand))
            if i == len(node.ops) - 1:
                self.b.copy(out, res)
                self.b.jump(join)
            else:
                nxt = self.b.new_block("cmpnext")
                self.b.branch(res, nxt, join)
                self.b.switch_to(nxt)
            left_v, left_t = right_v, right_t
        self.b.switch_to(join)
        return out

    def _ifexp(self, node: ast.IfExp) -> int:
        ty = TO_IR[self._type_of(node)]
        out = self.b.reg(ty)
        cond = self._truth(node.test)
        then_b, else_b, join = (self.b.new_block("ifexp"),
                                self.b.new_block("elsexp"),
                                self.b.new_block("endifexp"))
        self.b.branch(cond, then_b, else_b)
        self.b.switch_to(then_b)
        self.b.copy(out, self._coerce(self._expr(node.body),
                                      self._type_of(node.body),
                                      self._type_of(node)))
        self.b.jump(join)
        self.b.switch_to(else_b)
        self.b.copy(out, self._coerce(self._expr(node.orelse),
                                      self._type_of(node.orelse),
                                      self._type_of(node)))
        self.b.jump(join)
        self.b.switch_to(join)
        return out

    def _call(self, node: ast.Call) -> int:
        java = self.info.java_calls.get(id(node))
        if java is not None:
            return self._java_call(node, java)
        name = node.func.id
        if name == "print":
            for i, arg in enumerate(node.args):
                if i:
                    self.b.call(T.I64, "putchar",
                                [self.b.const(T.I64, _SPACE)])
                ty = self._type_of(arg)
                if ty is NONE:
                    # `print(None)` has nothing to pass: None's IR type is
                    # void, so there is no value for a writer to take. The
                    # expression is still evaluated for its side effects -- a
                    # call returning None is the common shape -- but the
                    # literal `None` is not, because `_expr` has no case for a
                    # constant of a type that occupies no register.
                    if not isinstance(arg, ast.Constant):
                        self._expr(arg)
                    self.b.call(T.VOID, "put_none", [])
                    continue
                value = self._expr(arg)
                # THE WRITER FOLLOWS THE STORAGE CLASS, not the Python type:
                # a machine `f64` is as much a float as `float` is, and
                # choosing on `ty is FLOAT` alone sent it through `put_int`,
                # which does not print a wrong float -- it prints an integer,
                # because the coercion truncates first.
                if TO_IR[ty].is_float:
                    self.b.call(T.VOID, "put_float",
                                [self._coerce(value, ty, FLOAT)])
                elif ty is BOOL:
                    self.b.call(T.VOID, "put_bool",
                                [self._coerce(value, ty, INT)])
                else:
                    self.b.call(T.VOID, "put_int",
                                [self._coerce(value, ty, INT)])
            self.b.call(T.I64, "putchar", [self.b.const(T.I64, _NEWLINE)])
            return self.b.const(T.I64, 0)

        if name in _CONVERSIONS and name not in self.infos:
            arg = node.args[0]
            return self._coerce(self._expr(arg), self._type_of(arg),
                                _CONVERSIONS[name])

        if name in MACHINE and name not in self.infos:
            arg = node.args[0]
            return self._coerce(self._expr(arg), self._type_of(arg),
                                MACHINE[name])

        if name in MEMORY_INTRINSICS and name not in self.infos:
            return self._memory(name, node)

        # The platform floor and the object runtime: both are calls to symbols
        # declared elsewhere, and neither needs anything the other does not.
        declared = (PLATFORM.get(name) if name not in self.infos else None) \
            or (OBJECT_RUNTIME.get(name) if name not in self.infos else None)
        if declared is not None:
            params, ret = declared
            args = [self._coerce(self._expr(a), self._type_of(a), want)
                    for a, want in zip(node.args, params)]
            result = self.b.call(TO_IR[ret], name, args)
            return result if result is not None else self.b.const(T.I64, 0)

        info = self.infos[name]
        params, ret = info.signature
        args = [self._coerce(self._expr(a), self._type_of(a), want)
                for a, want in zip(node.args, params)]
        result = self.b.call(TO_IR[ret], self.symbols[name], args)
        return result if result is not None else self.b.const(T.I64, 0)

    # ── the machine subset: memory ──────────────────────────────────────────
    def _memory(self, name: str, node: ast.Call) -> int:
        """`alloca`, `load`, `store`, `offset`, `sizeof` -- one instruction each.

        Nothing here is a call, so nothing here adds a runtime dependency.
        That is the whole reason the systems subset can hold a runtime: code
        written in it needs no runtime to run.
        """
        if name == "sizeof":
            # A COMPILE-TIME CONSTANT. `offset(p, sizeof(i64))` has to fold to
            # a number, because a struct layout written in terms of it must
            # cost the same as writing the number and must be checkable by
            # reading the IR. It also ADAPTS like any other literal, so
            # `i * sizeof(i32)` for a machine-typed `i` needs no conversion.
            ty = self._type_of(node)
            size = TO_IR[self._type_arg(node.args[0])].size
            return self.b.const(TO_IR[ty] if ty.is_machine else T.I64, size)
        if name == "alloca":
            return self.b.alloca(const_int(node.args[0], self.infos))
        if name == "reserve":
            # ONE GLOBAL PER NAME, zeroed. Recorded here rather than declared
            # up front because analysis already checked that two reserves of a
            # name agree about the size, so the first one to be lowered decides
            # and the rest are the same region.
            symbol = node.args[0].value
            if symbol not in self.reserved:
                self.reserved.add(symbol)
                self.module.globals.append(Global(
                    name=symbol, size=const_int(node.args[1], self.infos)))
            out = self.b.reg(T.PTR)
            self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=out,
                                    sym=symbol))
            return out
        if name == "load":
            return self.b.load(TO_IR[self._type_arg(node.args[0])],
                               self._expr(node.args[1]))
        if name == "store":
            self._store_to_memory(node)
            # `store(...)` yields nothing, but `_expr` must hand back a
            # register because every caller indexes one -- `print` returns a
            # constant for the same reason. `_stmt` reaches `_store_to_memory`
            # directly so the ordinary case does not pay for it: a runtime
            # written in this subset is mostly stores, and one dead register
            # each is a large share of an unoptimised function.
            return self.b.const(T.I64, 0)
        # offset. The byte count is widened to the pointer's own width, which
        # is what `Op.OFFSET` takes -- a narrower index would be sign-extended
        # by the backend anyway, and doing it here means every backend sees
        # the same instruction.
        base = self._expr(node.args[0])
        count = self._coerce(self._expr(node.args[1]),
                             self._type_of(node.args[1]), INT)
        return self.b.offset(base, count)

    def _store_to_memory(self, node: ast.Call) -> None:
        """`store(T, value, addr)`, and nothing else.

        THE VALUE IS EVALUATED BEFORE THE ADDRESS, which is the order the
        source reads left to right -- and both may have side effects.
        """
        ty = self._type_arg(node.args[0])
        value = self._coerce(self._expr(node.args[1]),
                             self._type_of(node.args[1]), ty)
        self.b.store(TO_IR[ty], value, self._expr(node.args[2]))

    def _type_arg(self, node) -> SemType:
        """The type named by an intrinsic's first argument. Analysis already
        checked it is one of `MACHINE`, so this only has to read it."""
        return MACHINE[node.id]

    def _java_externals(self) -> dict:
        """Every Java operation the analyser resolved, as an IR signature.

        The types come from the overload the analyser chose, so the
        declaration and the call cannot disagree about arity or width -- and
        the backend checks both against the class path, which is a third
        opinion from the only source that actually knows.
        """
        out: dict[str, tuple] = {}
        wants_string = False
        for info in self.infos.values():
            for java in info.java_calls.values():
                params = [TO_IR[sem_type(p)] for p in java["params"]]
                if java["receiver"]:
                    params.insert(0, T.I64)
                out[java["symbol"]] = (params, TO_IR[sem_type(java["returns"])])
            wants_string = wants_string or bool(info.java_strings)
        if wants_string:
            out[JAVA_STRING] = ([T.PTR], T.I64)
        return out

    # ── calling Java ────────────────────────────────────────────────────────
    def _java_call(self, node: ast.Call, java: dict) -> int:
        """A constructor, a static method or an instance method.

        The analyser already chose the overload and the backend already named
        the symbol; this only has to evaluate the arguments and emit the call.
        The receiver, when there is one, is the first argument -- which is what
        the backend's `call` operations expect and what makes an instance
        method an ordinary call at the IR level.
        """
        args = []
        if java["receiver"]:
            args.append(self._expr(node.func.value))
        for arg, want in zip(node.args, java["params"]):
            args.append(self._java_arg(arg, want))
        ret = sem_type(java["returns"])
        result = self.b.call(TO_IR[ret], java["symbol"], args)
        return result if result is not None else self.b.const(T.I64, 0)

    def _java_arg(self, node, want: str) -> int:
        """One argument, coerced to what the Java method declared."""
        text = self.info.java_strings.get(id(node))
        if text is not None:
            return self._java_string(text)
        return self._coerce(self._expr(node), self._type_of(node),
                            sem_type(want))

    def _java_string(self, text: str) -> int:
        """A Java `String` from a literal.

        The bytes go into a read-only global and the runtime builds the String
        from them. Not a constant in the class file: the literal has to survive
        `--emit-ir` and a later rebuild, and an IR symbol cannot hold arbitrary
        text -- a global can hold anything.
        """
        symbol = self.java_strings.get(text)
        if symbol is None:
            symbol = f"jstr_{len(self.java_strings)}"
            self.java_strings[text] = symbol
            self.module.globals.append(Global(
                name=symbol, size=len(text.encode("utf-8")) + 1,
                data=text.encode("utf-8") + b"\x00", readonly=True))
        address = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=address,
                                sym=symbol))
        return self.b.call(T.I64, JAVA_STRING, [address])

    # ── helpers ─────────────────────────────────────────────────────────────
    def _truth(self, node) -> int:
        return self._truth_of(self._expr(node), self._type_of(node))

    def _truth_of(self, value: int, ty: SemType) -> int:
        if ty is BOOL:
            return value
        ir_ty = TO_IR[ty]
        return self.b.cmp(Op.NE, ir_ty, value, self.b.const(ir_ty, 0))

    def _coerce(self, value: int, have: SemType, want: SemType) -> int:
        if have == want:
            return value
        # `bool(x)` is `x != 0`, NOT a narrowing conversion. Truncating 2 to
        # one bit gives 0, so a numeric cast here would make `bool(2)` False
        # and `bool(2.5)` whatever the low bit of the truncation happened to
        # be -- wrong, and wrong in a way that only shows on even numbers.
        if want is BOOL:
            return self._truth_of(value, have)
        src, dst = TO_IR[have], TO_IR[want]
        if src == dst:
            return value
        # A POINTER IS NOT A NUMBER TO CONVERT, it is 64 bits to reinterpret --
        # and BITCAST is the only opcode that will take it, because `ptr` is
        # not in the allowed set of TRUNC, EXTEND, FTOI or ITOF. Analysis
        # already refused every ptr conversion that is not 64-bit-to-64-bit,
        # so one bitcast is the whole job.
        if src.is_ptr or dst.is_ptr:
            out = self.b.reg(dst)
            self.b.emit(Instruction(Op.BITCAST, dst, dst=out, args=[value]))
            return out
        out = self.b.reg(dst)
        if src.is_int and dst.is_int and src.bits == dst.bits:
            # SIGNEDNESS ONLY -- `u64` to `i64` and back. Nothing moves, and
            # saying so with BITCAST beats a TRUNC that happens to be a no-op
            # at this width and would not be at another.
            op = Op.BITCAST
        elif src.is_float and dst.is_float:
            op = Op.FTOF
        elif src.is_float:
            op = Op.FTOI
        elif dst.is_float:
            op = Op.ITOF
        elif dst.bits > src.bits:
            op = Op.EXTEND
        else:
            op = Op.TRUNC
        self.b.emit(Instruction(op, dst, dst=out, args=[value]))
        return out

    def _type_of(self, node) -> SemType:
        return self.info.expr_types.get(id(node), INT)

    def _span(self, node) -> Span:
        """Where this node came from.

        Uses the same `span_of` analysis uses, so a diagnostic and the
        instruction it describes point at exactly the same bytes. An earlier
        version of this returned the builder's sticky span for everything,
        which meant every instruction in a function claimed to come from
        whatever statement was lowered last -- invisible until a backend
        reported an error and pointed at the wrong line.
        """
        return span_of(self.source, node) if hasattr(node, "lineno") \
            else self.b.span
