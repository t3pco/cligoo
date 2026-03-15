#!/usr/bin/env python3
"""Degoo API probe — test all known operations against live credentials.

Run this script to verify which API operations still work after a Degoo update,
discover new operation versions, and produce a health report.

Usage:
    # Use stored token (run `degoo login` or `degoo login --browser` first):
    python scripts/probe_api.py

    # Machine-readable JSON report:
    python scripts/probe_api.py --json

    # Attempt GraphQL schema introspection (may be blocked by AppSync):
    python scripts/probe_api.py --introspect

    # Show full response bodies (verbose):
    python scripts/probe_api.py --verbose

    # Pass a token directly (skip keyring):
    python scripts/probe_api.py --token eyJ...

Exit codes:
    0  All probed operations succeeded
    1  One or more operations failed
    2  Authentication error (no stored token)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field

# ── Add src to path so we can import cligoo without installing ─────────────
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from cligoo.constants import DEFAULT_HEADERS, GRAPHQL_URL  # noqa: E402

# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    operation: str
    status: str  # "ok" | "error" | "skipped"
    http_status: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    data_keys: list[str] = field(default_factory=list)
    raw: Any = None


# ── Low-level GraphQL helper (no auth module dependency for portability) ──────


def _gql(
    token: str,
    query: str,
    variables: dict | None = None,
    operation: str | None = None,
    timeout: float = 30,
    verbose: bool = False,
) -> ProbeResult:
    variables = variables or {}
    variables["Token"] = token
    body: dict[str, Any] = {"query": query, "variables": variables}
    if operation:
        body["operationName"] = operation

    t0 = time.perf_counter()
    try:
        resp = httpx.post(GRAPHQL_URL, json=body, headers=DEFAULT_HEADERS, timeout=timeout)
    except httpx.TimeoutException:
        return ProbeResult(operation=operation or "?", status="error", error="Request timed out")
    except Exception as exc:
        return ProbeResult(operation=operation or "?", status="error", error=str(exc))

    latency_ms = (time.perf_counter() - t0) * 1000
    payload = resp.json()
    # Always keep the full payload so callers can extract item IDs for
    # follow-up probes (GetOverlay4, GetPermissions3) regardless of --verbose.
    raw = payload

    if resp.status_code != 200:
        return ProbeResult(
            operation=operation or "?",
            status="error",
            http_status=resp.status_code,
            latency_ms=latency_ms,
            error=f"HTTP {resp.status_code}: {resp.text[:200]}",
            raw=raw,
        )

    if "errors" in payload:
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        return ProbeResult(
            operation=operation or "?",
            status="error",
            http_status=resp.status_code,
            latency_ms=latency_ms,
            error=msgs,
            raw=raw,
        )

    data = payload.get("data") or {}
    return ProbeResult(
        operation=operation or "?",
        status="ok",
        http_status=resp.status_code,
        latency_ms=latency_ms,
        data_keys=list(data.keys()),
        raw=raw,
    )


# ── Introspection ─────────────────────────────────────────────────────────────

_INTROSPECT_QUERY = """
{
  __schema {
    queryType { name }
    mutationType { name }
    types {
      name
      kind
      fields(includeDeprecated: true) {
        name
        isDeprecated
        deprecationReason
      }
    }
  }
}
"""


def _introspect(verbose: bool = False) -> ProbeResult:
    """Try GraphQL introspection (no token needed for the schema query itself)."""
    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            GRAPHQL_URL,
            json={"query": _INTROSPECT_QUERY},
            headers=DEFAULT_HEADERS,
            timeout=30,
        )
    except Exception as exc:
        return ProbeResult(operation="__schema", status="error", error=str(exc))

    latency_ms = (time.perf_counter() - t0) * 1000
    payload = resp.json()

    if "errors" in payload or resp.status_code != 200:
        error = "; ".join(e.get("message", str(e)) for e in payload.get("errors", []))
        return ProbeResult(
            operation="__schema",
            status="error",
            http_status=resp.status_code,
            latency_ms=latency_ms,
            error=error or f"HTTP {resp.status_code}",
            raw=payload if verbose else None,
        )

    schema = payload.get("data", {}).get("__schema", {})
    types = schema.get("types", [])
    non_builtin = [t for t in types if not t["name"].startswith("__")]

    result = ProbeResult(
        operation="__schema",
        status="ok",
        http_status=resp.status_code,
        latency_ms=latency_ms,
        raw=payload if verbose else None,
    )

    # Extract operation names from the schema
    query_fields = []
    mutation_fields = []
    for t in types:
        if t["name"] == schema.get("queryType", {}).get("name"):
            query_fields = [f["name"] for f in (t.get("fields") or [])]
        if t["name"] == schema.get("mutationType", {}).get("name"):
            mutation_fields = [f["name"] for f in (t.get("fields") or [])]

    result.data_keys = query_fields + mutation_fields
    result.error = f"Schema has {len(non_builtin)} types, {len(query_fields)} queries, {len(mutation_fields)} mutations"
    return result


# ── All probes ────────────────────────────────────────────────────────────────


def run_probes(token: str, verbose: bool = False) -> list[ProbeResult]:
    results: list[ProbeResult] = []

    def probe(op: str, query: str, variables: dict | None = None) -> ProbeResult:
        r = _gql(token, query, variables, operation=op, verbose=verbose)
        results.append(r)
        return r

    # ── User info ──────────────────────────────────────────────────────────
    probe(
        "GetUserInfo3",
        """query GetUserInfo3($Token: String!) {
            getUserInfo3(Token: $Token) {
                ID FirstName LastName Email AccountType UsedQuota TotalQuota
                OAuth2Provider FileSizeLimit
            }
        }""",
    )

    # ── List root folder ───────────────────────────────────────────────────
    list_result = probe(
        "GetFileChildren5",
        """query GetFileChildren5($Token: String!, $ParentID: String, $Limit: Int!, $Order: Int!) {
            getFileChildren5(Token: $Token, ParentID: $ParentID, Limit: $Limit, Order: $Order) {
                Items { ID Name Category Size }
                NextToken
            }
        }""",
        {"ParentID": "0", "Limit": 5, "Order": 3},
    )

    # Try to get an item ID from the listing for single-item probes
    first_file_id: str | None = None
    first_folder_id: str | None = None
    if list_result.status == "ok" and list_result.raw:
        items = (list_result.raw.get("data") or {}).get("getFileChildren5", {}).get("Items") or []
        for it in items:
            cat = it.get("Category", 0)
            if cat not in (1, 2, 10) and first_file_id is None:
                first_file_id = str(it["ID"])
            if cat in (1, 2) and first_folder_id is None:
                first_folder_id = str(it["ID"])

    # ── Single item ────────────────────────────────────────────────────────
    if first_file_id or first_folder_id:
        target_id = first_file_id or first_folder_id
        probe(
            "GetOverlay4",
            """query GetOverlay4($Token: String!, $ID: IDType!) {
                getOverlay4(Token: $Token, ID: $ID) {
                    ID Name Category Size FilePath
                }
            }""",
            {"ID": {"FileID": target_id}},
        )
    else:
        results.append(ProbeResult("GetOverlay4", "skipped", error="No item ID available from GetFileChildren5"))

    # ── Search ─────────────────────────────────────────────────────────────
    probe(
        "GetSearchContent3",
        """query GetSearchContent3($Token: String!, $SearchTerm: String!, $Limit: Int!) {
            getSearchContent3(Token: $Token, SearchTerm: $SearchTerm, Limit: $Limit) {
                Items { ID Name }
                NextToken
            }
        }""",
        {"SearchTerm": "test", "Limit": 3},
    )

    # ── Trash ──────────────────────────────────────────────────────────────
    probe(
        "GetDeletedFiles",
        """query GetDeletedFiles($Token: String!, $Limit: Int!) {
            getDeletedFiles(Token: $Token, Limit: $Limit) {
                Items { ID Name }
                NextToken
            }
        }""",
        {"Limit": 3},
    )

    # ── Shared ─────────────────────────────────────────────────────────────
    probe(
        "GetShared",
        """query GetShared($Token: String!, $Limit: Int!) {
            getShared(Token: $Token, Limit: $Limit) {
                Items { ID Name }
                NextToken
            }
        }""",
        {"Limit": 3},
    )

    # ── Collections ────────────────────────────────────────────────────────
    probe(
        "GetCollections5",
        """query GetCollections5($Token: String!, $Limit: Int!, $Order: Int!) {
            getCollections5(Token: $Token, Limit: $Limit, Order: $Order) {
                Items { ContentView { ID Name } }
                NextToken
            }
        }""",
        {"Limit": 3, "Order": 3},
    )

    # ── Feed ───────────────────────────────────────────────────────────────
    probe(
        "GetFeed",
        """query GetFeed($Token: String!, $Limit: Int!) {
            getFeed(Token: $Token, Limit: $Limit) {
                ID Name Category
            }
        }""",
        {"Limit": 3},
    )

    # ── Permissions (on first file/folder) ────────────────────────────────
    if first_file_id or first_folder_id:
        probe(
            "GetPermissions3",
            """query GetPermissions3($Token: String!, $ID: String!) {
                getPermissions3(Token: $Token, ID: $ID) {
                    CurrentUserPermissions
                    Users { ID Name Email }
                }
            }""",
            {"ID": first_file_id or first_folder_id},
        )
    else:
        results.append(ProbeResult("GetPermissions3", "skipped", error="No item ID available"))

    # ── Probe for newer operation versions (non-destructive) ───────────────
    # If Degoo releases GetFileChildren6, this will succeed.
    version_probe = _gql(
        token,
        """query GetFileChildren6($Token: String!, $ParentID: String, $Limit: Int!, $Order: Int!) {
            getFileChildren6(Token: $Token, ParentID: $ParentID, Limit: $Limit, Order: $Order) {
                Items { ID Name }
                NextToken
            }
        }""",
        {"ParentID": "0", "Limit": 1, "Order": 3},
        operation="GetFileChildren6",
        verbose=verbose,
    )
    version_probe.operation = "GetFileChildren6 [version probe]"
    results.append(version_probe)

    return results


# ── Reporting ─────────────────────────────────────────────────────────────────


def _status_icon(status: str) -> str:
    return {"ok": "✓", "error": "✗", "skipped": "⤳"}.get(status, "?")


def print_human_report(results: list[ProbeResult], verbose: bool = False) -> bool:
    ok = sum(1 for r in results if r.status == "ok")
    err = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skipped")
    total = len(results)

    print(f"\n{'─' * 60}")
    print(f"  Degoo API Probe Report — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'─' * 60}")
    print(f"  Endpoint: {GRAPHQL_URL}")
    print(f"{'─' * 60}\n")

    col_w = max(len(r.operation) for r in results) + 2

    for r in results:
        icon = _status_icon(r.status)
        lat = f"{r.latency_ms:6.0f} ms" if r.latency_ms is not None else "       "
        detail = ""
        if r.status == "ok" and r.data_keys:
            detail = f"  → {', '.join(r.data_keys)}"
        elif r.status == "error" and r.error:
            detail = f"  → {r.error}"
        elif r.status == "skipped" and r.error:
            detail = f"  → {r.error}"

        print(f"  {icon}  {r.operation:<{col_w}} {lat}{detail}")

        if verbose and r.raw and r.status != "skipped":
            print(f"     RAW: {json.dumps(r.raw, indent=2)[:500]}")

    print(f"\n{'─' * 60}")
    print(f"  Results: {ok} ok  |  {err} error  |  {skipped} skipped  |  {total} total")
    print(f"{'─' * 60}\n")

    if err == 0:
        print("  ✓ All reachable operations are functioning correctly.\n")
    else:
        print(f"  ✗ {err} operation(s) failed — see details above.\n")
        print("  Troubleshooting:")
        print("    • Run `degoo whoami` to verify your token is still valid")
        print("    • Check DevTools for new operation version numbers (see docs/API_INTERNALS.md §4c)")
        print("    • Re-run with --verbose to see full response bodies\n")

    return err == 0


def print_json_report(results: list[ProbeResult]) -> bool:
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": GRAPHQL_URL,
        "summary": {
            "ok": sum(1 for r in results if r.status == "ok"),
            "error": sum(1 for r in results if r.status == "error"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
            "total": len(results),
        },
        "operations": [
            {
                "operation": r.operation,
                "status": r.status,
                "http_status": r.http_status,
                "latency_ms": round(r.latency_ms, 1) if r.latency_ms is not None else None,
                "error": r.error,
                "data_keys": r.data_keys,
                # raw payload included in JSON report only when --verbose
            }
            for r in results
        ],
    }
    print(json.dumps(report, indent=2))
    return report["summary"]["error"] == 0


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe all known Degoo API operations and report their health.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--token", "-t", help="Access JWT (skip keyring lookup)")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON report")
    parser.add_argument("--introspect", "-i", action="store_true", help="Attempt GraphQL schema introspection")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print full response bodies")
    args = parser.parse_args()

    # ── Get token ──────────────────────────────────────────────────────────
    token = args.token
    if not token:
        try:
            from cligoo.auth import get_token

            token = get_token()
        except Exception as exc:
            print(f"\n  ✗ Authentication failed: {exc}", file=sys.stderr)
            print("  Run `degoo login` or `degoo login --browser` first.\n", file=sys.stderr)
            sys.exit(2)

    # ── Introspection (optional, separate from main probes) ────────────────
    if args.introspect:
        if not args.json:
            print("\n  Attempting GraphQL introspection...")
        r = _introspect(verbose=args.verbose)
        if args.json:
            payload = {"introspection": {"status": r.status, "detail": r.error, "operations": r.data_keys}}
            print(json.dumps(payload, indent=2))
        else:
            icon = _status_icon(r.status)
            if r.status == "ok":
                print(f"  {icon} Introspection succeeded ({r.latency_ms:.0f} ms)")
                print(f"    {r.error}")
                if r.data_keys:
                    print(f"    Operations discovered: {', '.join(sorted(r.data_keys))}")
            else:
                print(f"  {icon} Introspection blocked or failed: {r.error}")
                print("    (AppSync commonly disables introspection — this is normal)")
            print()
        if not args.introspect or not any([True]):
            pass  # fall through to main probes

    # ── Main probes ────────────────────────────────────────────────────────
    if not args.json:
        print("  Probing Degoo API operations…", end="", flush=True)

    results = run_probes(token, verbose=args.verbose)

    if not args.json:
        print(" done.\n")

    success = print_json_report(results) if args.json else print_human_report(results, verbose=args.verbose)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
