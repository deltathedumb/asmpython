def handle(event: dict) -> str:
    match event:
        case {"type": t}:
            return "type=" + str(t)
        case _:
            return "no type"

print(handle({"type": "scroll"}))
print(handle({}))
