"""Deployment checks that run before plugins or writable services start."""

import ipaddress
from pathlib import Path
from urllib.parse import urlparse


def validate_production_settings(settings) -> None:
    host = settings.api_host.strip().lower()
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if not local and not settings.strict_production:
        raise RuntimeError("Non-loopback API_HOST requires STRICT_PRODUCTION=true")
    if not settings.strict_production:
        return
    if settings.debug:
        raise RuntimeError("strict_production requires DEBUG=false")
    token = settings.internal_api_token
    if (len(token.encode("utf-8")) < 32 or token.strip() != token
            or token.lower().startswith(("your-", "replace", "placeholder", "changeme"))):
        raise RuntimeError("strict_production requires a non-placeholder INTERNAL_API_TOKEN of at least 32 bytes")
    if not settings.cors_origins:
        raise RuntimeError("strict_production requires explicit CORS_ORIGINS")
    for origin in settings.cors_origins:
        parsed = urlparse(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or "*" in origin or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment):
            raise RuntimeError("CORS_ORIGINS must contain explicit HTTP(S) origins without paths or wildcards")
    roots = [value.strip() for value in settings.workspace_roots.split(",") if value.strip()]
    if not roots:
        raise RuntimeError("strict_production requires explicit WORKSPACE_ROOTS")
    for value in roots:
        root = Path(value)
        if not root.is_absolute() or not root.is_dir() or root.resolve() == Path(root.anchor):
            raise RuntimeError("WORKSPACE_ROOTS must name existing absolute project directories, not filesystem roots")
    for field in ("project_dir", "runtime_dir"):
        root = Path(getattr(settings, field)).resolve()
        if root == Path(root.anchor):
            raise RuntimeError(f"{field.upper()} must not be a filesystem root")
