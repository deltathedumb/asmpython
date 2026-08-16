# probes: list.remove deletes an element by value (str elements)
# expect:
# ['aa', 'cc', 'dd']
# 3
xs = list(["aa", "bb", "cc", "dd"])
xs.remove("bb")
print(xs)
print(len(xs))
