# tier: spec
# ref: library/exceptions.html#concrete-exceptions
# expect:
# ZeroDivisionError
# IndexError
# KeyError
# ValueError
# TypeError
for thunk in ('1/0', '[1,2][9]', '{}["k"]', 'int("x")', 'len(5)'):
    try:
        eval(thunk)
    except Exception as e:
        print(type(e).__name__)
