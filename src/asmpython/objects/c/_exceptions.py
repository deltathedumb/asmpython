"""The object runtime, in C: exceptions.

ONE PART OF ONE TRANSLATION UNIT. `c/__init__.py` concatenates
these in order and the result is the file it always was, so a
definition here may rely on anything in an earlier part and
nothing in a later one. Sections, in order:
  * exceptions
"""

C = r"""/* --- exceptions -------------------------------------------------------- */
/* An exception VALUE, so `except ValueError as e:` has something to bind and
   `type(e).__name__` and `str(e)` can answer. It carries the type's name and
   the argument, which is all the suite ever asks of one.

   The error FLAG and the error VALUE are separate on purpose: an operation
   deep in the runtime sets the flag with a static string and no allocation,
   and the value is built only when a handler actually catches -- so the
   common path, where nothing fails, allocates nothing. */
static const char *apy_exc_parent(const char *name);
/* THE EXPORTED HALF, which `runtime/errstate.py` replaces. It exists so the
   lookup can move while `apy_exc_parent`'s hundred-odd callers keep passing a
   `const char *`; the C body below is what the runtime uses when nothing is
   ported. */
APY_API apy_value apy_exc_parent_of(apy_value name);
/* A user exception class runs its own `__init__`, which needs the class
   registry and the call machinery -- both far below this. */
static apy_value apy_exc_construct(apy_value exc, apy_value *args, int64_t n);

APY_API apy_value apy_make_exc(apy_value type_name, apy_value arg) {
    apy_obj *o = apy_alloc(APY_EXC_K);
    /* NOT INHERITED FROM THE UNION: a fresh exception carries no
       sub-exceptions, and reading a stale pointer here would make
       every ordinary raise look like a group. */
    o->v.e.subs = 0;
    o->v.e.dict = 0;
    o->v.e.cls = 0;
    o->v.e.pos = -1;
    o->v.e.name = O(type_name)->v.s.p;
    o->v.e.arg = arg;
    o->v.e.has_arg = 1;
    o->v.e.argv = 0;
    return apy_exc_construct(V(o), &arg, 1);
}

/* `raise E` and `except E:` -- an exception with NO argument, as distinct from
   one whose argument is None. See the `e` layout for why that distinction has
   to be carried rather than inferred. */
APY_API apy_value apy_make_exc0(apy_value type_name) {
    apy_obj *o = apy_alloc(APY_EXC_K);
    /* NOT INHERITED FROM THE UNION: a fresh exception carries no
       sub-exceptions, and reading a stale pointer here would make
       every ordinary raise look like a group. */
    o->v.e.subs = 0;
    o->v.e.dict = 0;
    o->v.e.cls = 0;
    o->v.e.pos = -1;
    o->v.e.name = O(type_name)->v.s.p;
    o->v.e.arg = apy_none();
    o->v.e.has_arg = 0;
    o->v.e.argv = 0;
    return apy_exc_construct(V(o), NULL, 0);
}

/* THE exception being handled right now, for implicit chaining: a `raise`
   inside an `except` block records it as the new exception's `__context__`.
   One slot rather than a stack, so a raise from inside a handler nested in
   another handler chains to the inner one only -- which is what the chain
   `e.__context__.__context__` would otherwise show, and is the part this does
   not have. */
static apy_value apy_handling;

/* THE HANDLER BEING RUN, through an accessor, for the same reason the source
   position is: the storage moves to `runtime/errstate.py` and the C asks. */
APY_API apy_value apy_handling_now(void) { return apy_handling; }

APY_API apy_value apy_error_handling(apy_value exc) {
    apy_value was = apy_handling;
    apy_handling = O(exc)->kind == APY_EXC_K ? exc : 0;
    return was ? was : apy_none();
}

/* `e.add_note(text)` -- PEP 678. Appended to a list made on first use, so an
   exception that never gets one carries no allocation. */
APY_API apy_value apy_add_note(apy_value exc, apy_value text) {
    if (O(exc)->kind != APY_EXC_K)
        return apy_fail2("AttributeError",
                         "'%s' object has no attribute 'add_note'%s",
                         apy_kind_name(exc), "");
    if (O(text)->kind != APY_STR_K)
        return apy_fail2("TypeError",
                         "add_note() argument must be str, not %s%s",
                         apy_kind_name(text), "");
    if (!O(exc)->v.e.notes) O(exc)->v.e.notes = apy_list_new(2);
    apy_seq_push(O(exc)->v.e.notes, text);
    return apy_none();
}

APY_API apy_value apy_raise(apy_value exc);
APY_API apy_value apy_error_value(void);

/* `raise X from Y`. The cause is EXPLICIT, and `from None` suppresses the
   implicit context rather than setting a cause -- so the two are recorded
   separately and `has_cause` tells them apart. */
APY_API apy_value apy_raise_from(apy_value exc, apy_value cause,
                                 int64_t has_cause) {
    if (O(exc)->kind == APY_EXC_K) {
        O(exc)->v.e.suppress = 1;
        O(exc)->v.e.cause = has_cause && O(cause)->kind == APY_EXC_K ? cause : 0;
    }
    return apy_raise(exc);
}

/* `raise X(...)` and `raise X`. The message is the argument's str(), which is
   what CPython prints for an uncaught one and what `str(e)` returns. */
APY_API apy_value apy_raise(apy_value exc) {
    const char *name;
    apy_value shown;
    if (O(exc)->kind != APY_EXC_K)
        return apy_fail2("TypeError",
                         "exceptions must derive from BaseException, not '%s'%s",
                         apy_kind_name(exc), "");
    /* The exception being HANDLED becomes this one's `__context__`, unless a
       `raise ... from` already spoke for the chain. Set here rather than at
       the `except` because only a raise creates a link. */
    /* Set even when `from` suppressed it. `raise X from Y` records BOTH: the
       cause is what to print and `__suppress_context__` is whether to print
       the context, not whether to have one. Skipping it here made
       `e.__context__` None for every `raise ... from`, which is a different
       object graph from the one CPython builds. */
    if (!O(exc)->v.e.context && apy_handling_now()
            && apy_handling_now() != exc)
        O(exc)->v.e.context = apy_handling_now();
    /* A raise while an error is still PENDING -- `try: raise A finally: raise
       B` -- chains too, and nothing was "being handled" there: the A is
       in flight rather than caught. Taken before `apy_fail_replacing` clears
       the cell, which is the only moment it exists. */
    if (!O(exc)->v.e.context && apy_err_type) {
        apy_value pending = apy_error_value();
        if (pending && pending != exc && O(pending)->kind == APY_EXC_K)
            O(exc)->v.e.context = pending;
    }
    /* WHERE IT WAS RAISED. An exception carries no traceback until this
       runs, which is what makes `ValueError("x").__traceback__` None and the
       same object's traceback real once it has been raised. */
    O(exc)->v.e.pos = apy_pos_now();
    name = O(exc)->v.e.name;
    shown = O(exc)->v.e.arg;
    if (!O(exc)->v.e.has_arg) {
        apy_fail_replacing(name, "");
        apy_err_value = exc;
        return 0;
    }
    shown = apy_str(shown);
    {
        char buf[256];
        snprintf(buf, sizeof buf, "%.*s", (int)O(shown)->v.s.n, O(shown)->v.s.p);
        apy_fail_replacing(name, buf);
        /* AFTER `apy_fail_replacing`, which clears it: the text is what an
           uncaught error reports and what a handler matches on, and the object
           is what `except ... as e` binds. Both, not either. */
        apy_err_value = exc;
        return 0;
    }
}

/* The builtin exception hierarchy, as a parent chain. `except Exception:` has
   to catch a ValueError, and `except LookupError:` a KeyError, so a handler
   matching by name alone would be wrong for every non-leaf class -- which is
   most of the ones people actually write.
   NULL parent means the root. A name not in the table is treated as its own
   root: a user-defined exception class matches only itself, which is right
   until user classes exist and can declare a base. */
static const char *const APY_EXC_TREE[][2] = {
    {"Exception", "BaseException"},
    {"SystemExit", "BaseException"},
    {"KeyboardInterrupt", "BaseException"},
    {"GeneratorExit", "BaseException"},
    /* PEP 654. `BaseExceptionGroup` catches groups of BaseExceptions and
       `ExceptionGroup` is the narrower one every ordinary program means. */
    /* `asyncio.CancelledError` inherits BaseException and NOT Exception,
       since 3.8: `except Exception:` inside a task must not swallow the
       cancellation the loop just delivered. */
    {"CancelledError", "BaseException"},
    /* `InvalidStateError` is what a Future raises when asked for a result it
       does not have. */
    {"InvalidStateError", "Exception"},
    {"BaseExceptionGroup", "BaseException"},
    {"ExceptionGroup", "Exception"},
    {"ArithmeticError", "Exception"},
    {"ZeroDivisionError", "ArithmeticError"},
    {"OverflowError", "ArithmeticError"},
    {"FloatingPointError", "ArithmeticError"},
    {"LookupError", "Exception"},
    {"IndexError", "LookupError"},
    {"KeyError", "LookupError"},
    {"NameError", "Exception"},
    {"UnboundLocalError", "NameError"},
    {"AttributeError", "Exception"},
    {"TypeError", "Exception"},
    {"ValueError", "Exception"},
    {"UnicodeError", "ValueError"},
    {"UnicodeDecodeError", "UnicodeError"},
    {"UnicodeEncodeError", "UnicodeError"},
    {"UnicodeTranslateError", "UnicodeError"},
    {"RuntimeError", "Exception"},
    {"NotImplementedError", "RuntimeError"},
    {"RecursionError", "RuntimeError"},
    {"AssertionError", "Exception"},
    {"ImportError", "Exception"},
    {"ModuleNotFoundError", "ImportError"},
    {"OSError", "Exception"},
    /* PEP 3151 SUBSUMED THE OLD NAMES: `IOError` and `EnvironmentError` are
       `OSError` in CPython -- the same object, not subclasses -- and the
       errno-specific ones became real classes under it. `IOError is OSError`
       is the test a program writes, so the two aliases are resolved to the
       one name rather than added to the tree; see `apy_exc_canonical`. */
    {"FileNotFoundError", "OSError"},
    {"PermissionError", "OSError"},
    {"IsADirectoryError", "OSError"},
    {"NotADirectoryError", "OSError"},
    {"FileExistsError", "OSError"},
    {"InterruptedError", "OSError"},
    {"BlockingIOError", "OSError"},
    {"ChildProcessError", "OSError"},
    {"ProcessLookupError", "OSError"},
    {"ConnectionError", "OSError"},
    {"BrokenPipeError", "ConnectionError"},
    {"ConnectionAbortedError", "ConnectionError"},
    {"ConnectionRefusedError", "ConnectionError"},
    {"ConnectionResetError", "ConnectionError"},
    {"TimeoutError", "OSError"},
    {"StopIteration", "Exception"},
    /* The WARNING categories. They are exceptions in Python -- `Warning`
       inherits `Exception` -- and a program reads `issubclass(
       DeprecationWarning, Warning)` back, which is why they belong in the
       hierarchy rather than in whatever module raises them. */
    {"Warning", "Exception"},
    {"UserWarning", "Warning"},
    {"DeprecationWarning", "Warning"},
    {"PendingDeprecationWarning", "Warning"},
    {"SyntaxWarning", "Warning"},
    {"RuntimeWarning", "Warning"},
    {"FutureWarning", "Warning"},
    {"ImportWarning", "Warning"},
    {"UnicodeWarning", "Warning"},
    {"BytesWarning", "Warning"},
    {"ResourceWarning", "Warning"},
    {"EncodingWarning", "Warning"},
    {"StopAsyncIteration", "Exception"},
    {"MemoryError", "Exception"},
    {"EOFError", "Exception"},
    {"SyntaxError", "Exception"},
    {"IndentationError", "SyntaxError"},
    {"TabError", "IndentationError"},
};

/* A `class MyError(ValueError):` written by the PROGRAM, registered at the
   point its `class` statement runs. The built-in tree above is static; this
   grows, so the two are searched in turn.

   WHY A NAME AND NOT A TYPE OBJECT. An exception here carries a type NAME and
   one argument -- there is no class pointer in an APY_EXC_K cell, and adding
   one would mean `raise`, `except`, the parent walk and `apy_error_matches`
   all learning about two kinds of exception type. Registering the name into
   the same tree the builtins live in means `except ValueError:` catches a
   MyError through exactly the code path that already made `except
   LookupError:` catch a KeyError.

   WHAT THAT COSTS, stated rather than discovered: such a class is a NAME in a
   hierarchy and not an object. It has no methods and no attributes, so the
   frontend refuses one whose body is anything but `pass` or a docstring --
   silently dropping a method would be the bad half of this trade. */
#define APY_USER_EXC_MAX 64
/* REACHED THROUGH TWO FUNCTIONS, the shape `runtime/errstate.py` uses for
   the pending error and the source positions: both are ordinary exports with
   C bodies, so the runtime still stands alone, and IR replaces them so the
   table moves with them.

   THE COUNT IS AN int64_t NOW rather than an `int`. IR has one integer width
   for this and a four-byte cell read as eight is a number nobody wrote; the
   two `for` loops below compare it against an `int` index, which is fine in
   either direction. */
static const char *apy_user_exc_c[APY_USER_EXC_MAX][2];
static int64_t apy_user_exc_n_c;
APY_API apy_value apy_user_exc_rows(void) { return (apy_value)apy_user_exc_c; }
APY_API apy_value apy_user_exc_slot(void) {
    return (apy_value)&apy_user_exc_n_c;
}
#define apy_user_exc   ((const char *(*)[2])apy_user_exc_rows())
#define apy_user_exc_n (*(int64_t *)apy_user_exc_slot())

APY_API apy_value apy_exc_register(apy_value name, apy_value parent) {
    int i;
    const char *n = O(name)->v.s.p;
    for (i = 0; i < (int)apy_user_exc_n; i++)
        if (strcmp(apy_user_exc[i][0], n) == 0) return apy_none();
    if (apy_user_exc_n >= APY_USER_EXC_MAX)
        return apy_fail("RuntimeError",
                        "too many user-defined exception classes");
    apy_user_exc[apy_user_exc_n][0] = n;
    apy_user_exc[apy_user_exc_n][1] = O(parent)->v.s.p;
    apy_user_exc_n++;
    return apy_none();
}

/* THE NAME ITS CALLERS USE, kept as a delegate: the body moved to IR, where
   the tree is a packed `rodata` blob rather than a table of pointers. */
static const char *apy_exc_parent(const char *name) {
    return (const char *)(uintptr_t)apy_exc_parent_of((apy_value)(uintptr_t)name);
}
static const char *apy_exc_parent_c(const char *name) {
    size_t i;
    int u;
    for (i = 0; i < sizeof APY_EXC_TREE / sizeof APY_EXC_TREE[0]; i++)
        if (strcmp(APY_EXC_TREE[i][0], name) == 0) return APY_EXC_TREE[i][1];
    for (u = 0; u < (int)apy_user_exc_n; u++)
        if (strcmp(apy_user_exc[u][0], name) == 0) return apy_user_exc[u][1];
    return NULL;
}
APY_API apy_value apy_exc_parent_of(apy_value name) {
    return (apy_value)(uintptr_t)apy_exc_parent_c((const char *)name);
}

/* Does the CURRENT error match a handler named `handler`? Walks the chain up
   from the raised type, so a base class catches every derived one. */
APY_API int64_t apy_error_matches(apy_value handler) {
    const char *want = O(handler)->v.s.p;
    const char *have = apy_err_type;
    if (!have) return 0;
    while (have) {
        if (strcmp(have, want) == 0) return 1;
        have = apy_exc_parent(have);
    }
    return 0;
}

/* The current error as a value, for `except ... as e`. Built here rather than
   at the raise, so a program that never catches never allocates one. */
/* A LOCAL READ BEFORE IT WAS ASSIGNED.

   Null is the runtime's "no value" and is never a legitimate one, so a null
   here means the assignment has not run -- which is a different thing from
   having been assigned None, and CPython distinguishes them too. Emitted only
   for locals the frontend could not prove, so an ordinary read pays nothing. */
APY_API apy_value apy_check_bound(apy_value v, apy_value name) {
    if (v) return v;
    return apy_fail2("UnboundLocalError",
                     "cannot access local variable '%s' where it is not "
                     "associated with a value%s", APY_CSTR(name), "");
}

APY_API apy_value apy_error_value(void) {
    apy_obj *o;
    if (!apy_err_type) return apy_none();
    /* The object that was RAISED, when there was one. Rebuilding from the
       message text would replace the payload with its own repr -- `raise
       E(42)` caught as `E('42')`. */
    if (apy_err_value) return apy_err_value;
    o = apy_alloc(APY_EXC_K);
    o->v.e.dict = 0;
    o->v.e.cls = 0;
    /* THE FAILING STATEMENT, not the one running now: a handler's own
       statements have already moved the cursor by the time this is built. */
    o->v.e.pos = apy_pos_latched();
    /* NOT INHERITED FROM THE UNION: a fresh exception carries no
       sub-exceptions, and reading a stale pointer here would make
       every ordinary raise look like a group. */
    o->v.e.subs = 0;
    o->v.e.name = apy_err_type;
    o->v.e.rendered = 1;
    o->v.e.arg = apy_err_msg[0]
        ? apy_str_copy(apy_err_msg, (int64_t)strlen(apy_err_msg))
        : apy_none();
    o->v.e.has_arg = apy_err_msg[0] != 0;
    return V(o);
}

"""
