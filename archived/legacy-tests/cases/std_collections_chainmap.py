# probes: ChainMap searches its maps in order
# expect:
# 1
# 3
import collections

merged = collections.ChainMap({"a": 1}, {"a": 2, "b": 3})
print(merged["a"])
print(merged["b"])
