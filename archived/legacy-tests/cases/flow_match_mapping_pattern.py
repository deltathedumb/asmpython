# probes: a mapping pattern matches keys and binds values
# expect:
# add 5
# quit
# unhandled
def route(payload):
    match payload:
        case {"action": "add", "value": v}:
            return "add " + str(v)
        case {"action": "quit"}:
            return "quit"
        case _:
            return "unhandled"


print(route({"action": "add", "value": 5}))
print(route({"action": "quit"}))
print(route({"action": "other"}))
