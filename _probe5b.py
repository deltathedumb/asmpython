def parse_int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return -1

print(parse_int("42"))
print(parse_int("abc"))
print(parse_int("0x10"))
