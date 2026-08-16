"""Generate string-formatting conformance probes.

Formatting is where a value's *kind* becomes observable. Every other subsystem
can carry a float around as an opaque 64-bit word and stay correct; the moment
it is formatted, the word has to be interpreted, and any disagreement about
what it holds prints as a wrong character rather than a crash. That makes these
probes double as the cheapest available readout on the value model -- but only
if each one pins a single directive, because "formatting is broken" is not an
actionable verdict.

Python has three formatting languages, and they are separately implemented:

* the format spec mini-language, reached via `format()`, `str.format` and
  f-strings -- fill, align, sign, width, grouping, precision, and a type code
* `%`-interpolation, an older and differently-specified path with its own
  padding rules and its own dict form
* f-string syntax proper -- expressions, `!r`/`!s` conversions, the `=` debug
  form, and *nested* replacement fields inside a spec

FAILURE_AUDIT.md ranks 10 and 20 are both here (`f-string: nested format spec`,
8 cases; `f-string: percent format spec`, 2), and the un-root-caused bucket's
"number formatting" and "string formatting" sub-groups add 9 more.

Usage: python gen_fmt_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# format spec: alignment, fill and width
# ---------------------------------------------------------------------------

case("fmt_spec_align_right", "> right-aligns within a width", r'''
print("[" + format("x", ">5") + "]")
print("[" + format(42, ">5") + "]")
''')

case("fmt_spec_align_left", "< left-aligns within a width", r'''
print("[" + format("x", "<5") + "]")
print("[" + format(42, "<5") + "]")
''')

case("fmt_spec_align_center", "^ centres within a width", r'''
print("[" + format("ab", "^6") + "]")
''')

case("fmt_spec_fill_character", "a fill character precedes the alignment", r'''
print(format("x", "*>5"))
print(format(7, "0>4"))
''')

case("fmt_spec_zero_pad", "a leading 0 zero-pads a number", r'''
print(format(42, "05"))
print(format(-42, "05"))
''')

case("fmt_spec_sign_always", "+ forces a sign on positive numbers", r'''
print(format(5, "+"))
print(format(-5, "+"))
print(format(5, " "))
''')

case("fmt_spec_default_alignment_differs", "numbers right-align, strings left-align", r'''
print("[" + format("x", "5") + "]")
print("[" + format(3, "5") + "]")
''')


# ---------------------------------------------------------------------------
# format spec: numeric type codes and precision
# ---------------------------------------------------------------------------

case("fmt_spec_float_precision", ".Nf fixes the fraction digits", r'''
print(format(3.14159, ".2f"))
print(format(2.0, ".3f"))
print(format(1.005, ".1f"))
''')

case("fmt_spec_thousands_separator", ", groups thousands", r'''
print(format(1234567, ","))
print(format(1234.5678, ",.2f"))
''')

case("fmt_spec_underscore_separator", "_ groups thousands with underscores", r'''
print(format(1234567, "_"))
''')

case("fmt_spec_percent", "% scales by 100 and appends a sign", r'''
print(format(0.25, "%"))
print(format(0.25, ".1%"))
''')

case("fmt_spec_exponent", "e renders scientific notation", r'''
print(format(1234.5678, "e"))
print(format(1234.5678, ".2e"))
''')

case("fmt_spec_general_g", "g picks fixed or scientific by magnitude", r'''
print(format(1234.5678, "g"))
print(format(0.00001234, "g"))
print(format(1234.5678, ".3g"))
''')

case("fmt_spec_hex_oct_bin", "x/o/b render integer bases", r'''
print(format(255, "x"))
print(format(255, "X"))
print(format(8, "o"))
print(format(5, "b"))
''')

case("fmt_spec_hash_prefixes_base", "# adds the base prefix", r'''
print(format(255, "#x"))
print(format(5, "#b"))
''')

case("fmt_spec_int_as_char", "c renders an int as its code point", r'''
print(format(65, "c"))
''')

case("fmt_spec_str_precision_truncates", ".N truncates a string", r'''
print(format("abcdef", ".3"))
''')

case("fmt_spec_float_no_code_keeps_point", "a bare float keeps its decimal point", r'''
print(format(2.0, ""))
print(str(2.0))
''')


# ---------------------------------------------------------------------------
# f-strings
# ---------------------------------------------------------------------------

case("fmt_fstring_expression", "an f-string evaluates a full expression", r'''
a = 3
b = 4
print(f"{a + b}")
print(f"{a * b + 1}")
''')

case("fmt_fstring_calls_method", "an f-string may call a method", r'''
name = "ada"
print(f"{name.upper()}")
''')

case("fmt_fstring_spec_applies", "a spec after : applies inside an f-string", r'''
v = 3.14159
print(f"{v:.2f}")
print(f"{42:05}")
''')

case("fmt_fstring_nested_spec_width", "a nested field supplies the width", r'''
width = 6
print("[" + f"{'x':>{width}}" + "]")
''')

case("fmt_fstring_nested_spec_precision", "a nested field supplies the precision", r'''
digits = 3
print(f"{3.14159:.{digits}f}")
''')

case("fmt_fstring_nested_spec_both", "nested fields supply width and precision together", r'''
width = 9
digits = 2
print("[" + f"{3.14159:>{width}.{digits}f}" + "]")
''')

case("fmt_fstring_conversion_repr", "!r formats with repr", r'''
s = "text"
print(f"{s!r}")
print(f"{s}")
''')

case("fmt_fstring_conversion_str", "!s formats with str", r'''
class Both:
    def __str__(self):
        return "STR"

    def __repr__(self):
        return "REPR"


b = Both()
print(f"{b!s}")
print(f"{b!r}")
print(f"{b}")
''')

case("fmt_fstring_debug_equals", "the = debug form prints expression and value", r'''
count = 7
print(f"{count=}")
''')

case("fmt_fstring_debug_equals_expression", "the = debug form keeps the expression text", r'''
a = 2
b = 3
print(f"{a + b=}")
''')

case("fmt_fstring_braces_literal", "doubled braces emit a literal brace", r'''
v = 1
print(f"{{{v}}}")
print(f"{{literal}}")
''')

case("fmt_fstring_dict_subscript", "an f-string may subscript a dict", r'''
row = {"name": "ada", "n": 2}
print(f"{row['name']}-{row['n']}")
''')

case("fmt_fstring_attribute_access", "an f-string may read an attribute", r'''
class Point:
    def __init__(self):
        self.x = 5


print(f"{Point().x}")
''')

case("fmt_fstring_nested_fstring", "an f-string may contain another f-string", r'''
inner = "world"
print(f"{f'hello {inner}'}")
''')

case("fmt_fstring_conditional_expression", "a conditional expression works inside a field", r'''
n = 5
print(f"{'big' if n > 3 else 'small'}")
''')

case("fmt_fstring_multiline_concat", "adjacent f-strings concatenate", r'''
a = 1
b = 2
text = (
    f"a={a} "
    f"b={b}"
)
print(text)
''')

case("fmt_fstring_of_container", "an f-string renders a container with repr", r'''
xs = [1, "two"]
d = {"k": 1}
print(f"{xs}")
print(f"{d}")
''')


# ---------------------------------------------------------------------------
# %-interpolation
# ---------------------------------------------------------------------------

case("fmt_percent_d_and_s", "%d and %s interpolate int and str", r'''
print("%d items" % 3)
print("%s items" % "many")
''')

case("fmt_percent_float_precision", "%.Nf fixes the fraction digits", r'''
print("%.2f" % 3.14159)
print("%.0f" % 2.5)
''')

case("fmt_percent_tuple_arguments", "a tuple supplies several conversions", r'''
print("%s scored %d (%.1f%%)" % ("ada", 9, 90.0))
''')

case("fmt_percent_literal_escape", "%% emits a literal percent sign", r'''
print("100%% done" % ())
print("%d%%" % 50)
''')

case("fmt_percent_padding_width", "%Nd pads to a width", r'''
print("[%5d]" % 42)
print("[%-5d]" % 42)
print("[%05d]" % 42)
''')

case("fmt_percent_named_mapping", "%(name)s reads from a mapping", r'''
print("%(greet)s %(name)s" % {"greet": "hi", "name": "ada"})
''')

case("fmt_percent_repr_conversion", "%r interpolates with repr", r'''
print("%r" % "text")
''')

case("fmt_percent_hex_oct", "%x and %o interpolate integer bases", r'''
print("%x" % 255)
print("%o" % 8)
''')

case("fmt_percent_exponent", "%e interpolates scientific notation", r'''
print("%e" % 1234.5678)
print("%.2e" % 1234.5678)
''')


# ---------------------------------------------------------------------------
# str.format
# ---------------------------------------------------------------------------

case("fmt_format_positional_auto", "empty fields consume arguments in order", r'''
print("{} and {}".format("a", "b"))
''')

case("fmt_format_positional_index", "numbered fields may repeat and reorder", r'''
print("{1}-{0}-{1}".format("a", "b"))
''')

case("fmt_format_named_arguments", "named fields read keyword arguments", r'''
print("{greet}, {name}".format(greet="hi", name="ada"))
''')

case("fmt_format_attribute_access", "a field may read an attribute", r'''
class Point:
    def __init__(self):
        self.x = 3


print("{0.x}".format(Point()))
''')

case("fmt_format_index_access", "a field may subscript a sequence", r'''
print("{0[1]}".format(["a", "b"]))
''')

case("fmt_format_spec_after_colon", "str.format applies the spec after the colon", r'''
print("{:>6}".format("x"))
print("{:.2f}".format(3.14159))
''')

case("fmt_format_map", "str.format_map reads a mapping", r'''
print("{name}".format_map({"name": "ada"}))
''')


# ---------------------------------------------------------------------------
# str methods that pad or align
# ---------------------------------------------------------------------------

case("fmt_str_just_methods", "ljust/rjust/center pad to a width", r'''
print("[" + "ab".ljust(5) + "]")
print("[" + "ab".rjust(5) + "]")
print("[" + "ab".center(6, "-") + "]")
''')

case("fmt_str_zfill", "zfill pads a numeric string with zeros", r'''
print("42".zfill(5))
print("-42".zfill(5))
''')


# ---------------------------------------------------------------------------
# what each kind renders as -- the value-model readout
# ---------------------------------------------------------------------------

case("fmt_bool_renders_as_word", "a bool formats as True/False everywhere", r'''
b = True
print(f"{b}")
print("{}".format(b))
print("%s" % b)
print(str(b))
''')

case("fmt_none_renders_as_word", "None formats as None everywhere", r'''
n = None
print(f"{n}")
print("{}".format(n))
print("%s" % (n,))
print(str(n))
''')

case("fmt_float_whole_keeps_point", "a whole-valued float keeps .0 in every path", r'''
f = 4.0
print(f"{f}")
print("{}".format(f))
print("%s" % f)
print(str(f))
''')


# ===========================================================================
# Wave 2. Formatting came back the most conformant of the five areas (29%
# failing against 46% overall), which is the signal to stop broadening it and
# instead probe the edges: values whose float representation is delicate, the
# specs that only apply to non-builtin numeric types, and the conversions wave
# 1 did not reach.
# ===========================================================================

case("fmt_conversion_ascii", "!a formats with ascii()", r'''
s = "café"
print(f"{s!a}")
''')

case("fmt_negative_zero", "negative zero keeps its sign", r'''
print(-0.0)
print(f"{-0.0}")
print(format(-0.0, ".1f"))
''')

case("fmt_float_extremes", "very large and very small floats round-trip", r'''
print(1e308)
print(1e-308)
print(2.220446049250313e-16)
''')

case("fmt_float_repr_shortest", "float repr is the shortest round-tripping form", r'''
print(0.3)
print(1 / 3)
print(2 / 3)
print(1e16)
print(1e17)
''')

case("fmt_infinity_and_nan", "inf and nan have canonical spellings", r'''
inf = float("inf")
nan = float("nan")
print(inf)
print(-inf)
print(nan)
print(f"{inf}")
''')

case("fmt_int_very_large", "a large int formats without loss", r'''
n = 2 ** 100
print(n)
print(f"{n}")
print(format(n, ","))
''')

case("fmt_spec_on_fraction", "a format spec applies to a Fraction", r'''
from fractions import Fraction

print(format(Fraction(1, 3), ".3f"))
''')

case("fmt_spec_on_decimal", "a format spec applies to a Decimal", r'''
from decimal import Decimal

print(format(Decimal("2.5"), ".3f"))
print(format(Decimal("1234.5"), ","))
''')

case("fmt_spec_e_uppercase", "E renders the exponent marker in upper case", r'''
print(format(1234.5, "E"))
print(format(1234.5, ".1E"))
''')

case("fmt_spec_g_uppercase", "G renders general format in upper case", r'''
print(format(0.000012345, "G"))
''')

case("fmt_thousands_on_negative", "grouping survives a minus sign", r'''
print(format(-1234567, ","))
print(format(-1234.5, ",.1f"))
''')

case("fmt_zero_pad_with_sign_and_group", "zero padding, sign and grouping combine", r'''
print(format(1234, "+012,"))
''')

case("fmt_repr_escapes_quotes", "repr picks a quoting style and escapes", r'''
print(repr("it's"))
print(repr('say "hi"'))
print(repr("back\\slash"))
''')

case("fmt_repr_of_nested_container", "repr renders nested containers", r'''
print(repr({"k": [1, (2, 3)], "s": {"inner": None}}))
''')

case("fmt_format_nested_spec_in_method", "str.format supports a nested spec field", r'''
print("{0:{1}}".format("x", ">6"))
''')

case("fmt_percent_multiple_padding_forms", "%-format combines flags, width and precision", r'''
print("[%+08.2f]" % 3.14159)
print("[%-8.3s]" % "abcdef")
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_fmt_cases.py", sys.argv))
