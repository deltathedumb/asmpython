# tier: spec
# ref: library/stdtypes.html#truth-value-testing
# expect:
# [] False
# () False
# {} False
# set() False
# '' False
# 0 False
# 0.0 False
# None False
for v in ([], (), {}, set(), "", 0, 0.0, None):
    print(repr(v), bool(v))
