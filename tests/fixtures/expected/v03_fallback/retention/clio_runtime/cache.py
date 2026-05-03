"""Content-addressed cache for CLIO judgment steps.

Copied verbatim into the emitted project as `clio_runtime/cache.py`.

Public surface:
    cache_key(step_name, model, prompt, schema_json) -> str  (hex SHA256)
    cache_lookup(cache_dir, step_name, key, ttl_seconds | None) -> str | None
    cache_store(cache_dir, step_name, key, model, response) -> None
    main()  -- CLI: `key` | `lookup <dir> <step> <key> [ttl]` | `store <dir> <step> <key> <model> <response>`

Entry on disk:
    <cache_dir>/<step_name>/<key>.json
    {"created_at": <epoch>, "model": "<m>", "response": "<raw>"}

Lookup semantics:
    ttl_seconds is None  -> permanent (CACHE: on)
    ttl_seconds is int   -> fresh if (now - created_at) < ttl_seconds (CACHE: ttl(...))
    Anything else        -> caller passes None for `off`; do not call lookup at all.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def cache_key(step_name: str, model: str, prompt: str, schema_json: str) -> str:
    payload = "\n".join([step_name, model, prompt, schema_json])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_lookup(
    cache_dir: Path,
    step_name: str,
    key: str,
    ttl_seconds: int | None,
) -> str | None:
    path = Path(cache_dir) / step_name / f"{key}.json"
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if ttl_seconds is not None:
        age = time.time() - float(entry.get("created_at", 0))
        if age >= ttl_seconds:
            return None
    return entry.get("response")


def cache_store(
    cache_dir: Path,
    step_name: str,
    key: str,
    model: str,
    response: str,
) -> None:
    step_dir = Path(cache_dir) / step_name
    step_dir.mkdir(parents=True, exist_ok=True)
    final = step_dir / f"{key}.json"
    tmp = final.with_suffix(".json.tmp")
    entry = {
        "created_at": int(time.time()),
        "model": model,
        "response": response,
    }
    tmp.write_text(json.dumps(entry))
    os.replace(tmp, final)


def _read_arg(arg: str) -> str:
    """Allow large args via @<file> indirection to avoid argv length limits."""
    if arg.startswith("@"):
        return Path(arg[1:]).read_text()
    return arg


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: python -m clio_runtime.cache key|lookup|store ...", file=sys.stderr)
        return 2

    sub = args[0]

    if sub == "key" and len(args) == 5:
        step_name, model, prompt, schema = args[1], args[2], _read_arg(args[3]), _read_arg(args[4])
        print(cache_key(step_name, model, prompt, schema))
        return 0

    if sub == "lookup" and len(args) in (4, 5):
        cache_dir = Path(args[1])
        step_name, key = args[2], args[3]
        ttl = int(args[4]) if len(args) == 5 and args[4] != "" else None
        hit = cache_lookup(cache_dir, step_name, key, ttl)
        if hit is None:
            return 1
        sys.stdout.write(hit)
        return 0

    if sub == "store" and len(args) == 6:
        cache_dir = Path(args[1])
        step_name, key, model, response = args[2], args[3], args[4], _read_arg(args[5])
        cache_store(cache_dir, step_name, key, model, response)
        return 0

    print(f"usage error: bad subcommand or arity: {args!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
