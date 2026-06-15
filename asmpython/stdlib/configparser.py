"""configparser module: configuration file parser.

Implements an INI-style configuration file parser. Sections and keys
are stored as lists for O(n) lookup (sufficient for typical config files).
"""
from __future__ import annotations


class Error(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg
    def __str__(self) -> str:
        return "configparser.Error: " + self.msg


class NoSectionError(Error):
    def __init__(self, section: str) -> None:
        self.section: str = section
        self.msg = "No section: " + section


class DuplicateSectionError(Error):
    def __init__(self, section: str) -> None:
        self.section: str = section
        self.msg = "Section already exists: " + section


class NoOptionError(Error):
    def __init__(self, option: str, section: str) -> None:
        self.option: str = option
        self.section: str = section
        self.msg = "No option " + option + " in section " + section


class InterpolationError(Error):
    pass


class MissingSectionHeaderError(Error):
    pass


class ParsingError(Error):
    pass


class SectionProxy:
    """Proxy for a configuration section."""

    def __init__(self, parser: ConfigParser, section: str) -> None:
        self._parser: ConfigParser = parser
        self._section: str = section

    def get_option(self, option: str, fallback: str = "") -> str:
        return self._parser.get(self._section, option, fallback)

    def getint_option(self, option: str, fallback: int = 0) -> int:
        return self._parser.getint(self._section, option, fallback)

    def getfloat_option(self, option: str, fallback: int = 0) -> float:
        return self._parser.getfloat(self._section, option, fallback)

    def getboolean_option(self, option: str, fallback: int = 0) -> int:
        return self._parser.getboolean(self._section, option, fallback)

    def __contains__(self, option: str) -> int:
        return self._parser.has_option(self._section, option)


class ConfigParser:
    """INI-style configuration file parser."""

    def __init__(self, defaults: int = 0,
                 allow_no_value: int = 0) -> None:
        self._sections: list = []
        self._section_keys: list = []
        self._section_vals: list = []
        self._defaults_keys: list = []
        self._defaults_vals: list = []

    def _find_section(self, section: str) -> int:
        i: int = 0
        while i < len(self._sections):
            if self._sections[i] == section:
                return i
            i = i + 1
        return -1

    def sections(self) -> list:
        result: list = []
        i: int = 0
        while i < len(self._sections):
            result.append(self._sections[i])
            i = i + 1
        return result

    def has_section(self, section: str) -> int:
        return 1 if self._find_section(section) >= 0 else 0

    def add_section(self, section: str) -> None:
        if self._find_section(section) >= 0:
            return
        self._sections.append(section)
        self._section_keys.append([])
        self._section_vals.append([])

    def remove_section(self, section: str) -> int:
        idx: int = self._find_section(section)
        if idx < 0:
            return 0
        new_secs: list = []
        new_keys: list = []
        new_vals: list = []
        i: int = 0
        while i < len(self._sections):
            if i != idx:
                new_secs.append(self._sections[i])
                new_keys.append(self._section_keys[i])
                new_vals.append(self._section_vals[i])
            i = i + 1
        self._sections = new_secs
        self._section_keys = new_keys
        self._section_vals = new_vals
        return 1

    def has_option(self, section: str, option: str) -> int:
        idx: int = self._find_section(section)
        if idx < 0:
            return 0
        keys: list = self._section_keys[idx]
        k: int = 0
        while k < len(keys):
            if keys[k] == option:
                return 1
            k = k + 1
        return 0

    def set(self, section: str, option: str, value: str = "") -> None:
        if self._find_section(section) < 0:
            self.add_section(section)
        idx: int = self._find_section(section)
        keys: list = self._section_keys[idx]
        vals: list = self._section_vals[idx]
        k: int = 0
        while k < len(keys):
            if keys[k] == option:
                vals[k] = value
                return
            k = k + 1
        keys.append(option)
        vals.append(value)

    def get(self, section: str, option: str, fallback: str = "") -> str:
        idx: int = self._find_section(section)
        if idx < 0:
            return fallback
        keys: list = self._section_keys[idx]
        vals: list = self._section_vals[idx]
        k: int = 0
        while k < len(keys):
            if keys[k] == option:
                return vals[k]
            k = k + 1
        return fallback

    def getint(self, section: str, option: str, fallback: int = 0) -> int:
        s: str = self.get(section, option, str(fallback))
        i: int = 0
        neg: int = 0
        if len(s) > 0 and s[0] == "-":
            neg = 1
            s = s[1:]
        j: int = 0
        while j < len(s):
            if s[j] >= "0" and s[j] <= "9":
                i = i * 10 + ord(s[j]) - 48
            j = j + 1
        if neg == 1:
            i = -i
        return i

    def getfloat(self, section: str, option: str,
                 fallback: int = 0) -> float:
        s: str = self.get(section, option, "0.0")
        return float(s)

    def getboolean(self, section: str, option: str,
                   fallback: int = 0) -> int:
        s: str = self.get(section, option, "").lower()
        if s == "1" or s == "yes" or s == "true" or s == "on":
            return 1
        if s == "0" or s == "no" or s == "false" or s == "off":
            return 0
        return fallback

    def options(self, section: str) -> list:
        idx: int = self._find_section(section)
        if idx < 0:
            return []
        result: list = []
        keys: list = self._section_keys[idx]
        i: int = 0
        while i < len(keys):
            result.append(keys[i])
            i = i + 1
        return result

    def items(self, section: str) -> list:
        idx: int = self._find_section(section)
        if idx < 0:
            return []
        result: list = []
        keys: list = self._section_keys[idx]
        vals: list = self._section_vals[idx]
        i: int = 0
        while i < len(keys):
            pair: list = []
            pair.append(keys[i])
            pair.append(vals[i])
            result.append(pair)
            i = i + 1
        return result

    def remove_option(self, section: str, option: str) -> int:
        idx: int = self._find_section(section)
        if idx < 0:
            return 0
        keys: list = self._section_keys[idx]
        vals: list = self._section_vals[idx]
        new_keys: list = []
        new_vals: list = []
        found: int = 0
        k: int = 0
        while k < len(keys):
            if keys[k] == option:
                found = 1
            else:
                new_keys.append(keys[k])
                new_vals.append(vals[k])
            k = k + 1
        self._section_keys[idx] = new_keys
        self._section_vals[idx] = new_vals
        return found

    def read_string(self, string: str) -> None:
        """Parse configuration from a string."""
        current_section: str = ""
        lines: list = _split_lines(string)
        i: int = 0
        while i < len(lines):
            line: str = _strip(lines[i])
            if len(line) == 0 or line[0] == "#" or line[0] == ";":
                i = i + 1
                continue
            if line[0] == "[":
                end: int = 0
                j: int = 1
                while j < len(line):
                    if line[j] == "]":
                        end = j
                        break
                    j = j + 1
                if end > 1:
                    current_section = line[1:end]
                    if self._find_section(current_section) < 0:
                        self.add_section(current_section)
            elif "=" in line and len(current_section) > 0:
                eq_idx: int = -1
                k: int = 0
                while k < len(line):
                    if line[k] == "=":
                        eq_idx = k
                        break
                    k = k + 1
                if eq_idx > 0:
                    key: str = _strip(line[:eq_idx])
                    val: str = _strip(line[eq_idx + 1:])
                    self.set(current_section, key, val)
            i = i + 1

    def write_string(self) -> str:
        """Return configuration as a string."""
        result: str = ""
        i: int = 0
        while i < len(self._sections):
            result = result + "[" + self._sections[i] + "]\n"
            keys: list = self._section_keys[i]
            vals: list = self._section_vals[i]
            k: int = 0
            while k < len(keys):
                result = result + keys[k] + " = " + vals[k] + "\n"
                k = k + 1
            result = result + "\n"
            i = i + 1
        return result


def _split_lines(text: str) -> list:
    lines: list = []
    current: str = ""
    i: int = 0
    while i < len(text):
        if text[i] == "\n":
            lines.append(current)
            current = ""
        else:
            current = current + text[i]
        i = i + 1
    if len(current) > 0:
        lines.append(current)
    return lines


def _strip(s: str) -> str:
    start: int = 0
    end: int = len(s)
    while start < end and (s[start] == " " or s[start] == "\t"):
        start = start + 1
    while end > start and (s[end - 1] == " " or s[end - 1] == "\t"):
        end = end - 1
    return s[start:end]


RawConfigParser = ConfigParser
SafeConfigParser = ConfigParser
BasicInterpolation = ConfigParser
DEFAULTSECT: str = "DEFAULT"
MAX_INTERPOLATION_DEPTH: int = 10
