# probes: pprint.pformat sorts dict keys
# expect:
# {'a': 2, 'b': 1}
import pprint

print(pprint.pformat({"b": 1, "a": 2}))
