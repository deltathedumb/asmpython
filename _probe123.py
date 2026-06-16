# probe123: complex exception patterns with class hierarchy
class Base(Exception):
    def __init__(self, code: int, msg: str) -> None:
        self.code = code
        self.msg = msg

    def __str__(self) -> str:
        return f"[{self.code}] {self.msg}"

class NetworkError(Base):
    pass

class TimeoutError(NetworkError):
    def __init__(self, host: str, timeout: int) -> None:
        super().__init__(408, f"timeout connecting to {host} after {timeout}s")
        self.host = host
        self.timeout = timeout

class AuthError(Base):
    def __init__(self, user: str) -> None:
        super().__init__(401, f"unauthorized: {user}")

def connect(host: str, user: str, fast: bool) -> str:
    if not fast:
        raise TimeoutError(host, 30)
    if user == "bad":
        raise AuthError(user)
    return "connected"

# Test specific catch
try:
    connect("example.com", "alice", False)
except TimeoutError as e:
    print(str(e))       # [408] timeout connecting to example.com after 30s
    print(e.host)       # example.com
    print(e.timeout)    # 30

# Test base class catch
try:
    connect("x.com", "bad", True)
except Base as e:
    print(str(e))   # [401] unauthorized: bad
    print(e.code)   # 401

# Test successful
try:
    result = connect("y.com", "alice", True)
    print(result)   # connected
except Base:
    print("fail")

# Test inheritance chain
try:
    raise TimeoutError("z.com", 60)
except NetworkError as e:
    print("caught as NetworkError")   # caught as NetworkError
except Base:
    print("caught as Base")
