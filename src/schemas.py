"""Pydantic schemas for every structured LLM output in the workflow."""

import re
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

YesNo = Literal["Yes", "No"]


class ComplaintCategory(str, Enum):
    PRODUCT_DEFECT = "Product Defect"
    BILLING = "Billing & Payment"
    DELIVERY = "Delivery & Shipping"
    SERVICE_QUALITY = "Service Quality"
    REFUND = "Refund & Return"
    TECHNICAL = "Technical Issue"
    ACCOUNT = "Account & Access"
    GENERAL_INQUIRY = "General Inquiry"
    OTHER = "Other"


class CaseStatus(str, Enum):
    OPEN = "Open"
    IN_PROGRESS = "In Progress"
    ESCALATED = "Escalated"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class ComplaintExtraction(BaseModel):
    """Task 1 output: structured facts extracted from a single document."""

    customer_name: str | None = Field(
        description="Full name of the customer. null if not stated in the document."
    )
    email: str | None = Field(description="Customer email address. null if not stated.")
    phone_number: str | None = Field(description="Customer phone number. null if not stated.")
    product_or_service: str | None = Field(
        description="Product or service the document is about. null if not stated."
    )
    complaint_category: ComplaintCategory = Field(
        description="Best-fitting category for the issue."
    )
    issue_description: str = Field(
        description="One to three sentence factual description of the customer's issue."
    )
    resolution_provided: str | None = Field(
        description="Any resolution or action already provided by the company. null if none."
    )
    is_complaint: YesNo = Field(
        description="'Yes' if the customer is complaining about a problem, 'No' for "
        "neutral inquiries, feedback or compliments."
    )
    escalation_required: YesNo = Field(
        description="'Yes' if the document requests escalation, mentions legal/consumer "
        "forum action, repeated failed resolutions, safety risk or a senior-management "
        "request. Otherwise 'No'."
    )
    supporting_document_available: YesNo = Field(
        description="'Yes' only if the document mentions attached/enclosed evidence such "
        "as invoices, receipts, photos, screenshots or order confirmations."
    )
    overall_case_status: CaseStatus = Field(
        description="Current status of the case as evidenced by the document."
    )

    @field_validator("email")
    @classmethod
    def email_must_look_valid(cls, value: str | None) -> str | None:
        # Discard obviously wrong values rather than propagating them downstream.
        if value is None:
            return None
        value = value.strip()
        return value if "@" in value and "." in value.split("@")[-1] else None


class CustomerEmail(BaseModel):
    """Task 2 output: a professional reply to the customer."""

    subject: str = Field(description="Concise email subject line.")
    body: str = Field(
        description="Full email body in plain text (no markdown). Greeting line, short "
        "paragraphs separated by blank lines (\\n\\n), sign-off 'Customer Support Team'."
    )


class CaseSummary(BaseModel):
    """Task 3 output: internal case summary for management."""

    case_overview: str = Field(description="One or two sentences: who, what product, what happened.")
    key_issue: str = Field(description="The core problem in one sentence.")
    action_taken: str = Field(
        description="Actions already taken according to the document, or 'No action recorded'."
    )
    current_status: str = Field(description="Current status of the case with brief justification.")
    recommended_next_action: str = Field(
        description="Concrete next step for the support team. Clearly a recommendation."
    )
    priority: Literal["Low", "Medium", "High"] = Field(
        description="High for escalations, safety or financial loss; Low for inquiries."
    )


# Minimum share of an evidence quote's words that must occur in the source document.
# Tolerates small paraphrases by the verifier while rejecting quotes taken from the email.
EVIDENCE_WORD_COVERAGE = 0.8
# Minimum share of a claim's words that must occur in the email for it to count as an
# email claim at all (small models sometimes list sentences from the document instead).
CLAIM_WORD_COVERAGE = 0.6


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _coverage(fragment: str, text: str) -> float:
    fragment_words = _words(fragment)
    if not fragment_words:
        return 0.0
    text_words = set(_words(text))
    return sum(word in text_words for word in fragment_words) / len(fragment_words)


class ClaimCheck(BaseModel):
    claim: str = Field(description="One factual statement copied word-for-word from the EMAIL.")
    evidence_quote: str | None = Field(
        description="Exact sentence or phrase copied word-for-word from the source document that "
        "supports the claim. null if the document contains no such text."
    )
    supported_by_document: bool = Field(
        description="true only if evidence_quote explicitly supports the claim."
    )

    def is_verified(self, document_text: str) -> bool:
        """LLM verdict AND a deterministic check that the quoted evidence really exists."""
        if not self.supported_by_document or not self.evidence_quote:
            return False
        if len(_words(self.evidence_quote)) < 2:
            return False
        return _coverage(self.evidence_quote, document_text) >= EVIDENCE_WORD_COVERAGE


class GroundingReport(BaseModel):
    """Output of the grounding check: every factual claim in the email and its evidence."""

    claims: list[ClaimCheck] = Field(
        description="Every factual claim about the customer, product, company policy, offerings or actions."
    )

    def unsupported_claims(self, document_text: str, email_text: str) -> list[str]:
        return [
            check.claim
            for check in self.claims
            if _coverage(check.claim, email_text) >= CLAIM_WORD_COVERAGE
            and not check.is_verified(document_text)
        ]
