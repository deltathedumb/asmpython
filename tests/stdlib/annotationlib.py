# COVERAGE: Format (VALUE, FORWARDREF, STRING, with CPython's own int values);
# get_annotations(obj, format=Format.VALUE) on a function with annotated
# parameters and a return, on a class with annotated fields (OWN annotations
# only, not inherited), on a class/function with none, and on a plain object
# with neither -- confirming the VALUE-format results are the real objects
# (int, str, a user class) rather than strings; eval_str un-stringizing a
# quoted BARE-IDENTIFIER annotation ("int"/"str", not "list[int]"); ForwardRef
# -- construction, equality, repr, and .evaluate() in all three formats for a
# BARE IDENTIFIER, including the NameError and the FORWARDREF-format fallback
# to `self`; call_annotate_function and call_evaluate_function for
# format=VALUE; get_annotate_from_class_namespace.
#
# NOT TESTED HERE: format=FORWARDREF and format=STRING in get_annotations and
# call_annotate_function (uasm refuses both BY NAME -- its __annotate__
# thunk evaluates every annotation as real code with no source text or
# per-entry failure handling kept alongside it, so there is nothing to build
# either format from; see bundled/annotationlib.py); and, in ForwardRef.evaluate
# and eval_str, resolving anything OTHER than a bare identifier ("list[int]",
# "int | None") -- both need eval(), and eval() cannot be called from inside a
# bundled module here, a compiler bug bundled/annotationlib.py documents in
# full. CPython implements every one of these fully, so asserting the refusal
# here would only ever diverge from the oracle; it belongs in a test measured
# against uasm alone, the way tests/uasm/integration/test_stdlib.py
# already does for `re`'s and `pathlib`'s refusals.
import annotationlib
from annotationlib import Format, ForwardRef


# ---- a function's parameters and return -----------------------------------
def f(x: int, y: str = "a") -> bool:
    return True


ann = annotationlib.get_annotations(f)
print(sorted(ann.items(), key=lambda kv: kv[0]))
# THE VALUES ARE REAL OBJECTS, not strings: identity with the classes
# themselves, not a name comparison against "int"/"str"/"bool".
print(ann["x"] is int, ann["y"] is str, ann["return"] is bool)
print(isinstance(ann["x"], str), isinstance(ann["return"], str))


def plain():
    return 1


print(annotationlib.get_annotations(plain))


# ---- a class's fields, and only its OWN ones -------------------------------
class Point:
    x: int
    y: int


ann2 = annotationlib.get_annotations(Point)
print(sorted(ann2.items(), key=lambda kv: kv[0]))
print(ann2["x"] is int, ann2["y"] is int)


class Empty:
    pass


print(annotationlib.get_annotations(Empty))


class Custom:
    pass


class Holder:
    value: Custom
    name: str


ann3 = annotationlib.get_annotations(Holder)
print(ann3["value"] is Custom, ann3["name"] is str)


class Base:
    a: int


class Sub(Base):
    b: str


# INHERITANCE IS NOT FOLLOWED: a subclass that adds no annotation of its own
# name gets none of its base's back.
print(annotationlib.get_annotations(Sub))
print(annotationlib.get_annotations(Base))


# ---- an object with neither __annotations__ nor __annotate__ --------------
try:
    annotationlib.get_annotations(42)
except TypeError as exc:
    print("TypeError:", exc)


# ---- eval_str un-stringizes a quoted annotation ----------------------------
def g(x: "int") -> "str":
    return "z"


unevaluated = annotationlib.get_annotations(g)
print(sorted(unevaluated.items(), key=lambda kv: kv[0]))
print(isinstance(unevaluated["x"], str), isinstance(unevaluated["return"], str))

evaluated = annotationlib.get_annotations(g, eval_str=True)
print(evaluated["x"] is int, evaluated["return"] is str)


# ---- Format --------------------------------------------------------------
print(Format.VALUE == 1, Format.FORWARDREF == 3, Format.STRING == 4)
print(Format.VALUE == Format.VALUE, Format.VALUE == Format.STRING)


# ---- ForwardRef -------------------------------------------------------------
fr = ForwardRef("int")
print(fr.evaluate() is int)
print(fr.evaluate(format=Format.STRING))
print(fr.evaluate(format=Format.FORWARDREF) is int)

fr_bad = ForwardRef("NoSuchName")
try:
    fr_bad.evaluate()
except NameError as exc:
    print("NameError:", exc)
print(fr_bad.evaluate(format=Format.FORWARDREF) is fr_bad)

fr2 = ForwardRef("Custom")
print(fr2.evaluate(globals={"Custom": Custom}) is Custom)

print(fr == ForwardRef("int"), fr == ForwardRef("str"), fr == "int")
print(repr(fr), repr(ForwardRef("X", module="m", is_class=True)))


# ---- call_annotate_function / call_evaluate_function ------------------------
def my_annotate(format):
    if format != Format.VALUE:
        raise NotImplementedError(format)
    return {"n": 5}


print(annotationlib.call_annotate_function(my_annotate, Format.VALUE))
print(annotationlib.call_evaluate_function(my_annotate, Format.VALUE))
# format=STRING/FORWARDREF on call_annotate_function is NOT exercised here --
# see the coverage line: CPython's real implementation does not simply raise
# in that case, it re-invokes `annotate` under a fake-globals STRING/
# FORWARDREF reconstruction, which uasm's simpler upfront refusal does
# not reproduce, so the two would diverge for reasons that have nothing to do
# with what this module actually covers.


# ---- get_annotate_from_class_namespace -------------------------------------
ns = {"__annotate__": my_annotate, "other": 1}
print(annotationlib.get_annotate_from_class_namespace(ns) is my_annotate)
print(annotationlib.get_annotate_from_class_namespace({}) is None)

# ---- a forward reference that is NOT a bare identifier ----------------------
# `eval()` resolves these now, which it could not when this module was
# written: calling a builtin from inside a bundled module's own body reached
# lowering unrewritten. See bundled/annotationlib.py.
class Node:
    pass


where = {"Node": Node}
print(ForwardRef("Node").evaluate(globals=where) is Node)
print(ForwardRef("list[int]").evaluate(globals=where))
print(ForwardRef("int | None").evaluate(globals=where))
print(ForwardRef("dict[str, list[Node]]").evaluate(globals=where)
      == dict[str, list[Node]])
print(ForwardRef("(1, 2, 3)").evaluate(globals=where))
print(ForwardRef("list[int]").evaluate(format=Format.STRING))


def annotated(x: "list[int]", y: "Node", z: "int | None") -> "tuple[int, str]":
    return (0, "")


got = annotationlib.get_annotations(annotated, globals=where, eval_str=True)
for key in ("x", "z", "return"):
    print(key, "=", got[key])
print("y is Node:", got["y"] is Node)

try:
    ForwardRef("missing[int]").evaluate(globals=where)
except NameError as exc:
    print("undefined:", exc)

print("done")
