"""ssl: TLS/SSL wrapper for socket connections.

Note: TLS requires C bindings (OpenSSL or equivalent). This module
provides the API surface but raises NotImplementedError for operations
that require the C library. It can be imported safely and constants are
accessible without raising errors.
"""
from __future__ import annotations

# Protocol constants
PROTOCOL_TLS: int = 2
PROTOCOL_TLS_CLIENT: int = 16
PROTOCOL_TLS_SERVER: int = 17
PROTOCOL_TLSv1: int = 3
PROTOCOL_TLSv1_1: int = 4
PROTOCOL_TLSv1_2: int = 5
PROTOCOL_SSLv23: int = 2  # alias for PROTOCOL_TLS

# Certificate requirements
CERT_NONE: int = 0
CERT_OPTIONAL: int = 1
CERT_REQUIRED: int = 2

# Alert levels
SSL_ERROR_ZERO_RETURN: int = 6
SSL_ERROR_WANT_READ: int = 2
SSL_ERROR_WANT_WRITE: int = 3
SSL_ERROR_SYSCALL: int = 5
SSL_ERROR_SSL: int = 1
SSL_ERROR_EOF: int = 8

# Verify modes
VERIFY_DEFAULT: int = 0
VERIFY_CRL_CHECK_LEAF: int = 4
VERIFY_CRL_CHECK_CHAIN: int = 12
VERIFY_X509_STRICT: int = 32
VERIFY_ALLOW_PROXY_CERTS: int = 64
VERIFY_X509_TRUSTED_FIRST: int = 32768

# Alert descriptions
ALERT_DESCRIPTION_CLOSE_NOTIFY: int = 0
ALERT_DESCRIPTION_UNEXPECTED_MESSAGE: int = 10
ALERT_DESCRIPTION_BAD_RECORD_MAC: int = 20
ALERT_DESCRIPTION_CERTIFICATE_EXPIRED: int = 45
ALERT_DESCRIPTION_HANDSHAKE_FAILURE: int = 40

# OP constants
OP_NO_SSLv2: int = 0x1000000
OP_NO_SSLv3: int = 0x2000000
OP_NO_TLSv1: int = 0x4000000
OP_NO_TLSv1_1: int = 0x10000000
OP_NO_TLSv1_2: int = 0x8000000
OP_NO_TLSv1_3: int = 0x20000000
OP_CIPHER_SERVER_PREFERENCE: int = 0x400000
OP_SINGLE_DH_USE: int = 0x100000
OP_NO_COMPRESSION: int = 0x20000
OP_ALL: int = 0x80000BFF

HAS_SNI: int = 1
HAS_ECDH: int = 1
HAS_NPN: int = 0
HAS_ALPN: int = 1
HAS_SSLv2: int = 0
HAS_SSLv3: int = 0
HAS_TLSv1: int = 1
HAS_TLSv1_1: int = 1
HAS_TLSv1_2: int = 1
HAS_TLSv1_3: int = 1

OPENSSL_VERSION: str = "OpenSSL 1.1.1 (stub)"
OPENSSL_VERSION_NUMBER: int = 0x1010100F
OPENSSL_VERSION_INFO: list = [1, 1, 1, 0, 15]


class SSLError(OSError):
    """Raised when an SSL-related error occurs."""

    def __init__(self, msg: str = "", errno: int = 0) -> None:
        self.msg: str = msg
        self.errno: int = errno
        self.reason: str = msg
        self.library: str = ""

    def __str__(self) -> str:
        return "SSLError: " + self.msg


class SSLZeroReturnError(SSLError):
    pass


class SSLWantReadError(SSLError):
    pass


class SSLWantWriteError(SSLError):
    pass


class SSLSyscallError(SSLError):
    pass


class SSLEOFError(SSLError):
    pass


class SSLCertVerificationError(SSLError):
    pass


class CertificateError(SSLError):
    pass


class SSLContext:
    """SSL context stub (requires C binding)."""

    def __init__(self, protocol: int = PROTOCOL_TLS) -> None:
        self.protocol: int = protocol
        self.verify_mode: int = CERT_NONE
        self.check_hostname: int = 0
        self.options: int = OP_ALL
        self.minimum_version: int = PROTOCOL_TLSv1
        self.maximum_version: int = PROTOCOL_TLS

    def load_cert_chain(self, certfile: str, keyfile: str = "",
                        password: str = "") -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def load_verify_locations(self, cafile: str = "", capath: str = "",
                               cadata: str = "") -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def load_default_certs(self, purpose: int = 0) -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def set_alpn_protocols(self, protocols: list) -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def set_npn_protocols(self, protocols: list) -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def set_ciphers(self, ciphers: str) -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def set_default_verify_paths(self) -> None:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def wrap_socket(self, sock: object, server_side: int = 0,
                    do_handshake_on_connect: int = 1,
                    suppress_ragged_eofs: int = 1,
                    server_hostname: str = "") -> object:
        raise NotImplementedError("ssl.SSLContext.wrap_socket requires C binding")

    def get_ca_certs(self, binary_form: int = 0) -> list:
        raise NotImplementedError("ssl.SSLContext requires C binding")

    def cert_store_stats(self) -> dict:
        raise NotImplementedError("ssl.SSLContext requires C binding")


class SSLSocket:
    """SSL socket stub (requires C binding)."""

    def __init__(self) -> None:
        self._closed: int = 0

    def read(self, nbytes: int = 4096) -> str:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def write(self, data: str) -> int:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def send(self, data: str) -> int:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def recv(self, nbytes: int) -> str:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def do_handshake(self) -> None:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def close(self) -> None:
        self._closed = 1

    def getpeercert(self, binary_form: int = 0) -> dict:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def cipher(self) -> list:
        raise NotImplementedError("ssl.SSLSocket requires C binding")

    def __enter__(self) -> SSLSocket:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.close()
        return 0


class SSLObject:
    """Non-socket SSL object stub."""

    def __init__(self) -> None:
        pass

    def read(self, len: int = 1024, buffer: str = "") -> str:
        raise NotImplementedError("ssl.SSLObject requires C binding")

    def write(self, data: str) -> int:
        raise NotImplementedError("ssl.SSLObject requires C binding")

    def do_handshake(self) -> None:
        raise NotImplementedError("ssl.SSLObject requires C binding")

    def unwrap(self) -> object:
        raise NotImplementedError("ssl.SSLObject requires C binding")

    def getpeercert(self, binary_form: int = 0) -> dict:
        raise NotImplementedError("ssl.SSLObject requires C binding")


def create_default_context(purpose: int = 0, cafile: str = "",
                            capath: str = "", cadata: str = "") -> SSLContext:
    """Create a default SSL context (raises NotImplementedError)."""
    raise NotImplementedError("ssl.create_default_context requires C binding")


def _create_unverified_context(protocol: int = PROTOCOL_TLS) -> SSLContext:
    """Create an unverified SSL context (raises NotImplementedError)."""
    raise NotImplementedError("ssl._create_unverified_context requires C binding")


def wrap_socket(sock: object, keyfile: str = "", certfile: str = "",
                server_side: int = 0, cert_reqs: int = CERT_NONE,
                ssl_version: int = PROTOCOL_TLS, ca_certs: str = "",
                do_handshake_on_connect: int = 1,
                suppress_ragged_eofs: int = 1,
                server_hostname: str = "") -> SSLSocket:
    """Wrap a socket with SSL (raises NotImplementedError)."""
    raise NotImplementedError("ssl.wrap_socket requires C binding")


def match_hostname(cert: dict, hostname: str) -> None:
    """Match a certificate against the expected hostname (stub)."""
    pass


def cert_time_to_seconds(cert_time: str) -> int:
    """Convert certificate time string to seconds (stub)."""
    return 0


def DER_cert_to_PEM_cert(der_cert_bytes: str) -> str:
    """Convert DER-encoded cert to PEM (raises NotImplementedError)."""
    raise NotImplementedError("ssl.DER_cert_to_PEM_cert requires C binding")


def PEM_cert_to_DER_cert(pem_cert_string: str) -> str:
    """Convert PEM cert to DER (raises NotImplementedError)."""
    raise NotImplementedError("ssl.PEM_cert_to_DER_cert requires C binding")


def get_server_certificate(addr: list, ssl_version: int = PROTOCOL_TLS,
                           ca_certs: str = "") -> str:
    """Retrieve a server certificate (raises NotImplementedError)."""
    raise NotImplementedError("ssl.get_server_certificate requires C binding")


def enum_certificates(store_name: str) -> list:
    """Enumerate certificates from the Windows cert store (stub)."""
    return []


def enum_crls(store_name: str) -> list:
    """Enumerate CRLs from the Windows cert store (stub)."""
    return []


class Purpose:
    """Constants for create_default_context's purpose parameter."""
    SERVER_AUTH: int = 1
    CLIENT_AUTH: int = 2


class TLSVersion:
    """TLS version constants."""
    MINIMUM_SUPPORTED: int = 2
    MAXIMUM_SUPPORTED: int = 4
    TLSv1: int = 769
    TLSv1_1: int = 770
    TLSv1_2: int = 771
    TLSv1_3: int = 772


class AlertDescription:
    """SSL alert description constants."""
    CLOSE_NOTIFY: int = 0
    UNEXPECTED_MESSAGE: int = 10
    BAD_RECORD_MAC: int = 20
    HANDSHAKE_FAILURE: int = 40
    CERTIFICATE_EXPIRED: int = 45
    CERTIFICATE_UNKNOWN: int = 46
    ILLEGAL_PARAMETER: int = 47
    UNKNOWN_CA: int = 48
    ACCESS_DENIED: int = 49
    DECODE_ERROR: int = 50
    DECRYPT_ERROR: int = 51
    PROTOCOL_VERSION: int = 70
    INSUFFICIENT_SECURITY: int = 71
    INTERNAL_ERROR: int = 80
    USER_CANCELLED: int = 90
    NO_RENEGOTIATION: int = 100
    UNSUPPORTED_EXTENSION: int = 110
