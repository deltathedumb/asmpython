# probes: a whole-valued float keeps .0 in every path
# expect:
# 4.0
# 4.0
# 4.0
# 4.0
f = 4.0
print(f"{f}")
print("{}".format(f))
print("%s" % f)
print(str(f))
