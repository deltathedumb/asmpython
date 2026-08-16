# expect-error: dict has no method 'iteritems'
d = {"a": 1}
for k, v in d.iteritems():
    print(k)
# Reduced from linguist samples/Python/gen-py-linguist-thrift.py:76
# (`for key, value in self.__dict__.iteritems()`) -- the Python 2 dict
# .iteritems() method was removed in Python 3.
