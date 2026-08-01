# probes: Counter.elements repeats each key
# expect:
# ['a', 'a', 'b']
import collections

print(sorted(collections.Counter({"a": 2, "b": 1}).elements()))
