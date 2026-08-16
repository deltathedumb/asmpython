# expect:
# pre
# post-true
# pre2
# caught: bad
# pre3
# caught: AssertionError
# done

# True asserts are inert; the program proceeds.
print("pre")
assert 1 + 1 == 2
print("post-true")

# False assert with a message: lowers to `raise <msg>`, so a surrounding
# `try` catches it as a string.
print("pre2")
try:
    assert 5 > 10, "bad"
    print("not reached")
except as e:
    print("caught:", e)

# Bare false assert raises the synthesized "AssertionError" string.
print("pre3")
try:
    assert 0
    print("not reached")
except as e:
    print("caught:", e)

print("done")
