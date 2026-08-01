# probes: `in` compares against each element (str elements)
# expect:
# True
# False
# True
xs = ["aa", "bb", "cc", "dd"]
print("bb" in xs)
print("zz" in xs)
print("zz" not in xs)
