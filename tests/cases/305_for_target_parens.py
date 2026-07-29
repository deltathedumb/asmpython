# expect:
# 10 20
# 10 20
# 10 20
# 1 2
# 3 4
# 30

# The loop-target shapes that must keep compiling NATIVELY after P026 started
# refusing nested unpacking (see cases_fail/nested_for_target.py).
#
# A parenthesized group spanning the WHOLE target list carries no information:
# `for (k, v) in items` means exactly `for k, v in items`, so it still
# flattens. Only a group that is one element AMONG SEVERAL is refused. This
# case exists because the obvious over-broad fix -- reject any parenthesized
# group -- passes the failing case and breaks every loop below.
pairs = [(10, 20)]

for (k, v) in pairs:
    print(k, v)

for k, v in pairs:
    print(k, v)

for (k, v,) in pairs:          # trailing comma inside the group
    print(k, v)

for k, v in [(1, 2), (3, 4)]:
    print(k, v)

# Deliberately NOT `for (k, v)` here: a comprehension whose unpacking target
# reuses a name already bound above silently evaluates to 0 (pre-existing at
# fa7087f7, unrelated to P026 -- see cases_fail/ note). Fresh names keep this
# case testing the one thing it is for: that a whole-target parenthesized
# group still compiles natively.
print(sum([q + w for (q, w) in pairs]))
