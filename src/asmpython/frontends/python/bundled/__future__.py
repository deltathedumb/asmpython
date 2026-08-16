"""The future statement's feature objects.

`from __future__ import annotations` is a COMPILER DIRECTIVE and is handled
where the source is parsed -- it never reaches this module. What is here is
the other half: `import __future__` then reading a feature back, which is an
ordinary module of ordinary objects and is how a program asks when a feature
arrived and whether it is still optional.

The release tuples are the real ones. A feature that is MANDATORY has an
`optional` release in the past and a mandatory one that has arrived;
`annotations` is the one that never did, so its mandatory release is None.
"""

CO_NESTED = 0x0010
CO_GENERATOR_ALLOWED = 0
CO_FUTURE_DIVISION = 0x20000
CO_FUTURE_ABSOLUTE_IMPORT = 0x40000
CO_FUTURE_WITH_STATEMENT = 0x80000
CO_FUTURE_PRINT_FUNCTION = 0x100000
CO_FUTURE_UNICODE_LITERALS = 0x200000
CO_FUTURE_BARRY_AS_BDFL = 0x400000
CO_FUTURE_GENERATOR_STOP = 0x800000
CO_FUTURE_ANNOTATIONS = 0x1000000


class _Feature:
    """One future feature: when it became available, when it became the rule,
    and the compiler flag that turns it on."""

    def __init__(self, optionalRelease, mandatoryRelease, compiler_flag):
        self.optional = optionalRelease
        self.mandatory = mandatoryRelease
        self.compiler_flag = compiler_flag

    def getOptionalRelease(self):
        return self.optional

    def getMandatoryRelease(self):
        return self.mandatory

    def __repr__(self):
        return "_Feature(" + repr(self.optional) + ", " \
               + repr(self.mandatory) + ", " + repr(self.compiler_flag) + ")"


nested_scopes = _Feature((2, 1, 0, "beta", 1), (2, 2, 0, "alpha", 0),
                         CO_NESTED)
generators = _Feature((2, 2, 0, "alpha", 1), (2, 3, 0, "final", 0),
                      CO_GENERATOR_ALLOWED)
division = _Feature((2, 2, 0, "alpha", 2), (3, 0, 0, "alpha", 0),
                    CO_FUTURE_DIVISION)
absolute_import = _Feature((2, 5, 0, "alpha", 1), (3, 0, 0, "alpha", 0),
                           CO_FUTURE_ABSOLUTE_IMPORT)
with_statement = _Feature((2, 5, 0, "alpha", 1), (2, 6, 0, "alpha", 0),
                          CO_FUTURE_WITH_STATEMENT)
print_function = _Feature((2, 6, 0, "alpha", 2), (3, 0, 0, "alpha", 0),
                          CO_FUTURE_PRINT_FUNCTION)
unicode_literals = _Feature((2, 6, 0, "alpha", 2), (3, 0, 0, "alpha", 0),
                            CO_FUTURE_UNICODE_LITERALS)
barry_as_FLUFL = _Feature((3, 1, 0, "alpha", 2), (4, 0, 0, "alpha", 0),
                          CO_FUTURE_BARRY_AS_BDFL)
generator_stop = _Feature((3, 5, 0, "beta", 1), (3, 7, 0, "alpha", 0),
                          CO_FUTURE_GENERATOR_STOP)
# NEVER BECAME MANDATORY, which is why its mandatory release is None -- PEP
# 563 was deferred and then superseded by PEP 649.
annotations = _Feature((3, 7, 0, "beta", 1), None, CO_FUTURE_ANNOTATIONS)

all_feature_names = [
    "nested_scopes", "generators", "division", "absolute_import",
    "with_statement", "print_function", "unicode_literals", "barry_as_FLUFL",
    "generator_stop", "annotations",
]
