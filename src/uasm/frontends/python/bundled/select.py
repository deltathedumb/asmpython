"""Readiness, over the host-service `net` group's `host_net_ready`.

THE LOOP THE GROUP DELIBERATELY DOES NOT HAVE. `objects/hostsvc.py`'s
`host_net_ready` asks about ONE descriptor, because a set of them means an
array layout every backend has to agree on and a frontend has to build --
a second ABI to keep in step, for a loop a caller can write. This module
is that loop, written once in Python where the whole point of the layer is
that it needs writing once.

COVERAGE: `select(rlist, wlist, xlist, timeout=None)` over objects with a
`fileno()` (a `socket`, or a bare descriptor int) and `error` as an alias
of `OSError`, matching CPython's. `poll()` objects with `register`,
`modify`, `unregister` and `poll(timeout_ms)`, and the `POLLIN`/`POLLOUT`/
`POLLERR`/`POLLHUP`/`POLLNVAL` constants. `PIPE_BUF`.

TIMEOUT IS HONOURED IN AGGREGATE, NOT PER DESCRIPTOR, and that is worth
being exact about because it is the one place the shape of the underlying
call shows through. `select` asks each descriptor in turn: with a timeout
of 0 -- a poll, and by far the common case -- each question is
instantaneous and the answers are the same set CPython returns. With a
POSITIVE timeout it polls each descriptor, and if none is ready waits the
remaining time on the FIRST one before polling the whole set once more, so
a wait wakes on the first descriptor rather than on any of them. With a
timeout of None over more than one descriptor it does the same with an
indefinite wait. A program watching several descriptors where any of them
may become ready first should pass 0 and loop, which is what a
single-threaded program with other work to do does anyway.

NOT COVERED, each refused BY NAME: the EXCEPTIONAL set (`xlist` must be
empty -- out-of-band data is a socket option this build's `net` group does
not have), `epoll`, `kqueue`, `devpoll` (each a platform-specific
interface where `select` is the portable one), `poll.poll()` on a
descriptor registered for `POLLPRI`, and `select` on anything that is not
a socket -- a file is always ready in CPython and this cannot say so,
since `host_net_ready` only knows about the `net` group's descriptors.
"""


_HOST_EINVAL = -9

#: `host_net_ready`'s `want`, spelled once.
_READ = 1
_WRITE = 2

error = OSError

POLLIN = 1
POLLPRI = 2
POLLOUT = 4
POLLERR = 8
POLLHUP = 16
POLLNVAL = 32

#: The smallest write a pipe guarantees is atomic. POSIX's floor; every
#: platform this targets is at least this and most are larger.
PIPE_BUF = 512


def _fileno(one, what):
    """A descriptor from whatever a caller passed -- an object with
    `fileno()`, or an int that already is one. CPython's own rule, and its
    own error for anything else."""
    if isinstance(one, int):
        got = one
    else:
        method = getattr(one, "fileno", None)
        if method is None:
            raise TypeError("argument must be an int, or have a fileno() "
                            "method")
        got = method()
        if not isinstance(got, int):
            raise TypeError("fileno() returned a non-integer")
    if got < 0:
        raise ValueError("file descriptor cannot be a negative integer ("
                         + str(got) + ")")
    return got


def _ready(one, want, timeout_ns, what):
    fd = _fileno(one, what)
    got = host_net_ready(fd, want, timeout_ns)
    if got == _HOST_EINVAL:
        raise OSError("[Errno 9] Bad file descriptor")
    if got < 0:
        raise OSError(what + ": failed")
    return got == 1


def select(rlist, wlist, xlist, timeout=None):
    """`(readable, writable, exceptional)`.

    THE ANSWER IS ALWAYS THE SAME SHAPE as CPython's -- three lists of the
    objects that were passed, not of descriptors -- so a program that
    passes sockets gets sockets back and can call `recv` on them.
    """
    readers = list(rlist)
    writers = list(wlist)
    if list(xlist):
        raise OSError("select: the exceptional set is not available -- "
                      "out-of-band data is a socket option this build's "
                      "network does not have; see the module docstring")
    if timeout is not None and timeout < 0:
        raise ValueError("timeout must be non-negative")

    def sweep(wait_ns):
        hits_r = []
        hits_w = []
        for one in readers:
            if _ready(one, _READ, wait_ns, "select"):
                hits_r.append(one)
        for one in writers:
            if _ready(one, _WRITE, wait_ns, "select"):
                hits_w.append(one)
        return hits_r, hits_w

    # A POLL FIRST, ALWAYS. Whatever the timeout, the question "is anything
    # ready right now" is answered without waiting, and a caller who passed
    # 0 is finished here -- which is the common case and the one that is
    # exactly CPython.
    hits_r, hits_w = sweep(0)
    if hits_r or hits_w or timeout == 0:
        return hits_r, hits_w, []
    if not readers and not writers:
        # NOTHING TO WAIT ON. CPython sleeps for the timeout and answers
        # three empty lists; with no descriptors there is nothing that
        # could change during the sleep, so the same answer comes back
        # without it.
        return [], [], []
    # See the module docstring: the wait happens on the FIRST descriptor,
    # then the whole set is polled once more.
    first = readers[0] if readers else writers[0]
    want = _READ if readers else _WRITE
    wait_ns = -1 if timeout is None else int(timeout * 1000000000)
    _ready(first, want, wait_ns, "select")
    hits_r, hits_w = sweep(0)
    return hits_r, hits_w, []


class poll:
    """CPython's `poll` object, over the same one-descriptor question.

    THE EVENT MASK IS HONOURED for `POLLIN` and `POLLOUT`, which are the
    two `host_net_ready` can answer. `POLLERR`, `POLLHUP` and `POLLNVAL`
    are OUTPUT-only in CPython too -- a caller does not register for them
    -- and this never reports them, because a descriptor this build can
    ask about is one the `net` group is holding open.
    """

    def __init__(self):
        self._registered = {}
        self._objects = {}

    def register(self, fd, eventmask=None):
        if eventmask is None:
            eventmask = POLLIN | POLLPRI | POLLOUT
        if eventmask & POLLPRI:
            # DROPPED RATHER THAN REFUSED, because CPython's own default
            # mask includes it and refusing the default would make
            # `register(sock)` fail. What is refused is asking for it
            # ALONE, below, where it is the whole request.
            eventmask = eventmask & ~POLLPRI
        if not eventmask & (POLLIN | POLLOUT):
            raise OSError("poll: POLLPRI alone is not available -- "
                          "out-of-band data is a socket option this "
                          "build's network does not have")
        key = _fileno(fd, "register")
        self._registered[key] = eventmask
        self._objects[key] = fd

    def modify(self, fd, eventmask):
        key = _fileno(fd, "modify")
        if key not in self._registered:
            raise OSError("[Errno 2] No such file or directory")
        self.register(fd, eventmask)

    def unregister(self, fd):
        key = _fileno(fd, "unregister")
        if key not in self._registered:
            raise KeyError(key)
        del self._registered[key]
        del self._objects[key]

    def poll(self, timeout=None):
        """`[(fd, events), ...]` for whatever is ready.

        MILLISECONDS, because CPython's `poll` takes them where its
        `select` takes seconds -- an inconsistency in the standard
        library that a reimplementation has to keep, since a program
        written against one passes the number the other would misread by
        a factor of a thousand.
        """
        if timeout is not None and timeout < 0:
            timeout = None
        out = []
        for fd in list(self._registered):
            mask = self._registered[fd]
            events = 0
            if mask & POLLIN and _ready(fd, _READ, 0, "poll"):
                events = events | POLLIN
            if mask & POLLOUT and _ready(fd, _WRITE, 0, "poll"):
                events = events | POLLOUT
            if events:
                out.append((fd, events))
        if out or timeout == 0 or not self._registered:
            return out
        first = list(self._registered)[0]
        mask = self._registered[first]
        want = _READ if mask & POLLIN else _WRITE
        wait_ns = -1 if timeout is None else int(timeout) * 1000000
        _ready(first, want, wait_ns, "poll")
        return self.poll(0)
