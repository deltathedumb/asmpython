# probes: match selects on literal patterns
# expect:
# ok
# missing
# other
def name_for(code):
    match code:
        case 200:
            return "ok"
        case 404:
            return "missing"
        case _:
            return "other"


print(name_for(200))
print(name_for(404))
print(name_for(500))
