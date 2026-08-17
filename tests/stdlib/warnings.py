# COVERAGE: formatwarning, filterwarnings, simplefilter, resetwarnings,
# filters, catch_warnings(record=True), warn with a category and with a
# Warning instance, the "error" action, and `deprecated` on a function, on a
# class, and with category=None. NOT covered here: once/default registry
# behaviour, stacklevel attribution, or the warning CPython issues when a
# class SUBCLASSES a deprecated one -- all of which the module itself declares
# it does not implement.
#
# EVERY `deprecated` ASSERTION RECORDS RATHER THAN PRINTS, and forces
# `simplefilter("always")` first. The startup filters ignore DeprecationWarning
# outside `__main__`, and this module attributes every warning to `<program>`
# because it has no frame inspection -- so a test that let the default filters
# decide would be measuring the attribution rather than the decorator, and
# would differ from CPython for a reason `deprecated` has nothing to do with.
#
# The FORMAT is the part programs depend on, so it is compared character for
# character rather than by substring.
import warnings

print(repr(warnings.formatwarning("m", UserWarning, "f.py", 12)))
print(repr(warnings.formatwarning("m", SyntaxWarning, "f.py", 12, "x = 1")))
# A blank source line adds nothing, and a padded one is stripped.
print(repr(warnings.formatwarning("m", UserWarning, "f.py", 1, "   ")))
print(repr(warnings.formatwarning("m", UserWarning, "f.py", 1, "  y  ")))

# `filterwarnings` INSERTS AT THE FRONT, so the newest rule wins. Asserted by
# adding two contradicting rules and seeing which takes effect.
warnings.resetwarnings()
print(len(warnings.filters))
warnings.simplefilter("ignore")
warnings.simplefilter("error")
print(warnings.filters[0][0], warnings.filters[1][0])

# `append=True` puts it last instead, so it loses to what is already there.
warnings.resetwarnings()
warnings.simplefilter("ignore")
warnings.simplefilter("error", append=True)
print(warnings.filters[0][0], warnings.filters[-1][0])

# ERROR turns a warning into an exception, which is how a test makes one fatal.
warnings.resetwarnings()
warnings.simplefilter("error")
try:
    warnings.warn("boom")
except UserWarning as exc:
    print("raised UserWarning:", exc)
try:
    warnings.warn("bang", RuntimeWarning)
except RuntimeWarning as exc:
    print("raised RuntimeWarning:", exc)

# A Warning INSTANCE carries its own category, overriding the argument.
warnings.resetwarnings()
warnings.simplefilter("error")
try:
    warnings.warn(SyntaxWarning("from the instance"), UserWarning)
except SyntaxWarning as exc:
    print("category came from the instance:", exc)

# IGNORE really drops it: nothing raised, nothing recorded.
warnings.resetwarnings()
warnings.simplefilter("ignore")
with warnings.catch_warnings(record=True) as seen:
    warnings.warn("dropped")
print("ignored ->", len(seen))

# RECORDING captures instead of printing, which is what lets a test assert a
# warning was issued without the text reaching a terminal.
warnings.resetwarnings()
warnings.simplefilter("always")
with warnings.catch_warnings(record=True) as seen:
    warnings.warn("first")
    warnings.warn("second", RuntimeWarning)
print(len(seen))
print(str(seen[0].message), seen[0].category.__name__)
print(str(seen[1].message), seen[1].category.__name__)

# `catch_warnings` RESTORES the filter list on exit, which is the reason it
# exists -- a test that changes filters must not leak them.
warnings.resetwarnings()
warnings.simplefilter("ignore")
before = len(warnings.filters)
with warnings.catch_warnings():
    warnings.simplefilter("error")
    warnings.filterwarnings("ignore", category=RuntimeWarning)
print(before == len(warnings.filters), warnings.filters[0][0])

# A CATEGORY FILTER matches subclasses, because `issubclass` is the rule.
warnings.resetwarnings()
warnings.simplefilter("ignore")
warnings.filterwarnings("error", category=Warning)
try:
    warnings.warn("subclass of Warning", UserWarning)
except UserWarning:
    print("Warning filter caught a UserWarning")

# ---- PEP 702, `deprecated` ------------------------------------------------
# The decorated things are built at module level, OUTSIDE any recording block:
# under CPython `class Sub(Old)` warns at the class statement itself, and that
# warning is not one asmpython issues. Keeping the definitions out here means
# the difference lands on stderr, where it belongs, rather than inside a count
# this compares.
#
# FILTERS RESET TO "ignore" FIRST, and not for tidiness. The block above leaves
# `error` in force for every Warning, and under CPython the `class Sub(Old)`
# statement below RAISES there -- the run stops, the oracle fails, and the
# suite reports it as the test program being wrong. It is the one place the
# uncovered half of `deprecated` is observable, so it is silenced deliberately
# rather than left to be rediscovered.
warnings.resetwarnings()
warnings.simplefilter("ignore")


@warnings.deprecated("use spam() instead")
def ham(a, b=2):
    "the old one"
    return a + b


@warnings.deprecated("gone in 4.0", category=RuntimeWarning)
def eggs():
    return "eggs"


@warnings.deprecated("documented only", category=None)
def quiet():
    return "quiet"


@warnings.deprecated("Old is over")
class Old:
    def __init__(self, n=0):
        self.n = n


class Sub(Old):
    pass


# THE ATTRIBUTE IS THE HALF A TYPE CHECKER READS, and it is set whether or not
# a warning is ever issued -- including for category=None, which issues none.
print(ham.__deprecated__, eggs.__deprecated__, quiet.__deprecated__)
print(Old.__deprecated__)
# `functools.wraps` is applied, so the wrapper still answers as the function.
print(ham.__name__, ham.__doc__)
print(ham.__wrapped__.__deprecated__)

warnings.resetwarnings()
warnings.simplefilter("always")
with warnings.catch_warnings(record=True) as seen:
    print(ham(1))
print(len(seen), seen[0].category.__name__, str(seen[0].message))

warnings.resetwarnings()
warnings.simplefilter("always")
with warnings.catch_warnings(record=True) as seen:
    print(eggs())
print(len(seen), seen[0].category.__name__, str(seen[0].message))

# category=None issues NOTHING. That is the spelling for something deprecated
# in the documentation and not yet noisy.
warnings.resetwarnings()
warnings.simplefilter("always")
with warnings.catch_warnings(record=True) as seen:
    print(quiet())
print("quiet ->", len(seen))

# A DEPRECATED CLASS WARNS WHEN IT IS INSTANTIATED, and its subclass does not:
# the subclass is not the thing that was deprecated.
warnings.resetwarnings()
warnings.simplefilter("always")
with warnings.catch_warnings(record=True) as seen:
    print(Old(3).n)
print(len(seen), seen[0].category.__name__, str(seen[0].message))

warnings.resetwarnings()
warnings.simplefilter("always")
with warnings.catch_warnings(record=True) as seen:
    print(Sub(4).n)
print("subclass ->", len(seen))

# The two TypeErrors, whose messages CPython states exactly.
try:
    warnings.deprecated(42)
except TypeError as exc:
    print("TypeError:", exc)
try:
    warnings.deprecated("m")(42)
except TypeError as exc:
    print("TypeError:", exc)

print("done")
