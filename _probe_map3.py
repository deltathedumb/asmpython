def handle(event: dict) -> str:
    match event:
        case {"type": "click", "x": x, "y": y}:
            return f"click at {x},{y}"
        case {"type": "key", "key": k}:
            return f"key pressed: {k}"
        case {"type": t}:
            return f"unknown event type: {t}"
        case _:
            return "no type"

print(handle({"type": "click", "x": 10, "y": 20}))
print(handle({"type": "key", "key": "Enter"}))
print(handle({"type": "scroll"}))
print(handle({}))
