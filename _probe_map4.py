d1 = {"type": "click", "x": 10, "y": 20}

match d1:
    case {"type": "click", "x": x, "y": y}:
        print("click at", x, y)
    case _:
        print("other")
