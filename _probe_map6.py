d = {"a": "hello", "b": "world"}

match d:
    case {"a": "hello"}:
        print("matched a=hello")
    case _:
        print("no match")
