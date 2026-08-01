# tier: cpython
# ref: reference/simple_stmts.html#the-assert-statement
# expect:
# passed
# AssertionError real message
try:
    assert (False, "message")
    print("passed")
except AssertionError:
    print("AssertionError")
try:
    assert False, "real message"
except AssertionError as e:
    print("AssertionError", e)
