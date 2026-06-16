# expect:
# caught zero div
# caught value error
# caught type error
# finally
# done

def divide(a: int, b: int) -> int:
    return a // b

def test_except() -> None:
    try:
        x: int = divide(10, 0)
    except ZeroDivisionError:
        print("caught zero div")
    except ValueError:
        print("caught value error")

    try:
        raise ValueError("bad value")
    except ZeroDivisionError:
        print("wrong handler")
    except ValueError:
        print("caught value error")

    try:
        raise TypeError("type mismatch")
    except (ZeroDivisionError, TypeError):
        print("caught type error")

    try:
        x = divide(1, 0)
    except ZeroDivisionError:
        pass
    finally:
        print("finally")

test_except()
print("done")
