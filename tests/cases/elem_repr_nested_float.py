# probes: a container renders inside another container (float elements)
# expect:
# [[1.5, 2.5, 3.5, 4.5]]
# {'k': [1.5, 2.5, 3.5, 4.5]}
# ([1.5, 2.5, 3.5, 4.5],)
xs = [1.5, 2.5, 3.5, 4.5]
print([xs])
print({"k": xs})
print((xs,))
