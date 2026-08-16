# expect:
# 6 4
def make_ops():
    return {'inc': lambda x: x + 1, 'dec': lambda x: x - 1}
ops = make_ops()
print(ops['inc'](5), ops['dec'](5))
# asmpython (beta/3.14.0) MISMATCH: prints '0 0\n' (wrong).
