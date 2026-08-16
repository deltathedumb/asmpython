# probes: str is immutable; += rebinds
# expect:
# a
# ab
s = "a"
t = s
t += "b"
print(s)
print(t)
