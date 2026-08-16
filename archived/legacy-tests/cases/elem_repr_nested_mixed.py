# probes: a container renders inside another container (mixed elements)
# expect:
# [[1, 'two', 3.5, True, None]]
# {'k': [1, 'two', 3.5, True, None]}
# ([1, 'two', 3.5, True, None],)
xs = [1, "two", 3.5, True, None]
print([xs])
print({"k": xs})
print((xs,))
