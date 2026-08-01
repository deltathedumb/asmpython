# tier: cpython
# ref: library/index.html
# expect:
# telnetlib ImportError
# cgi ImportError
# imp ImportError
# asynchat ImportError
# nntplib ImportError
removed = ["telnetlib", "cgi", "imp", "asynchat", "nntplib"]
for name in removed:
    try:
        __import__(name)
    except ImportError:
        print(name, "ImportError")
    else:
        print(name, "present")
