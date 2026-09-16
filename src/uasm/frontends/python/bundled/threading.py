"""Synchronization, on a runtime that has exactly one thread.

WHAT THIS IS AND IS NOT. `docs/STDLIB.md` puts `threading` in the tier
that "NEEDS THE FLOOR TO GROW", and for the SCHEDULER that is still
exactly right: a second thread of execution is a platform function this
runtime does not have and cannot fake -- `objects/hostsvc.py` has no
group for it, and inventing one that ran a target on the calling thread
while claiming otherwise would be worse than not having the module.

BUT MOST OF `threading` IS NOT THE SCHEDULER. `Lock`, `RLock`,
`Semaphore`, `BoundedSemaphore`, `Event`, `Condition`, `Barrier` and
`local` are STATE MACHINES over a counter and a flag, and every one of
them is EXACTLY CPython on one thread -- `acquire`/`release` pairing,
the re-entrancy count, `locked()`, the `RuntimeError` for releasing an
un-acquired lock, the `ValueError` for over-releasing a bounded
semaphore, `Event`'s set/clear/wait, `local`'s per-thread namespace
(there is one thread, so it is its namespace), and every one of them as
a context manager. A program that uses these to be CORRECT under
threading gets the same answers here, which is the part of the module
that has answers at all without a second thread.

WHERE A SECOND THREAD WOULD HAVE BEEN NEEDED, THIS SAYS SO RATHER THAN
GUESSING. An operation whose only possible outcome on one thread is to
wait forever for a thread that cannot exist RAISES `RuntimeError` naming
the deadlock, instead of hanging:

  * `Lock.acquire()` blocking on a lock this thread already holds.
  * `Condition.wait()` with no other thread to notify.
  * `Barrier.wait()` when the barrier wants more parties than there are.

CPython DEADLOCKS on each of those in a single-threaded program, so no
correct program reaches one and no test can distinguish "raised" from
"never returned" except by the fact that this one finishes. A
`blocking=False` acquire, by contrast, is a QUESTION rather than a wait,
and answers `False` exactly as CPython does.

`Thread` RUNS ITS TARGET, on this thread, at `start()`. That is the one
place the answers differ and it is worth being blunt about which way:
CPython's `start()` returns before the target necessarily runs, so
`t.start(); t.is_alive()` is a RACE there and is deterministically
`False` here; and two threads' output interleaves there and is strictly
sequential here. What holds either way -- and is what a program actually
checks -- is that the target ran exactly once with the arguments given,
that `join()` returns only after it has, that `is_alive()` is False
before `start()` and after `join()`, and that an exception in the target
is reported and does not propagate to the starter.

NOT COVERED, each refused BY NAME rather than approximated: `Timer`
(needs a scheduler to fire later, not just a clock), `settrace`,
`setprofile`, `stack_size`, `excepthook` as a replaceable hook,
`get_native_id`, `enumerate()` of threads other than the main one and
whatever `Thread` objects exist, and `Thread` subclass daemon semantics
at interpreter exit (nothing outlives the program here, so a daemon and
a non-daemon end identically).
"""


class ThreadError(Exception):
    pass


class BrokenBarrierError(RuntimeError):
    pass


#: WHY A RUNTIMEERROR AND NOT A HANG. See the module docstring: the
#: operation's only single-threaded outcome is to wait for a thread that
#: cannot exist, and CPython deadlocks there. Saying so is strictly more
#: useful than reproducing the hang, and no correct program depends on
#: the difference.
_DEADLOCK = ("this runtime has one thread, so this call could only wait "
             "for a thread that cannot exist (CPython would deadlock here)")


def _fail_deadlock(what):
    raise RuntimeError(what + ": " + _DEADLOCK)


class Lock:
    """A primitive lock: two states and no owner.

    NOT REENTRANT, and that is the whole difference from `RLock`: a
    thread that acquires it twice deadlocks in CPython, which is why the
    second acquire raises here rather than succeeding. Getting this
    wrong in the permissive direction would make a program that is
    BROKEN under real threads pass here.
    """

    def __init__(self):
        self._locked = False

    def acquire(self, blocking=True, timeout=-1):
        if timeout != -1 and not blocking:
            raise ValueError("can't specify a timeout for a non-blocking call")
        if not self._locked:
            self._locked = True
            return True
        if not blocking:
            return False
        if timeout > 0:
            # A TIMEOUT THAT CAN ONLY EXPIRE. Nothing can release the lock
            # while this call waits, so CPython's answer after `timeout`
            # seconds is False and so is this one -- without the wait,
            # which is the only part a program can tell apart and the part
            # it would rather not have.
            return False
        _fail_deadlock("Lock.acquire on a lock this thread already holds")

    def release(self):
        if not self._locked:
            raise RuntimeError("release unlocked lock")
        self._locked = False

    def locked(self):
        return self._locked

    def __enter__(self):
        self.acquire()
        return True

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def __repr__(self):
        state = "unlocked"
        if self._locked:
            state = "locked"
        return "<%s _thread.lock object at %s>" % (state, hex(id(self)))


def allocate_lock():
    """`_thread.allocate_lock`, re-exported as CPython's `threading` does."""
    return Lock()


class RLock:
    """A reentrant lock: the owner may acquire it again, and must release
    it as many times as it acquired it.

    THE OWNER CHECK IS REAL even with one thread, because the error it
    produces is: `release()` on a lock nobody holds is a `RuntimeError`
    in CPython and has to be one here, or a program with unbalanced
    acquire/release passes.
    """

    def __init__(self):
        self._count = 0

    def acquire(self, blocking=True, timeout=-1):
        # ALWAYS THE OWNER, because there is one thread. So an acquire
        # never blocks and never fails, which is exactly CPython's answer
        # for a reentrant lock the calling thread already holds.
        self._count = self._count + 1
        return True

    def release(self):
        if self._count == 0:
            raise RuntimeError("cannot release un-acquired lock")
        self._count = self._count - 1

    def locked(self):
        return self._count > 0

    def _is_owned(self):
        return self._count > 0

    def __enter__(self):
        self.acquire()
        return True

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False

    def __repr__(self):
        state = "unlocked"
        if self._count:
            state = "locked"
        return "<%s _thread.RLock object owner=%d count=%d at %s>" % (
            state, 0, self._count, hex(id(self)))


class Semaphore:
    """A counter that cannot go below zero."""

    def __init__(self, value=1):
        if value < 0:
            raise ValueError("semaphore initial value must be >= 0")
        self._value = value

    def acquire(self, blocking=True, timeout=None):
        if timeout is not None and not blocking:
            raise ValueError("can't specify timeout for non-blocking acquire")
        if self._value > 0:
            self._value = self._value - 1
            return True
        if not blocking:
            return False
        if timeout is not None and timeout > 0:
            return False
        _fail_deadlock("Semaphore.acquire on an exhausted semaphore")

    def release(self, n=1):
        if n < 1:
            raise ValueError("n must be one or more")
        self._value = self._value + n

    def __enter__(self):
        self.acquire()
        return True

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class BoundedSemaphore:
    """A `Semaphore` that refuses to be released above its initial value.

    THE POINT IS THE BUG IT CATCHES -- a release with no matching
    acquire -- so the `ValueError` is the feature and is worded exactly
    as CPython words it.
    """

    def __init__(self, value=1):
        if value < 0:
            raise ValueError("semaphore initial value must be >= 0")
        self._value = value
        self._initial = value

    def acquire(self, blocking=True, timeout=None):
        if timeout is not None and not blocking:
            raise ValueError("can't specify timeout for non-blocking acquire")
        if self._value > 0:
            self._value = self._value - 1
            return True
        if not blocking:
            return False
        if timeout is not None and timeout > 0:
            return False
        _fail_deadlock("BoundedSemaphore.acquire on an exhausted semaphore")

    def release(self, n=1):
        if n < 1:
            raise ValueError("n must be one or more")
        if self._value + n > self._initial:
            raise ValueError("Semaphore released too many times")
        self._value = self._value + n

    def __enter__(self):
        self.acquire()
        return True

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class Event:
    """A flag one thread sets and others wait on.

    EXACT ON ONE THREAD, all of it. `wait()` on a SET event returns True
    immediately in CPython, and `wait(timeout)` on a clear one returns
    False after the timeout -- neither needs a second thread, and both
    are the answers here. Only `wait()` with no timeout on a clear event
    has nothing to wait for; see `_DEADLOCK`.
    """

    def __init__(self):
        self._flag = False

    def is_set(self):
        return self._flag

    def isSet(self):
        return self._flag

    def set(self):
        self._flag = True

    def clear(self):
        self._flag = False

    def wait(self, timeout=None):
        if self._flag:
            return True
        if timeout is None:
            _fail_deadlock("Event.wait on an event nothing can set")
        # THE ANSWER A TIMED WAIT GIVES IS ITS RETURN VALUE, and here it
        # can only ever be False: nothing runs between this call and its
        # own timeout. Returning it without sleeping is the same answer
        # sooner.
        return False


class Condition:
    """A lock plus a waiting room.

    THE LOCK HALF IS EXACT: `with cond:`, `acquire`, `release`, and the
    `RuntimeError` for calling `wait` or `notify` without holding it --
    which is a real bug detector and the most common way to misuse one.
    The WAITING half needs a second thread; `notify`/`notify_all` are
    no-ops that still check the lock, because notifying an empty waiting
    room is legal and does nothing in CPython too.
    """

    def __init__(self, lock=None):
        if lock is None:
            lock = RLock()
        self._lock = lock
        self.acquire = lock.acquire
        self.release = lock.release

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()
        return False

    def _check_owned(self, what):
        held = getattr(self._lock, "_count", None)
        if held is None:
            held = 1 if self._lock.locked() else 0
        if not held:
            raise RuntimeError("cannot " + what + " on un-acquired lock")

    def wait(self, timeout=None):
        self._check_owned("wait")
        if timeout is None:
            _fail_deadlock("Condition.wait with nothing that can notify")
        return False

    def wait_for(self, predicate, timeout=None):
        # THE PREDICATE IS CHECKED FIRST, as CPython checks it first: a
        # condition that already holds returns its value without waiting,
        # and that is the case a single-threaded program can actually be
        # in.
        result = predicate()
        if result:
            return result
        return self.wait(timeout)

    def notify(self, n=1):
        self._check_owned("notify")

    def notify_all(self):
        self._check_owned("notify")

    def notifyAll(self):
        self.notify_all()


class Barrier:
    """A rendezvous for `parties` threads.

    `parties == 1` IS THE WHOLE SINGLE-THREADED CASE AND IT WORKS: one
    thread arrives, the barrier trips, `wait()` returns 0 and the
    generation advances -- exactly CPython. More parties than there are
    threads can only wait forever; see `_DEADLOCK`.
    """

    def __init__(self, parties, action=None, timeout=None):
        if parties < 1:
            raise ValueError("parties must be >= 1")
        self._parties = parties
        self._action = action
        self._timeout = timeout
        self._count = 0
        self._broken = False

    def wait(self, timeout=None):
        if self._broken:
            raise BrokenBarrierError("barrier is broken")
        if self._parties != 1:
            _fail_deadlock("Barrier.wait for %d parties" % (self._parties,))
        if self._action is not None:
            self._action()
        # THE LAST ARRIVAL GETS 0, and with one party every arrival is
        # the last one. CPython hands each thread a distinct index in
        # `range(parties)` and there is only one to hand out.
        return 0

    def reset(self):
        self._count = 0
        self._broken = False

    def abort(self):
        self._broken = True

    @property
    def parties(self):
        return self._parties

    @property
    def n_waiting(self):
        return self._count

    @property
    def broken(self):
        return self._broken


class local:
    """Thread-local storage.

    ONE THREAD MEANS ONE NAMESPACE, so this is an ordinary attribute bag
    -- and that is not an approximation, it is what thread-local storage
    IS when there is one thread. Every answer a program can read from it
    (an attribute set is an attribute got, one never set is an
    `AttributeError`) is CPython's.
    """

    def __getattr__(self, name):
        raise AttributeError("'_thread._local' object has no attribute "
                             + repr(name))


class Thread:
    """A target to run. See the module docstring for what differs.

    RUN AT `start()`, ON THIS THREAD. Everything else about the object --
    the name, the identity, `is_alive` before and after, `join`
    returning only once the target has finished, an exception being
    reported rather than propagated -- is what CPython's is.
    """

    _counter = [0]

    def __init__(self, group=None, target=None, name=None, args=(),
                 kwargs=None, daemon=None):
        if group is not None:
            raise ValueError("group argument must be None for now")
        self._target = target
        self._args = tuple(args)
        self._kwargs = dict(kwargs) if kwargs is not None else {}
        Thread._counter[0] = Thread._counter[0] + 1
        self._number = Thread._counter[0]
        if name is None:
            name = "Thread-" + str(self._number)
        self._name = str(name)
        self._daemon = bool(daemon) if daemon is not None else False
        self._started = False
        self._finished = False
        self._ident = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = str(value)

    @property
    def daemon(self):
        return self._daemon

    @daemon.setter
    def daemon(self, value):
        if self._started:
            raise RuntimeError("cannot set daemon status of active thread")
        self._daemon = bool(value)

    @property
    def ident(self):
        return self._ident

    @property
    def native_id(self):
        return self._ident

    def is_alive(self):
        return self._started and not self._finished

    def isAlive(self):
        return self.is_alive()

    def run(self):
        """What `start()` calls. A subclass overrides THIS, not `start`,
        and `Thread(target=...)` is the version that calls the target."""
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def start(self):
        if self._started:
            raise RuntimeError("threads can only be started once")
        self._started = True
        # ONE IDENT PER THREAD OBJECT, and distinct from the main
        # thread's -- a program that keys a dict on `t.ident` gets
        # distinct keys, which is the property `ident` is used for.
        self._ident = 1000 + self._number
        try:
            self.run()
        except BaseException as exc:
            # REPORTED AND SWALLOWED, exactly as CPython's `_bootstrap`
            # does: an exception in a thread does not reach whoever
            # called `start()`, it goes to `threading.excepthook` and
            # the starter carries on. Printing it and continuing is that
            # behaviour; the hook itself is not replaceable here.
            print("Exception in thread " + self._name + ":")
            print(type(exc).__name__ + ": " + str(exc))
        self._finished = True

    def join(self, timeout=None):
        if not self._started:
            raise RuntimeError("cannot join thread before it is started")
        return None

    def __repr__(self):
        state = "initial"
        if self._finished:
            state = "stopped"
        elif self._started:
            state = "started"
        ident = ""
        if self._ident is not None:
            ident = " " + str(self._ident)
        return "<Thread(%s,%s%s)>" % (self._name, ident, " " + state)


class _MainThread(Thread):
    """The thread the program is already on.

    STARTED AND ALIVE FROM THE BEGINNING, which is what makes
    `current_thread().is_alive()` True the way CPython's is -- it is not
    waiting to be started, it is the one running.
    """

    def __init__(self):
        Thread.__init__(self, name="MainThread")
        self._started = True
        self._finished = False
        self._ident = 1

    def start(self):
        raise RuntimeError("threads can only be started once")

    def join(self, timeout=None):
        raise RuntimeError("cannot join current thread")


_MAIN = _MainThread()


def current_thread():
    """Always the main thread: a `Thread`'s target runs on it too, so
    there is never a moment when anything else is current."""
    return _MAIN


def currentThread():
    return _MAIN


def main_thread():
    return _MAIN


def active_count():
    """1. A `Thread` here has finished by the time `start()` returns, so
    the main thread is the only one ever active."""
    return 1


def activeCount():
    return 1


def enumerate():
    return [_MAIN]


def get_ident():
    return 1


def stack_size(size=None):
    raise RuntimeError("threading.stack_size is not available: there is one "
                       "thread and no stack to size -- see the module "
                       "docstring")


def settrace(func):
    raise RuntimeError("threading.settrace is not available: there is no "
                       "second thread to trace -- see the module docstring")


def setprofile(func):
    raise RuntimeError("threading.setprofile is not available: there is no "
                       "second thread to profile -- see the module docstring")


TIMEOUT_MAX = 4294967.0
