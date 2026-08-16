# expect:
# empty string false
# nonempty string true
# loop count 2
# empty list false
# nonempty list true
# empty dict false
# nonempty dict true
empty = ""
if empty:
    print("empty string true")
else:
    print("empty string false")

nonempty = "x"
if nonempty:
    print("nonempty string true")
else:
    print("nonempty string false")

text = ""
count = 0
while not text:
    count += 1
    if count == 2:
        text = "done"
print("loop count", count)

empty_list: list[int] = []
if empty_list:
    print("empty list true")
else:
    print("empty list false")

nonempty_list = [1]
if nonempty_list:
    print("nonempty list true")
else:
    print("nonempty list false")

empty_dict: dict[str, int] = {}
if empty_dict:
    print("empty dict true")
else:
    print("empty dict false")

nonempty_dict = {"x": 1}
if nonempty_dict:
    print("nonempty dict true")
else:
    print("nonempty dict false")
