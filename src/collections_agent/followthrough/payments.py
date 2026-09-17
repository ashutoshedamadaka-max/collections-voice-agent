"""Payments source — swappable, mirrors sheets/client.py's `SheetsBackend` Protocol exactly.

There is no real payment-gateway/bank integration for this project. Today, a human manually
enters what actually got paid into the `Payments` tab — the follow-through job (job.py) checks
promises against whatever this Protocol returns, without knowing or caring that the current
implementation happens to be a spreadsheet a person edits by hand. A future real integration
(a bank statement importer, a payment-gateway webhook cache) implements the same one-method
Protocol; job.py's matching logic never changes.

Deliberately not the same signal as the in-call `log_payment_claim` tool
(webhooks/handlers.py): that's an unverified, ephemeral spoken claim, never written to Sheets.
The `Payments` tab is the ledger this job actually trusts.
"""

from __future__ import annotations

from typing import Protocol

from collections_agent.models.domain import Payment
from collections_agent.sheets.client import SheetsBackend
from collections_agent.sheets.readers import read_payments


class PaymentsSource(Protocol):
    def list_payments(self) -> list[Payment]: ...


class SheetPaymentsSource:
    """Today's implementation — wraps a SheetsBackend and reads the Payments tab."""

    def __init__(self, backend: SheetsBackend) -> None:
        self._backend = backend

    def list_payments(self) -> list[Payment]:
        return read_payments(self._backend)
