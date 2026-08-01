# tier: spec
# ref: library/warnings.html#warnings.deprecated
# min-python: 3.13
# expect:
# 1
# 1
# DeprecationWarning
import warnings
from warnings import deprecated

@deprecated("use g instead")
def f():
    return 1

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    print(f())
print(len(caught))
print(caught[0].category.__name__)
