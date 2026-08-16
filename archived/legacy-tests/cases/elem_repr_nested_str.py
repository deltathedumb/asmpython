# probes: a container renders inside another container (str elements)
# expect:
# [['aa', 'bb', 'cc', 'dd']]
# {'k': ['aa', 'bb', 'cc', 'dd']}
# (['aa', 'bb', 'cc', 'dd'],)
xs = ["aa", "bb", "cc", "dd"]
print([xs])
print({"k": xs})
print((xs,))
