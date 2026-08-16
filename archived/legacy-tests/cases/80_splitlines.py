# expect:
# 3
# alpha
# beta
# gamma
# 2

# str.splitlines(): split on '\n' (a trailing newline doesn't yield an empty
# final element), returning a list[str].

s = "alpha\nbeta\ngamma\n"
lines = s.splitlines()
print(len(lines))
for ln in lines:
    print(ln)
print(len("one\ntwo".splitlines()))
