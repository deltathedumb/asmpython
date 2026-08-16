# expect:
# [1, 2]
def g():
    yield 1
    yield 2


print(list(g()))
# asmpython (beta/3.14.0): "unsupported stmt YieldStmt". Generators work when
# the yield is inside a loop (see 451_generator_yield_in_if.py), but a bare
# top-level `yield` statement in the generator body is rejected.
