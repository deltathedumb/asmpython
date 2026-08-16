# expect:
# True
# True

import os

# listdir returns a list
items: list[str] = os.listdir(".")
print(len(items) > 0)

# our test file directory (tests/cases) should contain at least one .py file
found = 0
for item in items:
    if item[len(item) - 3:] == ".py":
        found = found + 1
print(found > 0)
