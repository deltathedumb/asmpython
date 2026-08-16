# probes: dict(pairs) reads both halves of each pair (str elements)
# expect:
# {'aa': 'bb', 'cc': 'dd'}
# 2
pairs = [("aa", "bb"), ("cc", "dd")]
built = dict(pairs)
print(built)
print(len(built))
