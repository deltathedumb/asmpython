# probes: a runtime-built str is a distinct object
# expect:
# True
# False
a = "hello"
b = "".join(["hel", "lo"])
print(a == b)
print(a is b)
