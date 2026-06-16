d = {"type": "click", "x": 10}

match d:
    case {"type": "click"}:
        print("click")
    case _:
        print("other")
