# probes: a container renders inside another container (int elements)
# expect:
# [[10, 20, 30, 40]]
# {'k': [10, 20, 30, 40]}
# ([10, 20, 30, 40],)
xs = [10, 20, 30, 40]
print([xs])
print({"k": xs})
print((xs,))
