"""getopt module: C-style command-line option parser (GNU-compatible).

Implements getopt() and gnu_getopt() matching CPython's getopt module.
Also provides GetoptError as the exception class.

Usage::

    import sys
    import getopt

    opts, args = getopt.getopt(sys.argv[1:], "hvf:", ["help", "verbose", "file="])
    for opt, val in opts:
        if opt in ("-h", "--help"):
            print("usage ...")
        elif opt in ("-f", "--file"):
            filename = val
"""
from __future__ import annotations


class GetoptError(Exception):
    """Raised when an unrecognised option or missing argument is detected."""

    def __init__(self, msg: str = "", opt: str = "") -> None:
        self.msg: str = msg
        self.opt: str = opt

    def __str__(self) -> str:
        return self.msg


# Alias used by CPython callers.
error = GetoptError


def _has_colon(s: str) -> int:
    """Return 1 if s ends with ':', 0 otherwise."""
    if len(s) == 0:
        return 0
    return 1 if s[len(s) - 1] == ":" else 0


def _strip_colon(s: str) -> str:
    """Return s with a trailing ':' removed (if present)."""
    if len(s) > 0 and s[len(s) - 1] == ":":
        return s[0:len(s) - 1]
    return s


def _has_equal(s: str) -> int:
    """Return 1 if s ends with '=', 0 otherwise."""
    if len(s) == 0:
        return 0
    return 1 if s[len(s) - 1] == "=" else 0


def _strip_equal(s: str) -> str:
    """Return s with a trailing '=' removed (if present)."""
    if len(s) > 0 and s[len(s) - 1] == "=":
        return s[0:len(s) - 1]
    return s


def _starts_with(s: str, prefix: str) -> int:
    """Return 1 if s starts with prefix."""
    plen: int = len(prefix)
    if len(s) < plen:
        return 0
    i: int = 0
    while i < plen:
        if s[i] != prefix[i]:
            return 0
        i = i + 1
    return 1


def getopt(args: list, shortopts: str, longopts: list = []) -> list:
    """Parse command-line options.

    args       -- list of option strings (typically sys.argv[1:])
    shortopts  -- string of single-char options; options requiring an argument
                  are followed by ':' (e.g. "hvf:")
    longopts   -- list of long option strings; options requiring an argument
                  are followed by '=' (e.g. ["help", "verbose", "file="])

    Returns a list [[opts_list, non_opts_list]] where opts_list is a list
    of [option, value] pairs and non_opts_list contains the remaining args.
    Options that take no argument have value "".

    Raises GetoptError on unrecognised options or missing arguments.
    """
    opts: list = []
    non_opts: list = []
    i: int = 0
    n: int = len(args)
    while i < n:
        arg: str = args[i]
        if arg == "--":
            # End of options; everything after is positional.
            i = i + 1
            while i < n:
                non_opts.append(args[i])
                i = i + 1
            break
        if _starts_with(arg, "--"):
            # Long option.
            opt_text: str = arg[2:]
            # Check for embedded '='.
            eq_pos: int = -1
            j: int = 0
            while j < len(opt_text):
                if opt_text[j] == "=":
                    eq_pos = j
                    break
                j = j + 1
            if eq_pos >= 0:
                opt_name: str = opt_text[0:eq_pos]
                opt_val: str = opt_text[eq_pos + 1:]
                has_val: int = 1
            else:
                opt_name = opt_text
                opt_val = ""
                has_val = 0
            # Find matching long opt.
            matched: str = ""
            k: int = 0
            while k < len(longopts):
                lo: str = longopts[k]
                lo_name: str = _strip_equal(lo)
                lo_needs: int = _has_equal(lo)
                if lo_name == opt_name:
                    matched = lo
                    if lo_needs and not has_val:
                        # Value in next arg.
                        i = i + 1
                        if i >= n:
                            raise GetoptError("option --" + opt_name + " requires argument", "--" + opt_name)
                        opt_val = args[i]
                    elif not lo_needs and has_val:
                        raise GetoptError("option --" + opt_name + " must not have an argument", "--" + opt_name)
                    opts.append(["--" + opt_name, opt_val])
                    break
                k = k + 1
            if len(matched) == 0:
                raise GetoptError("option --" + opt_name + " not recognized", "--" + opt_name)
        elif _starts_with(arg, "-") and len(arg) > 1:
            # Short option cluster (e.g. "-abc" = "-a -b -c").
            ci: int = 1
            while ci < len(arg):
                ch: str = arg[ci]
                # Find ch in shortopts.
                si: int = 0
                found_short: int = 0
                while si < len(shortopts):
                    if shortopts[si] == ch:
                        needs_arg: int = 0
                        if si + 1 < len(shortopts) and shortopts[si + 1] == ":":
                            needs_arg = 1
                        if needs_arg:
                            # Remainder of this cluster OR next argv.
                            if ci + 1 < len(arg):
                                sv: str = arg[ci + 1:]
                                ci = len(arg)  # consume rest
                            else:
                                i = i + 1
                                if i >= n:
                                    raise GetoptError("option -" + ch + " requires argument", "-" + ch)
                                sv = args[i]
                                ci = len(arg)
                            opts.append(["-" + ch, sv])
                        else:
                            opts.append(["-" + ch, ""])
                        found_short = 1
                        break
                    si = si + 1
                if not found_short:
                    raise GetoptError("option -" + ch + " not recognized", "-" + ch)
                ci = ci + 1
        else:
            non_opts.append(arg)
        i = i + 1
    return [opts, non_opts]


def gnu_getopt(args: list, shortopts: str, longopts: list = []) -> list:
    """Like getopt(), but allows options to appear after non-option args.

    Non-option arguments encountered before '--' are collected alongside
    options rather than terminating option processing.
    """
    opts: list = []
    non_opts: list = []
    i: int = 0
    n: int = len(args)
    while i < n:
        arg: str = args[i]
        if arg == "--":
            i = i + 1
            while i < n:
                non_opts.append(args[i])
                i = i + 1
            break
        if _starts_with(arg, "--"):
            # Delegate to getopt for this single arg.
            sub: list = getopt([arg], "", longopts)
            sub_opts: list = sub[0]
            for pair in sub_opts:
                opts.append(pair)
        elif _starts_with(arg, "-") and len(arg) > 1:
            sub2: list = getopt([arg], shortopts, longopts)
            sub_opts2: list = sub2[0]
            for pair2 in sub_opts2:
                opts.append(pair2)
        else:
            non_opts.append(arg)
        i = i + 1
    return [opts, non_opts]
