# expect:
# 5
def get_op(name):
    if name == 'max':
        return max
    return min
fn = get_op('max')
print(fn([3, 1, 4, 1, 5]))
# asmpython (beta/3.14.0) MISMATCH: prints '8491920\n' (wrong).
