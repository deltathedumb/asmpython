# probes: warnings.catch_warnings records a warning
# expect:
# 1
# careful
import warnings

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    warnings.warn("careful", UserWarning)
print(len(caught))
print(str(caught[0].message))
