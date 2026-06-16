# Global variable mutation in function
counter = 0

def increment():
    global counter
    counter += 1

increment()
increment()
increment()
print(counter)

# nonlocal
def outer():
    x = 0
    def inner():
        nonlocal x
        x += 1
    inner()
    inner()
    return x

print(outer())
