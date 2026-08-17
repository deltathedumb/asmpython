# COVERAGE: every feature name as a _Feature, its optional and mandatory
# release tuples, its compiler_flag, getOptionalRelease/getMandatoryRelease,
# all_feature_names, and the CO_* constants. The module is complete, so this
# tests all of it.
#
# `from __future__ import annotations` is NOT tested here: it is a compiler
# directive consumed where the source is parsed and never reaches the module.
# What this covers is the other half -- `import __future__` and reading a
# feature back, which is how a program asks when a feature arrived.
import __future__

print(__future__.annotations.optional[:2])
print(__future__.annotations.mandatory)
print(type(__future__.division).__name__)

# THE RELEASE TUPLES ARE THE REAL ONES, so a program comparing against
# sys.version_info gets a true answer rather than a plausible one.
#
# WRITTEN OUT RATHER THAN LOOPED THROUGH `getattr`. Under asmpython there are
# two `__future__`s -- this module, spliced in and reached by writing the
# attribute out, and the compiler's own table where the same names are integer
# flags -- and `getattr` reaches the second. The module says so; a program
# names the feature it wants, and that is what is measured here.
for feature in (__future__.nested_scopes, __future__.generators,
                __future__.division, __future__.absolute_import,
                __future__.with_statement, __future__.print_function,
                __future__.unicode_literals, __future__.barry_as_FLUFL,
                __future__.generator_stop, __future__.annotations):
    print(feature.optional, feature.mandatory, feature.compiler_flag)

print(__future__.all_feature_names)
print(len(__future__.all_feature_names))
print(__future__.division.getOptionalRelease())
print(__future__.division.getMandatoryRelease())
print(repr(__future__.annotations))

# The flag constants a compiler would pass through.
print(__future__.CO_FUTURE_DIVISION, __future__.CO_FUTURE_ANNOTATIONS)
print(__future__.CO_NESTED, __future__.CO_GENERATOR_ALLOWED)

# `annotations` NEVER BECAME MANDATORY -- PEP 563 was deferred and then
# superseded by PEP 649 -- which is the one feature whose mandatory release
# is None, and the reason that field is nullable at all.
print(__future__.annotations.mandatory is None,
      __future__.generator_stop.mandatory is None)

print("done")
