"""
prova_mcp.server — MCP server exposing the Prova reasoning verifier as tools.

Designed to be dropped into any MCP client (Claude Code, Cursor, Windsurf,
ChatGPT desktop, Zed, etc.) so the host model can verify its own reasoning
before producing an answer — and, when a Lean proof is returned, kernel-check
it locally with no trust in Prova or its servers.

Configuration is taken from the environment:

    PROVA_API_KEY        Optional. If unset, requests use Prova's public demo
                         tier (rate-limited, no certificate retention).
    PROVA_API_BASE_URL   Optional. Default: https://api.prova.cobound.dev
    PROVA_LEAN_BIN       Optional. Default: "lean". Path to the Lean 4
                         executable for the local kernel-check tool.
    PROVA_DEFAULT_RETAIN Optional. "true"/"false". Default: "false". Whether
                         the verifier should persist the original reasoning
                         text on the certificate row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from . import __version__

logger = logging.getLogger("prova_mcp")

DEFAULT_API_BASE = "https://api.prova.cobound.dev"
DEFAULT_LEAN_BIN = "lean"
DEFAULT_TIMEOUT_S = 90.0

mcp = FastMCP(
    name="prova",
    instructions=(
        "Prova verifies the structural validity of multi-step AI reasoning and "
        "issues a tamper-evident certificate backed by 2,400+ Lean 4 theorems. "
        "Use `verify_reasoning` on your own draft reasoning before producing a "
        "final answer to catch circular arguments, contradictions, and "
        "unsupported leaps. For VALID results, you can call `kernel_check_proof` "
        "to re-check the emitted Lean proof on the local machine — this closes "
        "the trust loop without depending on Prova's servers."
    ),
)


def _api_base() -> str:
    return os.environ.get("PROVA_API_BASE_URL", DEFAULT_API_BASE).rstrip("/")


def _api_key() -> str | None:
    key = os.environ.get("PROVA_API_KEY")
    return key.strip() if key else None


def _default_retain() -> bool:
    raw = os.environ.get("PROVA_DEFAULT_RETAIN", "false").strip().lower()
    return raw in {"1", "true", "yes", "y"}


def _headers() -> dict[str, str]:
    h = {
        "User-Agent": f"prova-mcp/{__version__}",
        "Accept": "application/json",
    }
    key = _api_key()
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _summarize_certificate(cert: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, model-friendly view of a certificate row."""
    summary: dict[str, Any] = {
        "certificate_id": cert.get("certificate_id") or cert.get("id"),
        "verdict": cert.get("verdict"),
        "confidence_score": cert.get("confidence_score"),
        "timestamp": cert.get("timestamp"),
        "certificate_url": cert.get("certificate_url"),
        "sha256": cert.get("sha256"),
        "prova_version": cert.get("prova_version"),
        "validator_version": cert.get("validator_version"),
        "has_lean_proof": bool(cert.get("lean_proof")),
    }
    failure = cert.get("failure")
    if failure:
        summary["failure"] = {
            "type": failure.get("type"),
            "location": failure.get("location"),
            "description": failure.get("description"),
        }
        kc = failure.get("known_consequence")
        if kc:
            summary["failure"]["known_consequence"] = {
                "name": kc.get("name"),
                "severity": kc.get("severity"),
                "consequence": kc.get("consequence"),
            }
    graph = cert.get("argument_graph")
    if isinstance(graph, dict):
        summary["graph"] = {
            "nodes": len(graph.get("nodes") or []),
            "edges": len(graph.get("edges") or []),
        }
    return summary


async def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{_api_base()}{path}"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
        resp = await client.post(url, json=body, headers=_headers())
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Prova API {resp.status_code} on POST {path}: {resp.text[:500]}"
            )
        return resp.json()


async def _api_get(path: str) -> httpx.Response:
    url = f"{_api_base()}{path}"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Prova API {resp.status_code} on GET {path}: {resp.text[:500]}"
            )
        return resp


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def verify_reasoning(
    reasoning: str,
    retain: bool | None = None,
    source_url: str | None = None,
    domain: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a multi-step reasoning chain and return a Prova certificate summary.

    Use this on any draft chain-of-thought before producing a final answer.
    A VALID verdict means the argument is structurally sound (no circularity,
    no contradiction, no unsupported leap). An INVALID verdict tells you
    exactly which step is broken in the `failure` field — repair that step
    and re-verify.

    Args:
        reasoning: The reasoning chain as plain text (numbered steps or prose).
        retain: If True, the original reasoning text is persisted on the
            certificate row. Default follows PROVA_DEFAULT_RETAIN env var
            (False if unset).
        source_url: Optional URL of a paper or document this reasoning came
            from — surfaced on the certificate page.
        domain: Optional hint: medical | legal | financial | code | general.
            Improves failure-classification accuracy.
        metadata: Optional caller-defined key/value pairs (<=20 keys, <=8 KB)
            attached to the certificate.

    Returns:
        Compact certificate summary including verdict, confidence_score,
        certificate_id, certificate_url, and (if INVALID) a `failure` block
        pinpointing the broken step.
    """
    if retain is None:
        retain = _default_retain()

    md: dict[str, Any] = dict(metadata or {})
    if source_url:
        md.setdefault("source_url", source_url)

    body: dict[str, Any] = {
        "reasoning": reasoning,
        "retain": bool(retain),
        "metadata": md,
    }
    if domain:
        body["metadata"]["domain"] = domain

    cert = await _api_post("/verify", body)
    return _summarize_certificate(cert)


@mcp.tool()
async def get_certificate(certificate_id: str) -> dict[str, Any]:
    """Fetch a previously issued certificate by ID (e.g. PRV-2026-A7X4).

    Returns the same compact summary as `verify_reasoning`. Use this to
    re-check a verdict, share a link, or inspect a failure that someone
    else's pipeline produced.
    """
    resp = await _api_get(f"/certificate/{certificate_id}")
    return _summarize_certificate(resp.json())


@mcp.tool()
async def download_lean_proof(certificate_id: str) -> dict[str, Any]:
    """Download the self-contained Lean 4 proof for a VALID certificate.

    INVALID certificates do not have a proof — the call will return an error.
    The returned `lean_source` can be passed straight into `kernel_check_proof`
    to verify it on the local machine without trusting Prova.

    Returns:
        {certificate_id, lean_source, byte_count}
    """
    resp = await _api_get(f"/certificate/{certificate_id}/lean")
    source = resp.text
    return {
        "certificate_id": certificate_id,
        "lean_source": source,
        "byte_count": len(source.encode("utf-8")),
    }


@mcp.tool()
async def kernel_check_proof(lean_source: str) -> dict[str, Any]:
    """Run the Lean 4 kernel on a proof string locally.

    This is the trust-anchor: Prova's verdict is only as strong as its server,
    but `lean` on the local machine is independent. Exit code 0 means the
    kernel accepted every step of the proof. Anything else means the proof is
    wrong and the certificate must not be trusted.

    Requires the `lean` binary on PATH (override with PROVA_LEAN_BIN). Install
    via elan: https://github.com/leanprover/elan

    Returns:
        {accepted, exit_code, stdout, stderr, lean_binary, lean_version}
    """
    lean_bin = os.environ.get("PROVA_LEAN_BIN", DEFAULT_LEAN_BIN)
    resolved = shutil.which(lean_bin)
    if not resolved:
        return {
            "accepted": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": (
                f"Lean binary '{lean_bin}' not found on PATH. "
                f"Install via elan: https://github.com/leanprover/elan"
            ),
            "lean_binary": lean_bin,
            "lean_version": None,
        }

    version = ""
    try:
        ver_proc = await asyncio.create_subprocess_exec(
            resolved, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        ver_out, _ = await ver_proc.communicate()
        version = ver_out.decode("utf-8", errors="replace").strip()
    except Exception:
        version = ""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".lean", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(lean_source)
        tmp_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            resolved, tmp_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return {
            "accepted": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": out.decode("utf-8", errors="replace"),
            "stderr": err.decode("utf-8", errors="replace"),
            "lean_binary": resolved,
            "lean_version": version,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@mcp.tool()
async def verify_and_kernel_check(
    reasoning: str,
    retain: bool | None = None,
    source_url: str | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Verify reasoning, then locally kernel-check the emitted Lean proof.

    The complete trust loop in one call. If the verdict is VALID, the Lean
    proof is downloaded and handed to the local Lean kernel; the result tells
    you whether the proof is mathematically sound on your own machine, not
    Prova's. INVALID verdicts skip the kernel step and return the failure.

    Returns:
        {certificate, kernel_check}
        — kernel_check is null when verdict != VALID or no proof was emitted.
    """
    cert_summary = await verify_reasoning(
        reasoning=reasoning,
        retain=retain,
        source_url=source_url,
        domain=domain,
    )
    result: dict[str, Any] = {"certificate": cert_summary, "kernel_check": None}
    if cert_summary.get("verdict") != "VALID" or not cert_summary.get("has_lean_proof"):
        return result

    cid = cert_summary.get("certificate_id")
    if not cid:
        return result
    proof = await download_lean_proof(cid)
    result["kernel_check"] = await kernel_check_proof(proof["lean_source"])
    return result


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("prova://certificate/{certificate_id}")
async def certificate_resource(certificate_id: str) -> str:
    """Expose a certificate as a readable resource (JSON)."""
    resp = await _api_get(f"/certificate/{certificate_id}")
    return json.dumps(resp.json(), indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the MCP server over stdio (the standard MCP transport)."""
    logging.basicConfig(
        level=os.environ.get("PROVA_MCP_LOG_LEVEL", "WARNING").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("prova-mcp %s starting (api=%s)", __version__, _api_base())
    mcp.run()


if __name__ == "__main__":
    main()
