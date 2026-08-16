# probes: f-strings render each kind correctly
# expect:
# 7
# text
# 2.5
# True
# 7 text 2.5 True
i = 7
s = "text"
f = 2.5
b = True
print(f"{i}")
print(f"{s}")
print(f"{f}")
print(f"{b}")
print(f"{i} {s} {f} {b}")
