# tier: spec
# ref: library/stdtypes.html#truth-value-testing
# expect:
# [] False False False
# None False True True
# 0 False False False
# '' False False False
# {} False False False
for v in ([], None, 0, "", {}):
    print(repr(v), bool(v), v is None, v == None)
