"""Running another program, over the host-service `proc` group.

THE LAST OF TIER 5, and the one `docs/STDLIB.md`'s "NEEDS THE FLOOR TO
GROW" was really about: creating a process is the largest single addition
to the platform floor of anything in the tier, and unlike `net` there was
no group already declared waiting to be implemented. `proc` is that
addition, and it is deliberately ONE operation --
`host_proc_run(argv, ...)`, which starts a program, waits for it, and
collects what it wrote.

WHY ONE OPERATION AND NOT A `Popen`. A pipe you write to while the child
writes back needs someone to read the other end, and on a runtime with
one thread there is nobody: a child that fills its stdout pipe blocks
until it is drained, and a parent blocked writing stdin will never drain
it. That is a real deadlock, not a limitation of the contract, and
CPython's own `Popen.communicate` exists precisely because doing it by
hand is easy to get wrong. So the group promises the shape that is always
safe -- run to completion, then read -- and `Popen` is refused BY NAME
rather than offered as something that hangs.

COVERAGE: `run()` with `capture_output`, `stdout=PIPE`, `stderr=PIPE`,
`stderr=STDOUT`, `text`/`encoding`, `check`, and a `list` or a `str`
command with `shell=True`; `CompletedProcess` with `.args`,
`.returncode`, `.stdout`, `.stderr` and `.check_returncode()`;
`check_output`, `check_call`, `call`, `getoutput`, `getstatusoutput`;
`CalledProcessError` (with `.returncode`, `.cmd`, `.output`/`.stdout`,
`.stderr`) and `SubprocessError`; `DEVNULL`, `PIPE`, `STDOUT`.

`shell=True` BUILDS THE SHELL'S OWN ARGV rather than passing a flag
down: `["/bin/sh", "-c", command]`, made here where a reader can see it,
so the group below never has to decide whether to involve a shell and
never has to quote a list into a string -- which is where command
injection comes from.

A SIGNAL IS A NEGATIVE RETURN CODE, as in CPython: a child killed by
signal N reports `-N`, and one that exited normally reports its status.
`127` is what a program that could not be executed reports, which is the
shell's own convention and what the child reports here.

NOT COVERED, each refused BY NAME: `Popen` and everything that needs a
live child (`communicate`, `poll`, `wait`, `terminate`, `kill`, `.pid`,
`.stdin`) -- see above; `input=` (writing to a child's stdin needs the
same live pipe); `timeout=` (there is nothing to interrupt a call that
has already blocked); `cwd=`, `env=`, `preexec_fn`, `pass_fds`,
`start_new_session`, `creationflags`; and CAPTURE ON WINDOWS, where the
group runs the child with inherited stdio because capturing there needs
`CreateProcess` and two structs whose layout a hand-written prototype
gets silently wrong -- `run(capture_output=True)` raises there rather
than answering empty output as though the child had said nothing.
"""


_HOST_ERR = -1
_HOST_ENOENT = -2
_HOST_EACCES = -3
_HOST_EINVAL = -9

#: How much of a child's output is collected. A caller cannot ask for more,
#: because the group's buffers are sized before the child runs and there is
#: no second call that could resume where the first stopped -- so this is
#: the number, and a child that writes more has its output TRUNCATED rather
#: than silently half-reported: `run()` says so by raising.
#:
#: EIGHT KILOBYTES, AND THE SIZE IS A REAL COST. Two buffers of this size
#: are marshalled through interpreter memory on every call, so a megabyte
#: made `subprocess.run(["echo", "hi"])` take two minutes under `uasm
#: run`. Eight kilobytes is more than any command a program here runs for
#: its output emits, and a program that needs more redirects to a file.
_CAPTURE_MAX = 1 << 13

PIPE = -1
STDOUT = -2
DEVNULL = -3


class SubprocessError(Exception):
    pass


class CalledProcessError(SubprocessError):
    """A non-zero exit from `check=True`, `check_call` or `check_output`."""

    def __init__(self, returncode, cmd, output=None, stderr=None):
        self.returncode = returncode
        self.cmd = cmd
        self.output = output
        self.stderr = stderr

    @property
    def stdout(self):
        """CPython'S OWN ALIAS. `output` is the historical name and
        `stdout` the modern one, and they are the same attribute -- a
        program that reads either gets the same bytes."""
        return self.output

    def __str__(self):
        # `'%s'` WITH LITERAL QUOTES, not `%r`, which is what CPython
        # writes -- so a list command reads `Command '['false']'` and not
        # `Command ['false']`. Copied from its source rather than guessed,
        # because the message is the thing a program's user sees.
        if self.returncode and self.returncode < 0:
            # CPython names the signal (`died with <Signals.SIGTERM: 15>`)
            # through the `signal` module, which this build does not have.
            # Its own fallback for a number it cannot name is used instead.
            return ("Command '%s' died with unknown signal %d."
                    % (self.cmd, -self.returncode))
        return ("Command '%s' returned non-zero exit status %d."
                % (self.cmd, self.returncode))


class TimeoutExpired(SubprocessError):
    def __init__(self, cmd, timeout, output=None, stderr=None):
        self.cmd = cmd
        self.timeout = timeout
        self.output = output
        self.stderr = stderr

    def __str__(self):
        return ("Command '%s' timed out after %s seconds"
                % (self.cmd, self.timeout))


class CompletedProcess:
    """What `run()` answers."""

    def __init__(self, args, returncode, stdout=None, stderr=None):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def check_returncode(self):
        if self.returncode:
            raise CalledProcessError(self.returncode, self.args,
                                     self.stdout, self.stderr)

    def __repr__(self):
        parts = ["args=" + repr(self.args),
                 "returncode=" + repr(self.returncode)]
        if self.stdout is not None:
            parts.append("stdout=" + repr(self.stdout))
        if self.stderr is not None:
            parts.append("stderr=" + repr(self.stderr))
        return "CompletedProcess(" + ", ".join(parts) + ")"


def _argv(args, shell):
    """The list of strings to execute.

    `shell=True` IS BUILT HERE, into the shell's own argv, so nothing
    below this line has to decide whether a shell is involved or quote a
    list into a string. See the module docstring.
    """
    if shell:
        if not isinstance(args, str):
            # CPython joins a list with spaces here and documents that it
            # is almost always a mistake. Refusing is the more useful
            # answer: a caller with a list does not need a shell.
            raise TypeError("shell=True takes the command as a str; a list "
                            "does not need a shell")
        return ["/bin/sh", "-c", args]
    if isinstance(args, str):
        return [args]
    out = []
    for one in args:
        out.append(one if isinstance(one, str) else str(one))
    if not out:
        raise ValueError("empty command")
    return out


def _pack(argv):
    """`argv` as the group wants it: NUL-separated in one buffer."""
    out = bytearray()
    for one in argv:
        out.extend(one.encode("utf-8"))
        out.append(0)
    return out


def _run_raw(argv):
    """`(status, stdout, stderr)`, straight from the host service."""
    packed = _pack(argv)
    out = bytearray(_CAPTURE_MAX)
    err = bytearray(_CAPTURE_MAX)
    status = bytearray(8)
    got = host_proc_run(packed, len(argv), len(packed),
                        out, _CAPTURE_MAX, err, _CAPTURE_MAX, status)
    if got == _HOST_ENOENT:
        raise FileNotFoundError("[Errno 2] No such file or directory: "
                                + repr(argv[0]))
    if got == _HOST_EACCES:
        raise PermissionError("[Errno 13] Permission denied: "
                              + repr(argv[0]))
    if got == _HOST_EINVAL:
        raise ValueError("subprocess: the command could not be passed to "
                         "the host (too many arguments, or one containing "
                         "a NUL)")
    if got < 0:
        raise OSError("subprocess: the child could not be started")
    if got >= _CAPTURE_MAX:
        raise OSError("subprocess: the child wrote more than %d bytes and "
                      "the rest was lost -- this build captures a fixed "
                      "buffer, see the subprocess module docstring"
                      % (_CAPTURE_MAX,))
    code = 0
    for i in range(8):
        code = code | (status[i] << (8 * i))
    if code >= (1 << 63):
        code = code - (1 << 64)
    # STDERR'S LENGTH IS NOT REPORTED by the group -- one call answers one
    # number and it is stdout's. The trailing zeroes of a buffer that was
    # not filled are stripped, which is exact for the text a program
    # writes and would only be wrong for a child emitting trailing NUL
    # bytes on stderr; nothing does.
    tail = len(err)
    while tail > 0 and err[tail - 1] == 0:
        tail = tail - 1
    return code, bytes(out[:got]), bytes(err[:tail])


def _decode(raw, text, encoding, errors):
    if not text and encoding is None:
        return raw
    return raw.decode(encoding if encoding else "utf-8",
                      errors if errors else "strict")


def run(args, stdin=None, input=None, stdout=None, stderr=None,
        capture_output=False, shell=False, cwd=None, timeout=None,
        check=False, encoding=None, errors=None, text=None,
        universal_newlines=None, env=None):
    """Run a command, wait for it, and answer a `CompletedProcess`."""
    if input is not None:
        raise ValueError("subprocess: input= needs a live pipe to the "
                         "child, which this build's one thread cannot "
                         "drain -- see the module docstring")
    if timeout is not None:
        raise ValueError("subprocess: timeout= needs something able to "
                         "interrupt a blocked call -- see the module "
                         "docstring")
    if cwd is not None or env is not None:
        raise ValueError("subprocess: cwd= and env= are not available -- "
                         "see the module docstring")
    if stdin is not None and stdin != DEVNULL:
        raise ValueError("subprocess: only stdin=DEVNULL is available -- "
                         "see the module docstring")
    argv = _argv(args, shell)
    code, raw_out, raw_err = _run_raw(argv)
    if universal_newlines is not None:
        text = universal_newlines
    wants_out = capture_output or stdout == PIPE
    wants_err = capture_output or stderr == PIPE or stderr == STDOUT
    if stderr == STDOUT:
        # MERGED INTO stdout, as CPython merges them -- the child wrote
        # them to two descriptors and a caller asking for one stream gets
        # one. The ORDER between them is the child's, and this cannot
        # preserve it: the two are collected separately, so the merge
        # appends rather than interleaves. Stated here rather than found.
        raw_out = raw_out + raw_err
        raw_err = b""
        wants_err = False
    got_out = _decode(raw_out, text, encoding, errors) if wants_out else None
    got_err = _decode(raw_err, text, encoding, errors) if wants_err else None
    if not wants_out and raw_out:
        # NOT CAPTURED MEANS THE CHILD'S OUTPUT IS THE PROGRAM'S, so it is
        # written on rather than dropped -- which is what a caller who did
        # not ask for a pipe expects to see.
        print(_decode(raw_out, True, encoding, errors), end="")
    if not wants_err and raw_err:
        print(_decode(raw_err, True, encoding, errors), end="")
    done = CompletedProcess(args, code, got_out, got_err)
    if check:
        done.check_returncode()
    return done


def call(args, **kwargs):
    """The exit status, with the child's output going where this
    program's does."""
    return run(args, **kwargs).returncode


def check_call(args, **kwargs):
    code = call(args, **kwargs)
    if code:
        raise CalledProcessError(code, args)
    return 0


def check_output(args, **kwargs):
    """The child's stdout, or `CalledProcessError` if it failed."""
    kwargs["stdout"] = PIPE
    done = run(args, **kwargs)
    if done.returncode:
        raise CalledProcessError(done.returncode, args, done.stdout,
                                 done.stderr)
    return done.stdout


def getstatusoutput(cmd, encoding=None, errors=None):
    """`(status, output)` for a SHELL command, with stderr merged in and
    a trailing newline stripped -- all three of which are CPython's own
    documented behaviour for this function rather than choices here."""
    try:
        done = run(cmd, shell=True, stdout=PIPE, stderr=STDOUT, text=True,
                   encoding=encoding, errors=errors)
        code, data = done.returncode, done.stdout
    except OSError as exc:
        code, data = 127, str(exc)
    if data and data[-1] == "\n":
        data = data[:-1]
    return code, data


def getoutput(cmd, encoding=None, errors=None):
    return getstatusoutput(cmd, encoding, errors)[1]


class Popen:
    """REFUSED BY NAME, and the docstring says why at length: a live child
    with pipes needs a second thread to drain them, and this runtime has
    one. `run()` is the shape that is always safe."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "subprocess.Popen is not available: a live child with pipes "
            "needs a thread to drain them and this runtime has one. Use "
            "subprocess.run(), which starts the child, waits for it and "
            "collects its output -- see the module docstring")
