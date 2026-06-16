d = {"a": "hello", "b": "world"}

match d:
    case {"a": x, "b": y}:
        print(x, y)
    case _:
        print("no match")
