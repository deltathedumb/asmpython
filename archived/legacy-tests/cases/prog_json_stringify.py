# expect:
# {"a": 1}
def to_json(obj):
    if isinstance(obj, dict):
        parts = [f'"{k}": {to_json(v)}' for k, v in obj.items()]
        return '{' + ', '.join(parts) + '}'
    elif isinstance(obj, str):
        return f'"{obj}"'
    else:
        return str(obj)
print(to_json({'a': 1}))
# asmpython (beta/3.14.0) MISMATCH: prints '8819568\n' (wrong).
