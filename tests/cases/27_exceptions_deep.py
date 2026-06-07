# expect:
# caught: from deep
# 7
def deep():
    raise "from deep"
    return 0

def middle():
    return deep()

def top():
    return middle()

try:
    top()
except as e:
    print("caught:", e)

# Control returns normally after the try.
print(3 + 4)
