# probes: finally runs after a handled exception
# expect:
# finally
# handled
def f():
    try:
        raise ValueError("x")
    except ValueError:
        return "handled"
    finally:
        print("finally")


print(f())
