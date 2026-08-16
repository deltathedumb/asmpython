# probes: a failing assert raises with its message
# expect:
# values differ
try:
    assert 1 == 2, "values differ"
    print("no error")
except AssertionError as err:
    print(str(err))
