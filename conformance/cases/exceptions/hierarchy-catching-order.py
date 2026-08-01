# tier: spec
# ref: library/exceptions.html#exception-hierarchy
# expect:
# ValueError-or-TypeError ValueError
# ValueError-or-TypeError TypeError
# LookupError KeyError
# LookupError IndexError
# True True
# True
for exc in (ValueError("v"), TypeError("t"), KeyError("k"), IndexError("i")):
    try:
        raise exc
    except LookupError as e:
        print("LookupError", type(e).__name__)
    except (ValueError, TypeError) as e:
        print("ValueError-or-TypeError", type(e).__name__)
print(issubclass(KeyError, LookupError), issubclass(IndexError, LookupError))
print(issubclass(ZeroDivisionError, ArithmeticError))
