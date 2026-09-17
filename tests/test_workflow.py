"""Workflow tests using a fake LLM - no network or model required."""

import csv
from pathlib import Path

import pytest
from pydantic import BaseModel

from src.llm.base import LLMClient, LLMError, TransientLLMError
from src.output.writers import OutputWriter
from src.schemas import CaseSummary, ClaimCheck, ComplaintExtraction, CustomerEmail, GroundingReport
from src.workflow import ComplaintProcessingWorkflow, GroundingStatus, ProcessingStatus

EXTRACTION_JSON = ComplaintExtraction(
    customer_name="Test User",
    email="test@example.com",
    phone_number=None,
    product_or_service="Mixer grinder",
    complaint_category="Product Defect",
    issue_description="Mixer grinder stopped working.",
    resolution_provided=None,
    is_complaint="Yes",
    escalation_required="No",
    supporting_document_available="No",
    overall_case_status="Open",
).model_dump_json()

EMAIL_JSON = CustomerEmail(subject="Your complaint", body="Dear Test User, ...").model_dump_json()

SUMMARY_JSON = CaseSummary(
    case_overview="Overview",
    key_issue="Issue",
    action_taken="No action recorded",
    current_status="Open",
    recommended_next_action="Call customer",
    priority="Medium",
).model_dump_json()


GROUNDED = '{"claims": []}'


class FakeLLM(LLMClient):
    def __init__(self, responses: dict[str, list[str]], **kwargs):
        super().__init__(model="fake", max_retries=3, **kwargs)
        self.responses = responses
        self.calls: list[str] = []

    def _complete_json(self, model, system_prompt, user_prompt, schema: type[BaseModel]) -> str:
        self.calls.append(schema.__name__)
        queue = self.responses[schema.__name__]
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    # Remove exponential backoff delays in tests.
    monkeypatch.setattr("src.llm.base.wait_exponential", lambda **_: (lambda _state: 0))


def make_data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (data / "complaint_a.txt").write_text("Test User: my mixer grinder stopped working after a week.")
    (data / "complaint_b.pdf").write_bytes(b"%PDF-1.4 corrupt")
    (data / "notes.xlsx").write_bytes(b"x")
    return data


def test_end_to_end_batch(tmp_path: Path):
    llm = FakeLLM({
        "ComplaintExtraction": [EXTRACTION_JSON],
        "CustomerEmail": [EMAIL_JSON],
        "CaseSummary": [SUMMARY_JSON],
        "GroundingReport": [GROUNDED],
    })
    output = tmp_path / "output"
    results = ComplaintProcessingWorkflow(llm, OutputWriter(output), max_workers=2).run(make_data_dir(tmp_path))

    statuses = {r.file_name: r.status for r in results}
    assert statuses == {
        "complaint_a.txt": ProcessingStatus.SUCCESS,
        "complaint_b.pdf": ProcessingStatus.FAILED,
        "notes.xlsx": ProcessingStatus.SKIPPED,
    }
    assert (output / "structured_data" / "complaint_a.json").exists()
    assert "To: test@example.com" in (output / "customer_emails" / "complaint_a.txt").read_text()
    assert (output / "case_summaries" / "complaint_a.md").exists()

    rows = list(csv.DictReader((output / "final_report.csv").open()))
    assert len(rows) == 3
    row_a = next(r for r in rows if r["file_name"] == "complaint_a.txt")
    assert row_a["customer_name"] == "Test User"
    assert row_a["priority"] == "Medium"


def test_invalid_json_is_retried_then_succeeds(tmp_path: Path):
    llm = FakeLLM({
        "ComplaintExtraction": ['{"customer_name": "broken"', EXTRACTION_JSON],
        "CustomerEmail": [EMAIL_JSON],
        "CaseSummary": [SUMMARY_JSON],
        "GroundingReport": [GROUNDED],
    })
    result = ComplaintProcessingWorkflow(llm, OutputWriter(tmp_path / "out")).process_document(
        make_data_dir(tmp_path) / "complaint_a.txt"
    )
    assert result.status == ProcessingStatus.SUCCESS
    assert llm.calls.count("ComplaintExtraction") == 2


def test_email_failure_gives_partial_result(tmp_path: Path):
    llm = FakeLLM({
        "ComplaintExtraction": [EXTRACTION_JSON],
        "CustomerEmail": [LLMError("model refused")],
        "CaseSummary": [SUMMARY_JSON],
    })
    result = ComplaintProcessingWorkflow(llm, OutputWriter(tmp_path / "out")).process_document(
        make_data_dir(tmp_path) / "complaint_a.txt"
    )
    assert result.status == ProcessingStatus.PARTIAL
    assert result.summary is not None and result.email is None


DOC_TEXT = "Test User: my mixer grinder stopped working after a week."


def test_grounding_check_rejects_claims_without_real_evidence():
    email = "Your mixer grinder stopped working. The warranty covers the motor. A refund has been issued."
    report = GroundingReport(claims=[
        ClaimCheck(claim="mixer grinder stopped working", evidence_quote="my mixer grinder stopped working", supported_by_document=True),
        # verifier "quotes" the email, not the document -> rejected by the evidence check
        ClaimCheck(claim="The warranty covers the motor", evidence_quote="The warranty covers the motor", supported_by_document=True),
        ClaimCheck(claim="A refund has been issued", evidence_quote=None, supported_by_document=False),
        # verifier listed a sentence that is not in the email at all -> ignored
        ClaimCheck(claim="Test User visited our showroom in Pune yesterday", evidence_quote=None, supported_by_document=False),
    ])
    assert report.unsupported_claims(DOC_TEXT, email) == ["The warranty covers the motor", "A refund has been issued"]


def test_ungrounded_email_is_revised(tmp_path: Path):
    bad = GroundingReport(claims=[ClaimCheck(claim="Dear Test User", evidence_quote=None, supported_by_document=False)])
    good = GroundingReport(claims=[])
    llm = FakeLLM({
        "ComplaintExtraction": [EXTRACTION_JSON],
        "CustomerEmail": [EMAIL_JSON],
        "CaseSummary": [SUMMARY_JSON],
        "GroundingReport": [bad.model_dump_json(), good.model_dump_json()],
    })
    result = ComplaintProcessingWorkflow(llm, OutputWriter(tmp_path / "out")).process_document(
        make_data_dir(tmp_path) / "complaint_a.txt"
    )
    assert result.email_grounding == GroundingStatus.REVISED
    assert llm.calls.count("CustomerEmail") == 2
    assert "X-Grounding-Check: Revised" in (tmp_path / "out" / "customer_emails" / "complaint_a.txt").read_text()


def test_fallback_model_used_when_primary_unavailable():
    class FlakyPrimary(FakeLLM):
        def _complete_json(self, model, *args):
            if model == "fake":
                raise TransientLLMError("503 overloaded")
            return EMAIL_JSON

    llm = FlakyPrimary({}, fallback_model="backup")
    email = llm.generate_structured("sys", "user", CustomerEmail, task_name="test")
    assert email.subject == "Your complaint"
