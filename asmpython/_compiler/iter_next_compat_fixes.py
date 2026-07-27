"""Model ``iter(x)`` and ``next(x)`` as real callable builtins.

asmpython compiles ``for`` loops natively (index-based) and dispatches
``for x in obj`` on a user class through the ``__iter__``/``__next__``
protocol (see ir_lower's ``_lower_for_iter_protocol``). But the BUILTINS
``iter()`` and ``next()`` -- used explicitly, e.g. by a bytecode
interpreter's ``GET_ITER``/``FOR_ITER`` (``it = iter(seq); v = next(it)``)
-- were never modeled: a bare ``iter``/``next`` name is in
ir_lower's ``_UNMODELED_BUILTIN_VALUES`` and a call to it fell through to
the graceful "unresolvable call" stub, silently returning 0/None. That
turns every explicit iterator use into a null iterator and a downstream
crash.

This module patches ``_lower_expr`` (the same monkeypatch pattern
``issubclass_compat_fixes`` uses) to lower ``iter(x)`` and ``next(x)`` with
a RUNTIME dispatch on ``x``'s actual type, exactly as CPython does:

  iter(x):
    - str            -> a fresh builtin-iterator cell over its chars
    - list / tuple   -> a fresh builtin-iterator cell over its elements
    - dict / set     -> a builtin-iterator over the KEYS (via _abi_dict_keys)
    - user instance  -> x.__iter__()   (runtime class-id dispatch)
    - anything else  -> original lowering (graceful stub)

  next(it):
    - a builtin-iterator cell -> advance it (raise StopIteration at the end)
    - user instance           -> it.__next__()   (runtime class-id dispatch)
    - anything else           -> original lowering

The builtin-iterator is an ordinary ``_abi_new_instance`` dict cell tagged
with a reserved ``__class__`` id (``_ITER_CLASS_ID``, far above any real
user class id) and carrying ``_seq`` / ``_idx`` / ``_len`` / ``_kind``
fields -- reusing only the existing dict ABI, so no new runtime/asm symbol
is introduced (keeps this change isolated to one Python module). StopIteration
is raised through the same ``_abi_raise`` primitive the ``__next__`` protocol
already uses, so an enclosing ``except StopIteration`` catches it normally.
"""

from __future__ import annotations

from . import ast_nodes as A
from . import ir_lower as IR


# Patch `_lower_expr_INNER`, not `_lower_expr`: the outer `_lower_expr` always
# delegates to `_lower_expr_inner`, but several store paths (notably
# `_lower_value_into_any_slot`, used when an `iter(x)` result flows into an
# "any"-typed variable) call `_lower_expr_inner` DIRECTLY, bypassing the outer
# wrapper. Patching the inner function catches `iter`/`next` on every path.
# (The outer `_lower_expr`'s auto-unbox choke is a no-op on our iterator cell:
# its tag is a large positive reserved id, outside the scalar-box tag range
# `_lower_unbox_any` unwraps, so the cell passes through unchanged.)
_ORIGINAL_LOWER_INNER = IR._lower_expr_inner

# A reserved runtime class id for the synthesized builtin-iterator cell.
# Real user class ids are assigned densely from 0 upward (see codegen's
# class_ids), and builtin-type ids are small negatives; this large positive
# sentinel collides with neither, so `next()` can recognize our own iterator
# by reading the same "__class__" tag every instance carries.
_ITER_CLASS_ID = 0x00E1_7E40  # 14777920

_KIND_LIST = 0  # list/tuple element walk (shared buffer layout)
_KIND_STR = 1   # str character walk


def _const(ctx, value):
    v = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("const", v, [value]))
    return v


def _str_key(ctx, name):
    """Address of an interned field-name string (dict key)."""
    sym = ctx.mctx.intern_str(name)
    v = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("global_addr", v, [sym]))
    return v


def _dict_set(ctx, cell, name, value):
    ctx.emit(IR.IRInstr("call", None, ["_abi_dict_set", cell, _str_key(ctx, name), value]))


def _dict_get(ctx, cell, name, default_v, ty=None):
    # The result tmp's type sets how the call result is consumed -- an
    # instance-field read that yields a pointer (e.g. a stored list/str) types
    # the tmp PTR, exactly as ir_lower's own A.Attr field read does.
    out = ctx.tmp(ty if ty is not None else IR.I64)
    ctx.emit(IR.IRInstr("call", out, ["_abi_dict_get_default", cell, _str_key(ctx, name), default_v]))
    return out


def _make_iter_cell(ctx, seq_v, len_v, kind):
    """Allocate and initialize a builtin-iterator cell over `seq_v`."""
    cell = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("call", cell, ["_abi_new_instance"]))
    _dict_set(ctx, cell, "__class__", _const(ctx, _ITER_CLASS_ID))
    _dict_set(ctx, cell, "_seq", seq_v)
    _dict_set(ctx, cell, "_idx", _const(ctx, 0))
    _dict_set(ctx, cell, "_len", len_v)
    _dict_set(ctx, cell, "_kind", _const(ctx, kind))
    return cell


def _list_len(ctx, seq_v):
    addr = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("gep", addr, [seq_v, IR._LIST_LEN_OFF]))
    out = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("load", out, [addr]))
    return out


def _instance_dispatch(ctx, recv_v, method, args, res_ty, tag_v, else_block):
    """Emit a runtime class-id dispatch chain calling `{owner}__method(recv,
    *args)` for every user class resolving `method`; branch to `else_block`
    if the receiver's class id (`tag_v`) matches no candidate. Returns
    (result_ptr, end_block) -- caller reads result_ptr in a block that both
    the hit paths and its own post-dispatch code reach. Mirrors ir_lower's
    own opaque-receiver MethodCall dispatch."""
    rows = IR._classes_resolving_method(ctx, method)
    res_ptr = ctx.ensure_slot(f"__iternext_res_{id(recv_v)}_{method}", res_ty)
    if not rows:
        ctx.emit(IR.IRInstr("br", None, [else_block.label]))
        return res_ptr, None
    checks = [ctx.new_block(f"itn_{method}_chk{i}") for i in range(len(rows))]
    hits = [ctx.new_block(f"itn_{method}_hit{i}") for i in range(len(rows))]
    end_b = ctx.new_block(f"itn_{method}_end")
    ctx.emit(IR.IRInstr("br", None, [checks[0].label]))
    for i, (cid, ow) in enumerate(rows):
        ctx.switch_to(checks[i])
        cid_v = _const(ctx, cid)
        m = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("icmp.eq", m, [tag_v, cid_v]))
        nxt = checks[i + 1].label if i + 1 < len(checks) else else_block.label
        ctx.emit(IR.IRInstr("br.t", None, [m, hits[i].label, nxt]))
        ctx.switch_to(hits[i])
        mv = ctx.tmp(res_ty)
        ctx.emit(IR.IRInstr("call", mv, [f"{ow}__{method}", recv_v, *args]))
        ctx.emit(IR.IRInstr("store", None, [mv, res_ptr]))
        ctx.emit(IR.IRInstr("br", None, [end_b.label]))
    return res_ptr, end_b


def _lower_iter_call(ctx, e):
    """iter(x): build a builtin-iterator cell (str/list/tuple/dict/set) or
    dispatch to x.__iter__() for a user instance.

    Branch on the ARGUMENT's STATIC type when it is known -- a statically-str
    argument (`iter("ab")`) is a RAW str pointer whose runtime tag reads
    UNTAGGED (raw strings aren't boxed), so it can't be recognized by tag; the
    static type names it precisely. Only a genuinely opaque ("any") argument
    needs the runtime tag dispatch (where a str is always a boxed cell with a
    legible str tag, and a raw list/dict is discriminated structurally)."""
    x = e.args[0]
    sty = A.expr_type(x)

    if sty == "str":
        sv = IR._lower_expr(ctx, x)
        slen = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("call", slen, ["strlen", sv]))
        return _make_iter_cell(ctx, sv, slen, _KIND_STR)
    if sty in ("list", "tuple"):
        lv = IR._lower_expr(ctx, x)
        return _make_iter_cell(ctx, lv, _list_len(ctx, lv), _KIND_LIST)
    if sty in ("dict", "set"):
        dv = IR._lower_expr(ctx, x)
        keys = ctx.tmp(IR.PTR)
        ctx.emit(IR.IRInstr("call", keys, ["_abi_dict_keys", dv]))
        return _make_iter_cell(ctx, keys, _list_len(ctx, keys), _KIND_LIST)
    if sty.startswith("instance:"):
        cls = sty.split(":", 1)[1]
        owner = IR._resolve_method_owner(ctx, cls, "__iter__") or cls
        obj = IR._lower_expr(ctx, x)
        r = ctx.tmp(IR.PTR)
        ctx.emit(IR.IRInstr("call", r, [f"{owner}____iter__", obj]))
        return r

    # opaque ("any"): runtime tag dispatch.
    return _lower_iter_call_runtime(ctx, e)


def _lower_iter_call_runtime(ctx, e):
    """iter(x) for an opaque ("any") x: dispatch on x's RUNTIME tag."""
    x = e.args[0]
    xv = IR._lower_expr_inner(ctx, x)  # raw, possibly-boxed value
    tag_v = IR._lower_read_any_tag(ctx, xv)
    raw_v = IR._lower_unbox_any(ctx, xv)  # str unboxes to its char pointer

    STR_TAG = IR.BUILTIN_TYPE_IDS["str"]
    UNTAGGED = IR.UNTAGGED_ID
    PTR_THRESHOLD = 0x10000

    res_ptr = ctx.ensure_slot(f"__iter_res_{id(e)}", IR.PTR)

    str_b = ctx.new_block(f"iter_str_{id(e)}")
    untag_b = ctx.new_block(f"iter_untag_{id(e)}")
    listlike_b = ctx.new_block(f"iter_list_{id(e)}")
    dictlike_b = ctx.new_block(f"iter_dict_{id(e)}")
    inst_b = ctx.new_block(f"iter_inst_{id(e)}")
    fallback_b = ctx.new_block(f"iter_fallback_{id(e)}")
    end_b = ctx.new_block(f"iter_end_{id(e)}")

    # tag == str ?
    is_str = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("icmp.eq", is_str, [tag_v, _const(ctx, STR_TAG)]))
    ctx.emit(IR.IRInstr("br.t", None, [is_str, str_b.label, untag_b.label]))

    # --- str: iterate characters ---
    ctx.switch_to(str_b)
    slen = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("call", slen, ["strlen", raw_v]))
    scell = _make_iter_cell(ctx, raw_v, slen, _KIND_STR)
    ctx.emit(IR.IRInstr("store", None, [scell, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    # tag == UNTAGGED -> a raw container (list/tuple/dict/set); else maybe instance
    ctx.switch_to(untag_b)
    is_untag = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("icmp.eq", is_untag, [tag_v, _const(ctx, UNTAGGED)]))
    ctx.emit(IR.IRInstr("br.t", None, [is_untag, listlike_b.label, inst_b.label]))

    # raw container: word-2 (offset 16) is a buffer pointer for a list/tuple,
    # a small tombstone count for a dict/set -- the same discriminator the
    # membership/index-assign `any` paths use.
    ctx.switch_to(listlike_b)
    w2addr = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("gep", w2addr, [raw_v, 16]))
    w2 = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("load", w2, [w2addr]))
    w2_is_ptr = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("icmp.gt", w2_is_ptr, [w2, _const(ctx, PTR_THRESHOLD)]))
    real_list_b = ctx.new_block(f"iter_reallist_{id(e)}")
    ctx.emit(IR.IRInstr("br.t", None, [w2_is_ptr, real_list_b.label, dictlike_b.label]))

    ctx.switch_to(real_list_b)
    llen = _list_len(ctx, raw_v)
    lcell = _make_iter_cell(ctx, raw_v, llen, _KIND_LIST)
    ctx.emit(IR.IRInstr("store", None, [lcell, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    # dict/set: iterate keys (a plain list from _abi_dict_keys)
    ctx.switch_to(dictlike_b)
    keys_v = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("call", keys_v, ["_abi_dict_keys", raw_v]))
    klen = _list_len(ctx, keys_v)
    kcell = _make_iter_cell(ctx, keys_v, klen, _KIND_LIST)
    ctx.emit(IR.IRInstr("store", None, [kcell, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    # user instance: x.__iter__()
    ctx.switch_to(inst_b)
    inst_res_ptr, inst_end = _instance_dispatch(
        ctx, raw_v, "__iter__", [], IR.PTR, tag_v, fallback_b
    )
    if inst_end is not None:
        ctx.switch_to(inst_end)
        iv = ctx.tmp(IR.PTR)
        ctx.emit(IR.IRInstr("load", iv, [inst_res_ptr]))
        ctx.emit(IR.IRInstr("store", None, [iv, res_ptr]))
        ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    # fallback: not an iterable this backend models -- original lowering
    # (evaluates the arg for side effects, yields the graceful stub). Re-lower
    # via the original so no assumption leaks in.
    ctx.switch_to(fallback_b)
    fb = _ORIGINAL_LOWER_INNER(ctx, e)
    ctx.emit(IR.IRInstr("store", None, [fb, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("load", out, [res_ptr]))
    return out


def _lower_next_call(ctx, e):
    """next(it): advance a builtin-iterator cell (raising StopIteration at the
    end), or dispatch to it.__next__() for a user instance."""
    it = e.args[0]
    itv = IR._lower_expr_inner(ctx, it)
    tag_v = IR._lower_read_any_tag(ctx, itv)

    res_ptr = ctx.ensure_slot(f"__next_res_{id(e)}", IR.PTR)

    builtin_b = ctx.new_block(f"next_builtin_{id(e)}")
    inst_b = ctx.new_block(f"next_inst_{id(e)}")
    fallback_b = ctx.new_block(f"next_fallback_{id(e)}")
    end_b = ctx.new_block(f"next_end_{id(e)}")

    is_builtin = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("icmp.eq", is_builtin, [tag_v, _const(ctx, _ITER_CLASS_ID)]))
    ctx.emit(IR.IRInstr("br.t", None, [is_builtin, builtin_b.label, inst_b.label]))

    # --- builtin iterator: read _idx/_len; raise StopIteration at end ---
    ctx.switch_to(builtin_b)
    miss = _const(ctx, 0)
    idx_v = _dict_get(ctx, itv, "_idx", miss)
    len_v = _dict_get(ctx, itv, "_len", miss)
    at_end = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("icmp.ge", at_end, [idx_v, len_v]))
    stop_b = ctx.new_block(f"next_stop_{id(e)}")
    live_b = ctx.new_block(f"next_live_{id(e)}")
    ctx.emit(IR.IRInstr("br.t", None, [at_end, stop_b.label, live_b.label]))

    ctx.switch_to(stop_b)
    if len(e.args) >= 2:
        # `next(it, default)` -- exhaustion yields the DEFAULT instead of
        # raising. Route it through the any-slot store choke so a str/int/float
        # default round-trips as a tagged value, the same as any other value
        # reaching an "any"-typed result (this call's own type). Without this
        # the second argument was ignored entirely and exhaustion produced 0,
        # so `next(it, 'done')` printed 0.
        dflt_v = IR._lower_value_into_any_slot(ctx, e.args[1])
        ctx.emit(IR.IRInstr("store", None, [dflt_v, res_ptr]))
        ctx.emit(IR.IRInstr("br", None, [end_b.label]))
    else:
        # end of sequence -> raise StopIteration (propagates to an enclosing
        # `except StopIteration`, exactly as the __next__ protocol's own end
        # does)
        empty_sym = ctx.mctx.intern_str("")
        msg_v = ctx.tmp(IR.PTR)
        ctx.emit(IR.IRInstr("global_addr", msg_v, [empty_sym]))
        ctx.emit(IR.IRInstr("call", None, ["_abi_raise", msg_v, _const(ctx, IR.BUILTIN_EXC_IDS["StopIteration"])]))
        # unreachable, but keep the block terminated / res_ptr defined
        ctx.emit(IR.IRInstr("store", None, [_const(ctx, 0), res_ptr]))
        ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    ctx.switch_to(live_b)
    seq_v = _dict_get(ctx, itv, "_seq", miss, ty=IR.PTR)
    kind_v = _dict_get(ctx, itv, "_kind", miss)
    # `seq_v` came back as I64 from _abi_dict_get_default; the list/str helpers
    # take a GP-sized address operand, and load/gep treat an I64 holding an
    # address transparently -- matching how dict_get_default values are used
    # elsewhere in ir_lower -- so it's used directly as the sequence pointer.
    is_str = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("icmp.eq", is_str, [kind_v, _const(ctx, _KIND_STR)]))
    str_elem_b = ctx.new_block(f"next_strelem_{id(e)}")
    list_elem_b = ctx.new_block(f"next_listelem_{id(e)}")
    advance_b = ctx.new_block(f"next_advance_{id(e)}")
    ctx.emit(IR.IRInstr("br.t", None, [is_str, str_elem_b.label, list_elem_b.label]))

    ctx.switch_to(str_elem_b)
    ch = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("call", ch, ["_abi_str_char_at", seq_v, idx_v]))
    # A str char is a fresh raw 1-char string; box it as str so the "any"-typed
    # next() result carries a legible str tag (a raw str reads UNTAGGED and
    # would be mis-formatted as an int / mis-boxed downstream). A list element
    # is stored as-is: a list[object] already holds boxed elements, and a
    # list[int] element flows on as a raw int exactly as any other int does.
    ch_boxed = IR._lower_box_any(ctx, ch, "str", None)
    ctx.emit(IR.IRInstr("store", None, [ch_boxed, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [advance_b.label]))

    ctx.switch_to(list_elem_b)
    elem_addr = IR._list_elem_addr(ctx, seq_v, idx_v)
    el = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("load", el, [elem_addr]))
    ctx.emit(IR.IRInstr("store", None, [el, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [advance_b.label]))

    ctx.switch_to(advance_b)
    one = _const(ctx, 1)
    nidx = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("iadd", nidx, [idx_v, one]))
    _dict_set(ctx, itv, "_idx", nidx)
    ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    # --- user instance: it.__next__() ---
    ctx.switch_to(inst_b)
    inst_res_ptr, inst_end = _instance_dispatch(
        ctx, itv, "__next__", [], IR.PTR, tag_v, fallback_b
    )
    if inst_end is not None:
        ctx.switch_to(inst_end)
        nv = ctx.tmp(IR.PTR)
        ctx.emit(IR.IRInstr("load", nv, [inst_res_ptr]))
        ctx.emit(IR.IRInstr("store", None, [nv, res_ptr]))
        ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    ctx.switch_to(fallback_b)
    fb = _ORIGINAL_LOWER_INNER(ctx, e)
    ctx.emit(IR.IRInstr("store", None, [fb, res_ptr]))
    ctx.emit(IR.IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(IR.PTR)
    ctx.emit(IR.IRInstr("load", out, [res_ptr]))
    return out


def _lower_inner_with_iter_next(ctx, e):
    if isinstance(e, A.Call) and getattr(e, "dstar", None) is None and not getattr(e, "kwargs", None):
        if e.func == "iter" and len(e.args) == 1:
            return _lower_iter_call(ctx, e)
        if e.func == "next" and len(e.args) in (1, 2):
            # 1 or 2 args: `next(it, default)` returns the default on
            # exhaustion instead of raising (see `_lower_next_call`). The
            # two-argument form used to fall through to the generic builtin
            # call, which produced 0 and never advanced the iterator at all.
            return _lower_next_call(ctx, e)
    return _ORIGINAL_LOWER_INNER(ctx, e)


# -- reachability: keep every __iter__/__next__ emitted when iter()/next() are
# used ------------------------------------------------------------------------
#
# The iter()/next() lowering above dispatches to a user instance's
# `{Class}____iter__` / `{Class}____next__` on a runtime class-id match, but the
# reachability walker (`_reachable_callables`) only marks a method reachable
# from AST patterns it recognizes (a `for x in instance`, a `str(instance)`,
# ...). It has no `iter(x)`/`next(x)` case, so those dispatch targets were never
# emitted -> undefined symbol at link. Mirror the walker's own "lowering fixed,
# walker not fixed" fix pattern: when the module calls iter()/next() anywhere,
# force every class's __iter__/__next__ into the emitted method set.

_ORIGINAL_REACHABLE = IR._reachable_callables


def _calls_iter_or_next(node) -> bool:
    if isinstance(node, A.Call) and node.func in ("iter", "next"):
        return True
    for f in getattr(node, "__dataclass_fields__", ()):
        v = getattr(node, f, None)
        if isinstance(v, list):
            for it in v:
                if hasattr(it, "__dataclass_fields__") and _calls_iter_or_next(it):
                    return True
        elif hasattr(v, "__dataclass_fields__") and _calls_iter_or_next(v):
            return True
    return False


def _module_uses_iter_next(mod) -> bool:
    for f in mod.funcs:
        for st in f.body:
            if _calls_iter_or_next(st):
                return True
    for cls in mod.classes:
        for m in cls.methods:
            for st in m.body:
                if _calls_iter_or_next(st):
                    return True
    return False


def _mangle_iter_method(cls, m):
    """Build the mangled `{Class}__{method}` FuncDef exactly as
    `_reachable_callables` does for a reachable method (receiver param widened
    to "any"), so an appended __iter__/__next__ lowers identically to one the
    walker itself emitted."""
    param_types = list(m.param_types)
    if param_types and "staticmethod" not in m.decorators:
        param_types[0] = ("any", None, None, [], None)
    return A.FuncDef(
        name=f"{cls.name}__{m.name}",
        params=list(m.params),
        body=list(m.body),
        pos=m.pos,
        defaults=list(m.defaults),
        param_types=param_types,
        ret_type=m.ret_type,
        vararg=m.vararg,
        kwarg=m.kwarg,
        asm_body=m.asm_body,
        asm_symbol=f"{cls.name}__{m.name}" if m.asm_body is not None else None,
        access_policy=m.access_policy,
        abi_name=m.abi_name,
        is_public_export=cls.is_public_export or m.is_public_export,
        decorators=list(m.decorators),
        method_owner_class=cls.name,
    )


def _reachable_with_iter_next(mod):
    out_funcs, out_methods = _ORIGINAL_REACHABLE(mod)
    if not _module_uses_iter_next(mod):
        return out_funcs, out_methods
    have = {f.name for f in out_methods}
    for cls in mod.classes:
        for m in cls.methods:
            if m.name in ("__iter__", "__next__"):
                mangled = f"{cls.name}__{m.name}"
                if mangled not in have:
                    out_methods.append(_mangle_iter_method(cls, m))
                    have.add(mangled)
    return out_funcs, out_methods


# -- sema: type iter()/next() results as "any" ------------------------------
#
# sema types an unmodeled call (`iter(x)`/`next(x)`) as the "int" default. That
# is wrong for these: a builtin iterator yields values of any kind (a str's
# chars, a list[object]'s boxed elements). Left as "int", `frame.stack.append(
# next(it))` boxes the result AS an int -- double-boxing an already-boxed
# object element and mis-tagging a str char. A post-pass stamps every
# iter()/next() call's `inferred_type` to "any" (read by `A.expr_type`), so the
# store choke forwards the value still-boxed and the value-to-string path
# formats it by its real runtime kind. Runs AFTER the original analysis so it
# overrides the default without disturbing anything sema computed.

from . import sema as _SEMA

_ORIGINAL_ANALYZE = _SEMA.SemaAnalyzer.analyze


def _stamp_iter_next_any(node):
    if isinstance(node, A.Call) and node.func in ("iter", "next") and len(node.args) == 1:
        node.inferred_type = "any"
    for f in getattr(node, "__dataclass_fields__", ()):
        v = getattr(node, f, None)
        if isinstance(v, list):
            for it in v:
                if hasattr(it, "__dataclass_fields__"):
                    _stamp_iter_next_any(it)
        elif hasattr(v, "__dataclass_fields__"):
            _stamp_iter_next_any(v)


def _analyze_with_iter_next(self):
    _ORIGINAL_ANALYZE(self)
    for f in self.mod.funcs:
        for st in f.body:
            _stamp_iter_next_any(st)
    for cls in self.mod.classes:
        for m in cls.methods:
            for st in m.body:
                _stamp_iter_next_any(st)
    for st in self.mod.body:
        _stamp_iter_next_any(st)


if not getattr(IR, "_asmpython_iter_next_patch", False):
    IR._lower_expr_inner = _lower_inner_with_iter_next
    IR._reachable_callables = _reachable_with_iter_next
    _SEMA.SemaAnalyzer.analyze = _analyze_with_iter_next
    IR._asmpython_iter_next_patch = True
