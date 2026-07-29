"""Hoist `await` out of expression position into statement position.

A suspension point can only be *encoded* as a statement: the coroutine (and
generator) transform flattens a body into basic blocks and splits it at
statements, so a resume point has to be a statement boundary. But `await` is
an EXPRESSION -- `x = await f()` hides the suspension inside an assignment,
and `if await ready():` hides it inside a test.

This pass rewrites every statement containing awaits into A-normal form, so
each suspension becomes its own statement and the flattener can treat it
exactly as it treats `yield`::

    total = total + await fetch(u)

becomes::

    _aw1  = fetch(u)          # the awaitable
    _aw1r = <AwaitStmt _aw1>  # the suspension point
    total = total + _aw1r

Two properties matter and are easy to get wrong:

**Evaluation order.** Awaits are hoisted left-to-right in the order the
expression evaluates them, so side effects keep their relative order.

**Conditional evaluation.** An operand that CPython may not evaluate at all
must not be hoisted, because hoisting makes it unconditional. `a and await b`
only awaits when `a` is truthy; a comprehension body runs a variable number of
times. Those decline (return None) and are left for the caller to report,
rather than being silently rewritten into something that means something else.

The same machinery is what would let `x = yield v` work -- a yield used as an
expression, currently unsupported -- so this is one mechanism paying for two
features.
"""

from __future__ import annotations

from . import ast_nodes as A


#: Expression nodes whose operands are evaluated CONDITIONALLY or REPEATEDLY.
#: An await inside one cannot be hoisted out without changing how often it
#: runs, so a statement containing one is declined rather than rewritten.
_CONDITIONAL_NODES = (
    A.BoolOp,        # `a and await b` -- b only when a is truthy
    A.IfExp,         # `await a if c else await b` -- exactly one of the two
    A.Comprehension,
    A.DictComprehension,
    A.Lambda,        # body runs at call time, not here
)


def contains_await(node: object) -> bool:
    """True if `node` holds an `A.Await` anywhere inside it."""
    if isinstance(node, A.Await):
        return True
    for field in getattr(node, "__dataclass_fields__", ()):
        value = getattr(node, field, None)
        if isinstance(value, list):
            for item in value:
                if contains_await(item):
                    return True
        elif hasattr(value, "__dataclass_fields__"):
            if contains_await(value):
                return True
    return False


class AwaitHoister:
    """Rewrites expressions, collecting the awaits it lifts out.

    One instance per function so the temporary names it invents are unique
    within that function without needing a global counter.
    """

    def __init__(self, prefix: str = "_aw") -> None:
        self.prefix = prefix
        self.count = 0
        #: (result_name, awaitable_expr) in the order they must be awaited.
        self.pending: list = []
        #: Set when an await was found somewhere it cannot be hoisted from.
        self.declined = False

    def fresh(self) -> str:
        self.count += 1
        return f"{self.prefix}{self.count}"

    def rewrite(self, e):
        """Return `e` with each await replaced by a reference to its result,
        appending to `self.pending` in evaluation order. Sets `self.declined`
        if an await sits somewhere this pass will not hoist from; the caller
        must check it and leave the statement alone."""
        if e is None or not hasattr(e, "__dataclass_fields__"):
            return e

        if isinstance(e, _CONDITIONAL_NODES):
            if contains_await(e):
                self.declined = True
            return e

        if isinstance(e, A.Await):
            # Hoist the awaited expression first: `await f(await g())` must
            # await g before it can even build f's argument list.
            inner = self.rewrite(e.value)
            name = self.fresh()
            self.pending.append((name, inner))
            return A.Name(name=f"{name}r", pos=e.pos)

        # Rebuild with each field rewritten IN FIELD ORDER, which is
        # evaluation order for every expression node in this AST.
        kwargs = {}
        for field in e.__dataclass_fields__:
            value = getattr(e, field)
            if isinstance(value, list):
                out: list = []
                for item in value:
                    if hasattr(item, "__dataclass_fields__"):
                        out.append(self.rewrite(item))
                    else:
                        out.append(item)
                kwargs[field] = out
            elif hasattr(value, "__dataclass_fields__"):
                kwargs[field] = self.rewrite(value)
            else:
                kwargs[field] = value
        return type(e)(**kwargs)
