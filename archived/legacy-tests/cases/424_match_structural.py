# expect:
# empty list
# single: 1
# pair: 1,2
# head=1 rest=3
# click at 10 20
# key at x=5
# other

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

print(process_command([]))
print(process_command([1]))
print(process_command([1, 2]))
print(process_command([1, 2, 3, 4]))

def handle(event: dict) -> str:
    match event:
        case {"type": "click", "x": x, "y": y}:
            return "click at " + str(x) + " " + str(y)
        case {"type": "key", "x": x}:
            return "key at x=" + str(x)
        case _:
            return "other"

print(handle({"type": "click", "x": 10, "y": 20}))
print(handle({"type": "key", "x": 5}))
print(handle({"type": "scroll"}))
