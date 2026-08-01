# tier: spec
# ref: reference/simple_stmts.html#the-assert-statement
# expect:
# AssertionError why
assert True
try:
    assert False, "why"
except AssertionError as e:
    print("AssertionError", e)
