# expect:
# ValueError
# KeyError
for exc in [ValueError, KeyError]:
    try:
        raise exc('x')
    except (ValueError, KeyError) as e:
        print(type(e).__name__)
# asmpython (beta/3.14.0) rejects at compile: [E132] list element of type type is not supported yet
