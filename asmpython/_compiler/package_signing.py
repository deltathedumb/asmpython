"""Certificate-backed detached signatures for ASMPython packages and artifacts.

This deliberately implements signing and cryptographic verification without an
extension-authority policy. Trust-chain enforcement can be layered on later;
for now callers decide which certificates they trust.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SIGNATURE_FORMAT = "asmpython.detached-signature"
SIGNATURE_VERSION = 1


class PackageSigningError(RuntimeError):
    pass


def _openssl() -> str:
    path = shutil.which("openssl")
    if path is None:
        raise PackageSigningError("OpenSSL is required for certificate signing")
    return path


def _run(command: list[str], *, input_data: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise PackageSigningError(f"failed to run {command[0]}: {exc}") from exc
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        raise PackageSigningError(message or f"{command[0]} exited {proc.returncode}")
    return proc.stdout


def _certificate_metadata(certificate: Path) -> dict[str, Any]:
    openssl = _openssl()
    text = _run(
        [
            openssl,
            "x509",
            "-in",
            str(certificate),
            "-noout",
            "-subject",
            "-issuer",
            "-serial",
            "-fingerprint",
            "-sha256",
            "-dates",
        ]
    ).decode("utf-8", "replace")
    metadata: dict[str, Any] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            metadata[key.strip().lower().replace(" ", "_")] = value.strip()
    der = _run([openssl, "x509", "-in", str(certificate), "-outform", "DER"])
    metadata["certificate_sha256"] = hashlib.sha256(der).hexdigest()
    return metadata


def sign_package(
    package: Path,
    certificate: Path,
    private_key: Path,
    *,
    output: Path | None = None,
    key_password_file: Path | None = None,
) -> Path:
    package = package.expanduser()
    certificate = certificate.expanduser()
    private_key = private_key.expanduser()
    if not package.is_file():
        raise PackageSigningError(f"package does not exist: {package}")
    if not certificate.is_file():
        raise PackageSigningError(f"certificate does not exist: {certificate}")
    if not private_key.is_file():
        raise PackageSigningError(f"private key does not exist: {private_key}")
    openssl = _openssl()
    command = [openssl, "dgst", "-sha256", "-sign", str(private_key)]
    if key_password_file is not None:
        command.extend(("-passin", f"file:{key_password_file}"))
    command.append(str(package))
    signature = _run(command)
    certificate_pem = certificate.read_text(encoding="utf-8")
    payload = {
        "format": SIGNATURE_FORMAT,
        "format_version": SIGNATURE_VERSION,
        "algorithm": "rsa-or-ec-sha256",
        "package": package.name,
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "signature": base64.b64encode(signature).decode("ascii"),
        "certificate_pem": certificate_pem,
        "certificate": _certificate_metadata(certificate),
        "authority_policy": "caller-managed",
    }
    output = output or package.with_suffix(package.suffix + ".apsig")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


def read_signature(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageSigningError(f"cannot read signature {path}: {exc}") from exc
    if payload.get("format") != SIGNATURE_FORMAT or payload.get("format_version") != SIGNATURE_VERSION:
        raise PackageSigningError(f"unsupported signature format/version in {path}")
    return payload


def verify_signature(package: Path, signature_path: Path) -> dict[str, Any]:
    package = package.expanduser()
    payload = read_signature(signature_path.expanduser())
    actual_digest = hashlib.sha256(package.read_bytes()).hexdigest()
    digest_valid = actual_digest == payload.get("package_sha256")
    cryptographic_valid = False
    error: str | None = None
    if digest_valid:
        openssl = _openssl()
        with tempfile.TemporaryDirectory(prefix="asmpython-signature-") as temp:
            root = Path(temp)
            certificate = root / "certificate.pem"
            public_key = root / "public.pem"
            signature = root / "signature.bin"
            certificate.write_text(str(payload.get("certificate_pem", "")), encoding="utf-8")
            try:
                signature.write_bytes(base64.b64decode(payload["signature"], validate=True))
                _run(
                    [
                        openssl,
                        "x509",
                        "-in",
                        str(certificate),
                        "-pubkey",
                        "-noout",
                        "-out",
                        str(public_key),
                    ]
                )
                _run(
                    [
                        openssl,
                        "dgst",
                        "-sha256",
                        "-verify",
                        str(public_key),
                        "-signature",
                        str(signature),
                        str(package),
                    ]
                )
                cryptographic_valid = True
            except (KeyError, ValueError, PackageSigningError) as exc:
                error = str(exc)
    else:
        error = "package SHA-256 does not match the signed digest"
    return {
        "valid": digest_valid and cryptographic_valid,
        "digest_valid": digest_valid,
        "cryptographic_valid": cryptographic_valid,
        "package_sha256": actual_digest,
        "certificate": payload.get("certificate", {}),
        "authority_policy": payload.get("authority_policy", "caller-managed"),
        "error": error,
    }


def command_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="asmpython sign")
    sub = parser.add_subparsers(dest="action", required=True)
    sign = sub.add_parser("package")
    sign.add_argument("package", type=Path)
    sign.add_argument("--certificate", required=True, type=Path)
    sign.add_argument("--key", required=True, type=Path)
    sign.add_argument("--key-password-file", type=Path, default=None)
    sign.add_argument("--output", type=Path, default=None)
    verify = sub.add_parser("verify")
    verify.add_argument("package", type=Path)
    verify.add_argument("--signature", type=Path, default=None)
    verify.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("signature", type=Path)
    show.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "package":
            output = sign_package(
                args.package,
                args.certificate,
                args.key,
                output=args.output,
                key_password_file=args.key_password_file,
            )
            print(f"asmpython: wrote detached signature {output}")
            return 0
        if args.action == "show":
            payload = read_signature(args.signature)
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
            else:
                print(f"signature: {args.signature}")
                print(f"package: {payload.get('package')}")
                print(f"digest: {payload.get('package_sha256')}")
                certificate = payload.get("certificate", {})
                print(f"subject: {certificate.get('subject', '-')}")
                print(f"issuer: {certificate.get('issuer', '-')}")
                print("authority policy: caller-managed")
            return 0
        signature = args.signature or args.package.with_suffix(args.package.suffix + ".apsig")
        result = verify_signature(args.package, signature)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(
                f"{args.package}: {'valid signature' if result['valid'] else 'INVALID signature'}"
            )
            certificate = result.get("certificate", {})
            if certificate:
                print(f"  subject: {certificate.get('subject', '-')}")
                print(f"  fingerprint: {certificate.get('sha256_fingerprint', certificate.get('certificate_sha256', '-'))}")
            if result.get("error"):
                print(f"  error: {result['error']}", file=sys.stderr)
        return 0 if result["valid"] else 1
    except (OSError, PackageSigningError) as exc:
        print(f"asmpython: sign: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "PackageSigningError",
    "command_main",
    "read_signature",
    "sign_package",
    "verify_signature",
]
