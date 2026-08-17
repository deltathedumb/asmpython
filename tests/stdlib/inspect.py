# COVERAGE: signature over every combination of parameter kind, str() and
# repr() of Signature and Parameter, the parameters mapping and its order,
# defaults positional and keyword-only, annotations and the return annotation,
# _empty, and isfunction / isclass / getdoc. NOT covered here: getsource,
# getmembers, the frame helpers, signature of a builtin or a class or a bound
# method, bind/BoundArguments, getfullargspec, or replace() -- the module
# declares it has none of them.
#
# `repr(parameter.kind)` IS NOT COMPARED: CPython's kinds are an enum.IntEnum
# and this module's are a small class of its own, so the repr differs and the
# module says so. `.name`, `.value` and comparison are what code uses, and
# those are compared.
#
# THE DEFAULTS ARE THE POINT. This is the case that found the original bug:
# `co_varnames` left `*args` and `**kw` out entirely and the defaults were
# split on the count of KEYWORD-ONLY parameters, so `def f(a, b=1, *args, c)`
# reported `b`'s default as `c`'s. Every shape below has a default in a
# different place for that reason.
import inspect


def plain(a, b, c):
    pass


def defaulted(a, b=1, c="x"):
    pass


def starred(a, b=1, *args, c, d=2, **kw):
    pass


def positional_only(a, b, /, c, *, d):
    pass


def annotated(a: int, b: str = "x") -> bool:
    pass


def variadic_only(*args, **kw):
    pass


for fn in (plain, defaulted, starred, positional_only, annotated,
           variadic_only):
    sig = inspect.signature(fn)
    print(fn.__name__, str(sig))
    print("   ", repr(sig))
    print("   ", list(sig.parameters))


# The shape the original defect got wrong, read out one field at a time.
sig = inspect.signature(starred)
for name in sig.parameters:
    one = sig.parameters[name]
    print(name, one.kind.name, one.kind.value,
          "<empty>" if one.default is inspect.Parameter.empty
          else repr(one.default))
print(repr(sig.parameters["b"]), repr(sig.parameters["args"]),
      repr(sig.parameters["kw"]))

# THE UNDERLYING FACTS the signature is recovered from, so that a difference
# lands on the code object rather than on the reader of it.
print(starred.__defaults__, starred.__kwdefaults__)
code = starred.__code__
print(code.co_varnames, code.co_argcount, code.co_kwonlyargcount)
print(positional_only.__code__.co_posonlyargcount)

# Annotations, including the return.
sig = inspect.signature(annotated)
print(sig.parameters["a"].annotation, sig.parameters["b"].annotation)
print(sig.return_annotation)
print(inspect.signature(plain).return_annotation is inspect.Signature.empty)
print(inspect.Signature.empty is inspect.Parameter.empty)

# The five kinds are distinct and ordered as the parameters must be.
kinds = (inspect.Parameter.POSITIONAL_ONLY,
         inspect.Parameter.POSITIONAL_OR_KEYWORD,
         inspect.Parameter.VAR_POSITIONAL,
         inspect.Parameter.KEYWORD_ONLY,
         inspect.Parameter.VAR_KEYWORD)
print([k.name for k in kinds])
print([k.value for k in kinds])
print(kinds[0] == inspect.Parameter.POSITIONAL_ONLY,
      kinds[0] == inspect.Parameter.KEYWORD_ONLY)


# ---- the predicates --------------------------------------------------------
class Thing:
    """A docstring."""

    def method(self):
        pass


print(inspect.isfunction(plain), inspect.isfunction(Thing))
print(inspect.isclass(Thing), inspect.isclass(plain))
# `getdoc` OF A CLASS IS NOT COMPARED: a class carries no `__doc__` through
# this compiler, so it answers None where CPython answers the text. The module
# states it; the function case is what is measured.
print(inspect.getdoc(plain))


def documented():
    """What it does."""


print(inspect.getdoc(documented))

print("done")
