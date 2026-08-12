import time
import logging
from typing import Optional, List
import requests
from app.models.schemas import SearchResult, FetchedDocument

logger = logging.getLogger(__name__)

class BudgetExhaustedError(Exception):
    """Raised when the IndianKanoon call budget for this run is exhausted."""
    pass

class IndianKanoonClient:
    """Client for the IndianKanoon API."""

    BASE_URL = "https://api.indiankanoon.org"
    SEARCH_COST = 0.50  # ₹ per search call
    DOC_COST = 0.20     # ₹ per document call
    # S-2: Default timeout for all API calls (seconds)
    REQUEST_TIMEOUT = 30
    # Guard against a pathological response exhausting memory; the largest
    # real judgments are well under this.
    MAX_RESPONSE_BYTES = 8 * 1024 * 1024
    # Transient-failure retries. A single 502 used to abort the whole run and
    # forfeit the paid call.
    MAX_ATTEMPTS = 3
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, token: str, dev_mode: bool = False, max_dev_calls: int = 50,
                 max_calls: Optional[int] = None):
        if not token:
            raise ValueError("IndianKanoon token is required (set IKANOON_TOKEN)")
        self.token = token
        self.dev_mode = dev_mode
        self._call_count = 0
        self._max_dev_calls = max_dev_calls
        # The cap is enforced in EVERY environment. It used to apply only when
        # dev_mode was set, so a production run had no ceiling at all on a
        # metered API billed per call (see SEARCH_COST / DOC_COST below).
        self._max_calls = max_dev_calls if dev_mode else (max_calls if max_calls is not None else 500)

    @property
    def calls_made(self) -> int:
        return self._call_count

    @property
    def estimated_spend(self) -> float:
        """Rough ₹ spent so far, for run-stats reporting."""
        return round(self._call_count * self.DOC_COST, 2)

    def _check_budget(self) -> None:
        """Raise if the call budget for this run is exhausted."""
        if self._call_count >= self._max_calls:
            scope = "Development" if self.dev_mode else "Production"
            raise BudgetExhaustedError(
                f"{scope} budget exceeded: max {self._max_calls} API calls allowed per run."
            )

    def _track_call(self) -> None:
        """Check budget before making a call, then increment."""
        self._check_budget()
        self._call_count += 1

    def build_form_input(
        self,
        topic: str,
        doctypes: str = "supremecourt",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> str:
        """Build the formInput string with inline filter operators."""
        parts = [topic]
        if doctypes:
            parts.append(f"doctypes: {doctypes}")
        if from_date:
            parts.append(f"fromdate: {from_date}")
        if to_date:
            parts.append(f"todate: {to_date}")

        return " ".join(parts)

    def _post(self, url: str, data: Optional[dict] = None) -> dict:
        """POST with a timeout, bounded retries on transient failures, and a
        response-size guard. Returns the decoded JSON body."""
        headers = {"Authorization": f"Token {self.token}"}
        last_error: Optional[Exception] = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                # S-2: timeout prevents hung workers if IndianKanoon is slow
                response = requests.post(
                    url, headers=headers, data=data, timeout=self.REQUEST_TIMEOUT
                )

                if response.status_code in self.RETRY_STATUSES and attempt < self.MAX_ATTEMPTS:
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        "IndianKanoon %s returned %d — retrying in %ds (attempt %d/%d)",
                        url, response.status_code, delay, attempt, self.MAX_ATTEMPTS
                    )
                    time.sleep(delay)
                    continue

                response.raise_for_status()

                if len(response.content) > self.MAX_RESPONSE_BYTES:
                    raise ValueError(
                        f"Response from {url} exceeds {self.MAX_RESPONSE_BYTES} bytes"
                    )

                return response.json()

            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = e
                if attempt < self.MAX_ATTEMPTS:
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        "IndianKanoon %s failed (%s) — retrying in %ds (attempt %d/%d)",
                        url, e, delay, attempt, self.MAX_ATTEMPTS
                    )
                    time.sleep(delay)
                    continue
                raise

        raise last_error if last_error else RuntimeError(f"POST {url} failed")

    def search(self, form_input: str, page_num: int = 0) -> List[SearchResult]:
        """Search IndianKanoon. MUST use POST. Returns parsed results."""
        self._track_call()

        result_data = self._post(
            f"{self.BASE_URL}/search/",
            data={"formInput": form_input, "pagenum": page_num},
        )
        docs = result_data.get("docs", [])

        results = []
        for doc in docs:
            # Handle potential type mismatches from API
            tid = str(doc.get("tid", ""))
            if not tid:
                continue

            results.append(SearchResult(
                tid=tid,
                title=doc.get("title", ""),
                headline=doc.get("headline", ""),
                docsource=doc.get("docsource", ""),
                docsize=int(doc.get("docsize", 0) or 0)
            ))

        return results

    def fetch_document(self, tid: str) -> FetchedDocument:
        """Fetch a full document by tid. Returns parsed document."""
        # tid is interpolated into the request path, so reject anything that is
        # not a plain document id rather than letting it traverse the URL.
        tid_str = str(tid)
        if not tid_str.isdigit():
            raise ValueError(f"Invalid IndianKanoon tid: {tid_str!r}")

        self._track_call()

        doc_data = self._post(f"{self.BASE_URL}/doc/{tid_str}/")

        return FetchedDocument(
            tid=tid,
            title=doc_data.get("title", ""),
            doc_html=doc_data.get("doc", ""),
            docsource=doc_data.get("docsource", "")
        )

