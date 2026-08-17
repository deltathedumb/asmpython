# COVERAGE: formatwarning, filterwarnings, simplefilter, resetwarnings,
# filters, catch_warnings(record=True), warn with a category and with a
# Warning instance, and the "error" action. NOT covered here: once/default
# registry behaviour, stacklevel attribution, or `deprecated` -- all of which
# the module itself declares it does not implement.
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

print("done")
