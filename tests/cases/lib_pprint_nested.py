# expect:
# {'a': [1, 2], 'b': {'c': 3}}
import pprint
print(pprint.pformat({'a': [1, 2], 'b': {'c': 3}}, width=40))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
