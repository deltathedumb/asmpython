# tier: spec
# ref: library/warnings.html
# expect:
# ['DeprecationWarning', 'UserWarning']
# 0
# True
import warnings

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    warnings.warn("dep", DeprecationWarning)
    warnings.warn("usr", UserWarning)
print(sorted(w.category.__name__ for w in caught))

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("ignore")
    warnings.warn("dep", DeprecationWarning)
print(len(caught))
print(issubclass(DeprecationWarning, Warning))
