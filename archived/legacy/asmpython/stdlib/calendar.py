"""calendar module: calendar operations."""
from __future__ import annotations


MONDAY: int = 0
TUESDAY: int = 1
WEDNESDAY: int = 2
THURSDAY: int = 3
FRIDAY: int = 4
SATURDAY: int = 5
SUNDAY: int = 6

day_name: list[str] = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]
day_abbr: list[str] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

month_name: list[str] = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]
month_abbr: list[str] = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]


def isleap(year: int) -> int:
    """Return True if year is a leap year."""
    if year % 400 == 0:
        return 1
    if year % 100 == 0:
        return 0
    if year % 4 == 0:
        return 1
    return 0


def leapdays(y1: int, y2: int) -> int:
    """Return number of leap years in range [y1, y2)."""
    y1 = y1 - 1
    y2 = y2 - 1
    return (y2 // 4 - y1 // 4) - (y2 // 100 - y1 // 100) + (y2 // 400 - y1 // 400)


def _days_in_month(year: int, month: int) -> int:
    if month in [1, 3, 5, 7, 8, 10, 12]:
        return 31
    if month in [4, 6, 9, 11]:
        return 30
    if month == 2:
        if isleap(year):
            return 29
        return 28
    return 30


def weekday(year: int, month: int, day: int) -> int:
    """Return weekday (0=Monday) for given date."""
    if month < 3:
        month = month + 12
        year = year - 1
    k: int = year % 100
    j: int = year // 100
    h: int = (day + (13 * (month + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7
    dow: int = (h + 5) % 7
    return dow


def monthrange(year: int, month: int) -> list[int]:
    """Return [weekday_of_first_day, number_of_days] for given year/month."""
    first_day: int = weekday(year, month, 1)
    days: int = _days_in_month(year, month)
    return [first_day, days]


def monthcalendar(year: int, month: int) -> list[list[int]]:
    """Return matrix of weeks for given year/month (0 = not in month)."""
    info: list[int] = monthrange(year, month)
    first_weekday: int = info[0]
    num_days: int = info[1]
    weeks: list[list[int]] = []
    week: list[int] = [0, 0, 0, 0, 0, 0, 0]
    day: int = 1
    col: int = first_weekday
    while day <= num_days:
        week[col] = day
        col = col + 1
        if col == 7:
            weeks.append(week)
            week = [0, 0, 0, 0, 0, 0, 0]
            col = 0
        day = day + 1
    if col > 0:
        weeks.append(week)
    return weeks


def _pad_int(n: int, width: int) -> str:
    s: str = str(n)
    while len(s) < width:
        s = " " + s
    return s


def month(theyear: int, themonth: int, w: int = 2, l: int = 1) -> str:
    """Return a month's calendar as a multi-line string."""
    header: str = month_name[themonth] + " " + str(theyear)
    header_line: str = header.center(7 * (w + 1) - 1)
    day_header: str = ""
    i: int = 0
    while i < 7:
        abbr: str = day_abbr[i]
        if i > 0:
            day_header = day_header + " "
        day_header = day_header + abbr[:w].rjust(w)
        i = i + 1
    lines: list[str] = [header_line, day_header]
    for week in monthcalendar(theyear, themonth):
        line: str = ""
        j: int = 0
        while j < 7:
            if j > 0:
                line = line + " "
            if week[j] == 0:
                line = line + " " * w
            else:
                line = line + _pad_int(week[j], w)
            j = j + 1
        lines.append(line)
    sep: str = "\n"
    i = 0
    while i < l - 1:
        sep = sep + "\n"
        i = i + 1
    return sep.join(lines)


def prcal(year: int, w: int = 2, l: int = 1, c: int = 6) -> None:
    """Print a year's calendar."""
    print(str(year).center(7 * (w + 1) * 3 + c * 2 - 1))
    m: int = 1
    while m <= 12:
        m1: str = month(year, m, w, l)
        m2: str = month(year, m + 1, w, l)
        m3: str = month(year, m + 2, w, l)
        lines1: list[str] = m1.split("\n")
        lines2: list[str] = m2.split("\n")
        lines3: list[str] = m3.split("\n")
        max_rows: int = len(lines1)
        if len(lines2) > max_rows:
            max_rows = len(lines2)
        if len(lines3) > max_rows:
            max_rows = len(lines3)
        col_width: int = 7 * (w + 1) - 1
        i = 0
        while i < max_rows:
            s1: str = lines1[i] if i < len(lines1) else ""
            s2: str = lines2[i] if i < len(lines2) else ""
            s3: str = lines3[i] if i < len(lines3) else ""
            row: str = s1.ljust(col_width) + " " * c + s2.ljust(col_width) + " " * c + s3
            print(row)
            i = i + 1
        m = m + 3


def prmonth(theyear: int, themonth: int, w: int = 2, l: int = 1) -> None:
    """Print a single month's calendar."""
    print(month(theyear, themonth, w, l))


def timegm(t: list[int]) -> int:
    """Convert a time tuple (UTC) to Unix timestamp."""
    year: int = t[0]
    mon: int = t[1]
    day: int = t[2]
    hour: int = t[3]
    minute: int = t[4]
    sec: int = t[5]
    days: int = 0
    y: int = 1970
    while y < year:
        if isleap(y):
            days = days + 366
        else:
            days = days + 365
        y = y + 1
    mdays: list[int] = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if isleap(year):
        mdays[2] = 29
    m: int = 1
    while m < mon:
        days = days + mdays[m]
        m = m + 1
    days = days + day - 1
    return days * 86400 + hour * 3600 + minute * 60 + sec


class TextCalendar:
    """Text calendar."""

    def __init__(self, firstweekday: int = 0) -> None:
        self.firstweekday: int = firstweekday

    def formatmonth(self, theyear: int, themonth: int, w: int = 2, l: int = 1) -> str:
        return month(theyear, themonth, w, l)

    def prmonth(self, theyear: int, themonth: int, w: int = 2, l: int = 1) -> None:
        prmonth(theyear, themonth, w, l)

    def formatyear(self, theyear: int, w: int = 2, l: int = 1, c: int = 6) -> str:
        lines: list[str] = []
        m: int = 1
        while m <= 12:
            lines.append(month(theyear, m, w, l))
            m = m + 1
        return "\n\n".join(lines)

    def pryear(self, theyear: int, w: int = 2, l: int = 1, c: int = 6) -> None:
        prcal(theyear, w, l, c)


class HTMLCalendar:
    """HTML calendar generator."""

    def __init__(self, firstweekday: int = 0) -> None:
        self.firstweekday: int = firstweekday

    def formatmonth(self, theyear: int, themonth: int, withyear: int = 1) -> str:
        title: str = month_name[themonth]
        if withyear:
            title = title + " " + str(theyear)
        html: str = '<table border="0" cellpadding="0" cellspacing="0" class="month">\n'
        html = html + '<tr><th colspan="7" class="month">' + title + "</th></tr>\n"
        html = html + "<tr>"
        i: int = 0
        while i < 7:
            html = html + '<th class="' + day_abbr[i].lower() + '">' + day_abbr[i] + "</th>"
            i = i + 1
        html = html + "</tr>\n"
        for week in monthcalendar(theyear, themonth):
            html = html + "<tr>"
            j: int = 0
            while j < 7:
                if week[j] == 0:
                    html = html + '<td class="noday">&nbsp;</td>'
                else:
                    html = html + '<td class="day">' + str(week[j]) + "</td>"
                j = j + 1
            html = html + "</tr>\n"
        html = html + "</table>\n"
        return html
