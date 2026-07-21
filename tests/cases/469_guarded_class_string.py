# expect:
# 1
# 1

class Provider:
    pass


def resolves(value) -> bool:
    if isinstance(value, type):
        return True
    return str(value).startswith("somnia.")


print(resolves(Provider))
print(resolves("somnia.Scene"))
