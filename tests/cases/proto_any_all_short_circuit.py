# probes: any/all stop at the deciding element
# expect:
# checked False
# checked True
# checked False
# True
# checked True
# checked False
# checked True
# False
def note(value):
    print("checked " + str(value))
    return value


print(any([note(False), note(True), note(False)]))
print(all([note(True), note(False), note(True)]))
