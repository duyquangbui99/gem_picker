"""Etherscan free-tier API (5 calls/sec w/ key) — on-chain due diligence for
EVM tokens only. Note: true holder-count endpoints are Etherscan Pro-only on
the free plan, so this uses two free-tier-accessible proxies instead:
contract verification status (scam/rug-pull filter) and recent transfer
activity (rough proxy for on-chain momentum). Screeners must treat this as
"not applicable" for non-EVM chains and for tokens without a contract
address, not as a bad score."""

from pathlib import Path

import requests

from gempicker.data_sources.base import DEFAULT_TIMEOUT, cached_json, with_retry

BASE_URL = "https://api.etherscan.io/api"


@with_retry
def _get(session: requests.Session, api_key: str, params: dict) -> dict:
    resp = session.get(BASE_URL, params={**params, "apikey": api_key}, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def is_contract_verified(session: requests.Session, api_key: str, cache_dir: Path, contract_address: str) -> bool | None:
    """None means "unknown/lookup failed" — must be treated as neutral, not
    as a red flag, since a failed lookup isn't evidence of a scam."""

    def fetch() -> dict:
        return _get(session, api_key, {"module": "contract", "action": "getsourcecode", "address": contract_address})

    try:
        data = cached_json(cache_dir, f"etherscan_source_{contract_address}", 86_400, fetch)
        result = data.get("result", [])
        if not result:
            return None
        return bool(result[0].get("SourceCode"))
    except requests.RequestException:
        return None


def recent_transfer_count(session: requests.Session, api_key: str, cache_dir: Path, contract_address: str, limit: int = 100) -> int | None:
    """Count of the most recent ERC-20 transfer events as a rough on-chain
    activity proxy. Returns None on lookup failure (treat as neutral)."""

    def fetch() -> dict:
        return _get(
            session,
            api_key,
            {
                "module": "account",
                "action": "tokentx",
                "contractaddress": contract_address,
                "page": 1,
                "offset": limit,
                "sort": "desc",
            },
        )

    try:
        data = cached_json(cache_dir, f"etherscan_transfers_{contract_address}", 3600, fetch)
        result = data.get("result", [])
        return len(result) if isinstance(result, list) else None
    except requests.RequestException:
        return None
