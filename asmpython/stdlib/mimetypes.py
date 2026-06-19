"""mimetypes: map file extensions to MIME content types.

Usage::

    import mimetypes

    ctype, encoding = mimetypes.guess_type("image.png")
    # => ("image/png", None)

    ext = mimetypes.guess_extension("text/html")
    # => ".html"

    exts = mimetypes.guess_all_extensions("text/html")
    # => [".htm", ".html", ...]
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Extension -> (type, encoding) mapping
# Covers the most common IANA media types.
# ---------------------------------------------------------------------------

_types_map: dict = {}
_encodings_map: dict = {}
_suffix_map: dict = {}
_common_types: dict = {}


def _init_tables() -> None:
    # Text types
    _types_map[".txt"] = "text/plain"
    _types_map[".htm"] = "text/html"
    _types_map[".html"] = "text/html"
    _types_map[".css"] = "text/css"
    _types_map[".csv"] = "text/csv"
    _types_map[".tsv"] = "text/tab-separated-values"
    _types_map[".xml"] = "text/xml"
    _types_map[".xsl"] = "text/xsl"
    _types_map[".ics"] = "text/calendar"
    _types_map[".vcf"] = "text/vcard"
    _types_map[".md"] = "text/markdown"
    _types_map[".rst"] = "text/x-rst"
    _types_map[".rtf"] = "text/rtf"
    _types_map[".js"] = "text/javascript"
    _types_map[".mjs"] = "text/javascript"
    _types_map[".py"] = "text/x-python"
    _types_map[".sh"] = "text/x-shellscript"
    _types_map[".bat"] = "text/x-batch"
    _types_map[".ps1"] = "text/x-powershell"

    # Image types
    _types_map[".png"] = "image/png"
    _types_map[".jpg"] = "image/jpeg"
    _types_map[".jpeg"] = "image/jpeg"
    _types_map[".gif"] = "image/gif"
    _types_map[".bmp"] = "image/bmp"
    _types_map[".ico"] = "image/x-icon"
    _types_map[".svg"] = "image/svg+xml"
    _types_map[".webp"] = "image/webp"
    _types_map[".tif"] = "image/tiff"
    _types_map[".tiff"] = "image/tiff"
    _types_map[".avif"] = "image/avif"
    _types_map[".heic"] = "image/heic"
    _types_map[".psd"] = "image/vnd.adobe.photoshop"

    # Audio types
    _types_map[".mp3"] = "audio/mpeg"
    _types_map[".wav"] = "audio/wav"
    _types_map[".ogg"] = "audio/ogg"
    _types_map[".flac"] = "audio/flac"
    _types_map[".aac"] = "audio/aac"
    _types_map[".m4a"] = "audio/mp4"
    _types_map[".opus"] = "audio/opus"
    _types_map[".mid"] = "audio/midi"
    _types_map[".midi"] = "audio/midi"
    _types_map[".weba"] = "audio/webm"

    # Video types
    _types_map[".mp4"] = "video/mp4"
    _types_map[".mpeg"] = "video/mpeg"
    _types_map[".mpg"] = "video/mpeg"
    _types_map[".avi"] = "video/x-msvideo"
    _types_map[".mov"] = "video/quicktime"
    _types_map[".webm"] = "video/webm"
    _types_map[".mkv"] = "video/x-matroska"
    _types_map[".flv"] = "video/x-flv"
    _types_map[".ogv"] = "video/ogg"
    _types_map[".ts"] = "video/mp2t"
    _types_map[".m4v"] = "video/mp4"

    # Application types
    _types_map[".json"] = "application/json"
    _types_map[".pdf"] = "application/pdf"
    _types_map[".zip"] = "application/zip"
    _types_map[".gz"] = "application/gzip"
    _types_map[".bz2"] = "application/x-bzip2"
    _types_map[".xz"] = "application/x-xz"
    _types_map[".tar"] = "application/x-tar"
    _types_map[".rar"] = "application/vnd.rar"
    _types_map[".7z"] = "application/x-7z-compressed"
    _types_map[".wasm"] = "application/wasm"
    _types_map[".doc"] = "application/msword"
    _types_map[".docx"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    _types_map[".xls"] = "application/vnd.ms-excel"
    _types_map[".xlsx"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    _types_map[".ppt"] = "application/vnd.ms-powerpoint"
    _types_map[".pptx"] = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    _types_map[".odt"] = "application/vnd.oasis.opendocument.text"
    _types_map[".ods"] = "application/vnd.oasis.opendocument.spreadsheet"
    _types_map[".odp"] = "application/vnd.oasis.opendocument.presentation"
    _types_map[".epub"] = "application/epub+zip"
    _types_map[".apk"] = "application/vnd.android.package-archive"
    _types_map[".swf"] = "application/x-shockwave-flash"
    _types_map[".exe"] = "application/x-msdownload"
    _types_map[".dll"] = "application/x-msdownload"
    _types_map[".so"] = "application/x-sharedlib"
    _types_map[".dmg"] = "application/x-apple-diskimage"
    _types_map[".deb"] = "application/x-debian-package"
    _types_map[".rpm"] = "application/x-rpm"
    _types_map[".jar"] = "application/java-archive"
    _types_map[".war"] = "application/java-archive"
    _types_map[".ear"] = "application/java-archive"

    # Font types
    _types_map[".ttf"] = "font/ttf"
    _types_map[".otf"] = "font/otf"
    _types_map[".woff"] = "font/woff"
    _types_map[".woff2"] = "font/woff2"
    _types_map[".eot"] = "application/vnd.ms-fontobject"

    # Content-encoding
    _encodings_map[".gz"] = "gzip"
    _encodings_map[".br"] = "br"
    _encodings_map[".zst"] = "zstd"

    # Suffix map (e.g. .tar.gz -> .gz)
    _suffix_map[".tgz"] = ".tar.gz"
    _suffix_map[".tbz2"] = ".tar.bz2"
    _suffix_map[".txz"] = ".tar.xz"


_init_tables()


def guess_type(url: str, strict: int = 1) -> list:
    """Guess the type of a file based on its URL/filename.

    Returns [type, encoding] where type is 'major/minor' or None,
    and encoding is 'gzip', 'br', etc. or None (represented as '').
    """
    scheme_end: int = url.find("://")
    path: str = url
    if scheme_end >= 0:
        q: int = url.find("?", scheme_end)
        if q >= 0:
            path = url[scheme_end + 3:q]
        else:
            path = url[scheme_end + 3:]

    # Handle suffix map (e.g. .tgz -> .tar.gz)
    base: str = path
    enc: str = ""

    for suffix in _suffix_map:
        if base.endswith(suffix):
            base = base[:len(base) - len(suffix)] + _suffix_map[suffix]
            break

    # Check for encoding extension
    dot: int = base.rfind(".")
    if dot >= 0:
        ext: str = base[dot:]
        if ext in _encodings_map:
            enc = _encodings_map[ext]
            base = base[:dot]

    # Get MIME type from remaining extension
    dot = base.rfind(".")
    if dot >= 0:
        ext = base[dot:].lower()
        if ext in _types_map:
            return [_types_map[ext], enc]

    return ["", enc]


def guess_extension(ctype: str, strict: int = 1) -> str:
    """Guess the extension for a file based on its MIME type.

    Returns the first matching extension (with leading dot), or ''.
    """
    ctype = ctype.lower()
    for ext in _types_map:
        if _types_map[ext] == ctype:
            return ext
    return ""


def guess_all_extensions(ctype: str, strict: int = 1) -> list:
    """Return all known extensions for a MIME type."""
    ctype = ctype.lower()
    result: list = []
    for ext in _types_map:
        if _types_map[ext] == ctype:
            result.append(ext)
    return result


def add_type(ctype: str, ext: str, strict: int = 1) -> None:
    """Add a mapping from extension *ext* to MIME type *ctype*."""
    if not ext.startswith("."):
        ext = "." + ext
    _types_map[ext.lower()] = ctype.lower()


def read_mime_types(filename: str) -> dict:
    """Read a MIME types file (Apache format) and return a dict of ext -> type."""
    result: dict = {}
    if not __import__("os").path.exists(filename):
        return result
    f = open(filename, "r")
    content: str = f.read()
    f.close()
    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts: list = line.split()
        if len(parts) < 2:
            continue
        ctype: str = parts[0]
        i: int = 1
        while i < len(parts):
            ext: str = parts[i]
            if not ext.startswith("."):
                ext = "." + ext
            result[ext.lower()] = ctype.lower()
            i = i + 1
    return result


def init(files: list = []) -> None:
    """Re-initialize the module (no-op; built-in table is always loaded)."""
    pass


# Module-level aliases
types_map: dict = _types_map
encodings_map: dict = _encodings_map
suffix_map: dict = _suffix_map
common_types: dict = _common_types
