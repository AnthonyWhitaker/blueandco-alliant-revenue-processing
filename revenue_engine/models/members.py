from datetime import date
from typing import Optional

from pydantic import Field

from .common import ApiModel, InputModel


class Member(ApiModel):
    id: int
    name: str
    entity_code: Optional[str]
    premier_entity_code: Optional[str]
    alliant_entity_code: Optional[str]
    account_id: Optional[str]
    billing_city: Optional[str]
    billing_state: Optional[str]
    billing_zip: Optional[str]
    is_active: bool


class MemberInput(InputModel):
    name: str = Field(min_length=1)
    premier_entity_code: Optional[str] = None
    account_id: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None


class MemberImportResult(ApiModel):
    inserted: int
    skipped: int


class LedgerEntry(ApiModel):
    id: int
    payor: str
    payment_received_date: Optional[date]
    deposit_amount: float
    sf_payment_id: str
    contract_id: Optional[str]


class LedgerImportResult(ApiModel):
    inserted: int
