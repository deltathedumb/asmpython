# probes: an or-pattern accepts several alternatives
# expect:
# True
# False
def weekend(day):
    match day:
        case "sat" | "sun":
            return True
        case _:
            return False


print(weekend("sat"))
print(weekend("mon"))
