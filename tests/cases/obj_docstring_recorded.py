# probes: __doc__ holds the class docstring
# expect:
# The documentation.
class Documented:
    """The documentation."""


print(Documented.__doc__)
