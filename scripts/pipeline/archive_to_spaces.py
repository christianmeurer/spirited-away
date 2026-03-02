#!/usr/bin/env python3
"""Archive internal manifests/artifacts to DigitalOcean Spaces.

Conservative implementation for internal-use runbook workflows.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _resolve_arg_or_env(arg_value: str | None, env_key: str, default: str = "") -> str:
    if arg_value is not None and arg_value.strip():
        return arg_value.strip()
    return os.getenv(env_key, default).strip()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _derive_signing_key(secret_key: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    return _hmac_sha256(k_service, "aws4_request")


def _collect_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted([candidate for candidate in path.rglob("*") if candidate.is_file()])
    raise FileNotFoundError(f"Input path not found: {path}")


def _build_object_key(prefix: str, local_file: Path, input_root: Path) -> str:
    relative = local_file.relative_to(input_root).as_posix()
    if prefix:
        return f"{prefix.rstrip('/')}/{relative}"
    return relative


def _put_object_s3_compatible(
    endpoint: str,
    bucket: str,
    region: str,
    access_key: str,
    secret_key: str,
    object_key: str,
    content_bytes: bytes,
) -> dict[str, Any]:
    method = "PUT"
    service = "s3"
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    endpoint_stripped = endpoint.rstrip("/")
    endpoint_host = endpoint_stripped.replace("https://", "").replace("http://", "")
    url = f"{endpoint_stripped}/{bucket}/{quote(object_key)}"

    payload_hash = hashlib.sha256(content_bytes).hexdigest()
    canonical_uri = f"/{bucket}/{quote(object_key)}"
    canonical_querystring = ""
    canonical_headers = (
        f"host:{endpoint_host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = _derive_signing_key(secret_key, datestamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    headers = {
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": authorization,
        "Content-Type": "application/octet-stream",
    }

    response = requests.put(url, headers=headers, data=content_bytes, timeout=120)
    response.raise_for_status()
    return {
        "bucket": bucket,
        "key": object_key,
        "endpoint": endpoint_stripped,
        "url": f"{endpoint_stripped}/{bucket}/{quote(object_key)}",
        "status_code": response.status_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive internal artifacts to DigitalOcean Spaces")
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument("--input", required=True, help="File or directory to archive")
    parser.add_argument("--prefix", default=None, help="Spaces object key prefix override")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    enabled = _resolve_arg_or_env(None, "ENABLE_SPACES_ARCHIVAL", "false").lower() == "true"
    if not enabled:
        raise RuntimeError(
            "Spaces archival is disabled. Set ENABLE_SPACES_ARCHIVAL=true to enable this command."
        )

    endpoint = _resolve_arg_or_env(None, "DO_SPACES_ENDPOINT")
    bucket = _resolve_arg_or_env(None, "DO_SPACES_BUCKET")
    region = _resolve_arg_or_env(None, "DO_SPACES_REGION", "nyc3")
    access_key = _resolve_arg_or_env(None, "DO_SPACES_ACCESS_KEY_ID")
    secret_key = _resolve_arg_or_env(None, "DO_SPACES_SECRET_ACCESS_KEY")
    prefix = _resolve_arg_or_env(args.prefix, "DO_SPACES_ARCHIVE_PREFIX", "internal-rnd/archives")

    required = {
        "DO_SPACES_ENDPOINT": endpoint,
        "DO_SPACES_BUCKET": bucket,
        "DO_SPACES_REGION": region,
        "DO_SPACES_ACCESS_KEY_ID": access_key,
        "DO_SPACES_SECRET_ACCESS_KEY": secret_key,
    }
    missing = [name for name, value in required.items() if not value or value.startswith("REPLACE_WITH_")]
    if missing:
        raise RuntimeError(
            "Missing required Spaces configuration values: " + ", ".join(sorted(missing))
        )

    input_path = Path(args.input)
    files = _collect_files(input_path)
    if not files:
        raise RuntimeError(f"No files found to archive from: {input_path}")

    input_root = input_path if input_path.is_dir() else input_path.parent
    uploaded: list[dict[str, Any]] = []

    for local_file in files:
        key = _build_object_key(prefix, local_file, input_root)
        if args.dry_run:
            uploaded.append(
                {
                    "bucket": bucket,
                    "key": key,
                    "endpoint": endpoint.rstrip("/"),
                    "url": f"{endpoint.rstrip('/')}/{bucket}/{quote(key)}",
                    "status_code": 0,
                    "dry_run": True,
                }
            )
            continue

        content_bytes = local_file.read_bytes()
        result = _put_object_s3_compatible(
            endpoint=endpoint,
            bucket=bucket,
            region=region,
            access_key=access_key,
            secret_key=secret_key,
            object_key=key,
            content_bytes=content_bytes,
        )
        uploaded.append(result)

    payload = {
        "usage_scope": "INTERNAL_RND",
        "sharing_allowed": False,
        "input": str(input_path),
        "prefix": prefix,
        "uploaded_count": len(uploaded),
        "uploaded": uploaded,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
