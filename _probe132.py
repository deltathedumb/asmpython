# probe132: structural pattern matching with sequences and mappings
def process_command(cmd) -> str:
    match cmd:
        case []:
            return "empty list"
        case [x]:
            return "single: " + str(x)
        case [x, y]:
            return "pair: " + str(x) + "," + str(y)
        case [x, *rest]:
            return "head=" + str(x) + " rest=" + str(len(rest))
        case _:
            return "other"

print(process_command([]))        # empty list
print(process_command([1]))       # single: 1
print(process_command([1, 2]))    # pair: 1,2
print(process_command([1,2,3,4])) # head=1 rest=3

# Dict pattern matching
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

print(handle({"type": "click", "x": 10, "y": 20}))   # click at 10,20
print(handle({"type": "key", "key": "Enter"}))         # key pressed: Enter
print(handle({"type": "scroll"}))                      # unknown event type: scroll
print(handle({}))                                      # no type
