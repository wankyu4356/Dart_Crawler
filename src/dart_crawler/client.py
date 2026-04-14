"""Thin wrapper around the DART OpenAPI."""
from __future__ import annotations

import time
from typing import Any

import requests

from .config import Settings

BASE_URL = "https://opendart.fss.or.kr/api"


class DartAPIError(RuntimeError):
    """Raised when DART API returns a non-success status."""


class DartClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self.session = requests.Session()

    def _get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        params["crtfc_key"] = self.settings.api_key
        url = f"{BASE_URL}/{endpoint}"

        last_exc: Exception | None = None
        for attempt in range(self.settings.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.settings.request_timeout)
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status and status != "000":
                    raise DartAPIError(f"{status}: {data.get('message')}")
                time.sleep(self.settings.request_delay)
                return data
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                time.sleep(self.settings.request_delay * (2**attempt))
        raise DartAPIError(f"Request failed after retries: {last_exc}")

    def list_disclosures(
        self,
        corp_code: str | None = None,
        bgn_de: str | None = None,
        end_de: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict[str, Any]:
        """list.json - 공시 검색."""
        params: dict[str, Any] = {"page_no": page_no, "page_count": page_count}
        if corp_code:
            params["corp_code"] = corp_code
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        return self._get("list.json", **params)

    def company(self, corp_code: str) -> dict[str, Any]:
        """company.json - 기업 개황."""
        return self._get("company.json", corp_code=corp_code)
