# tier: spec
# ref: library/exceptions.html#concrete-exceptions
# expect:
# ZeroDivisionError
# IndexError
# KeyError
# ValueError
# TypeError
# NameError
# AttributeError
# ValueError
ops = [
    lambda: 1 / 0,
    lambda: [][0],
    lambda: {}["k"],
    lambda: int("x"),
    lambda: 1 + "a",
    lambda: undefined_name,
    lambda: (1).missing_attr,
    lambda: [1, 2].index(9),
]
for op in ops:
    try:
        op()
    except Exception as e:
        print(type(e).__name__)
