"""Persist workflow outputs:

output/
├── structured_data/<doc>.json
├── customer_emails/<doc>.txt
├── case_summaries/<doc>.md
└── final_report.csv
"""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from src.ingestion.document_loader import LoadedDocument
from src.logger import get_logger
from src.schemas import CaseSummary, ComplaintExtraction, CustomerEmail

if TYPE_CHECKING:
    from src.workflow import DocumentResult

logger = get_logger(__name__)

REPORT_COLUMNS = [
    "file_name",
    "file_type",
    "processing_status",
    "customer_name",
    "email",
    "phone_number",
    "product_or_service",
    "complaint_category",
    "is_complaint",
    "escalation_required",
    "supporting_document_available",
    "overall_case_status",
    "priority",
    "issue_description",
    "email_generated",
    "email_grounding_check",
    "unsupported_claims",
    "summary_generated",
    "processing_seconds",
    "errors",
]


class OutputWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.structured_dir = output_dir / "structured_data"
        self.emails_dir = output_dir / "customer_emails"
        self.summaries_dir = output_dir / "case_summaries"
        for directory in (self.structured_dir, self.emails_dir, self.summaries_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def write_structured_data(self, document: LoadedDocument, extraction: ComplaintExtraction) -> Path:
        path = self.structured_dir / f"{document.document_id}.json"
        payload = {
            "source_file": document.file_name,
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "data": extraction.model_dump(mode="json"),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def write_customer_email(
        self,
        document: LoadedDocument,
        extraction: ComplaintExtraction,
        email: CustomerEmail,
        grounding_status: str = "",
        unsupported_claims: list[str] | None = None,
    ) -> Path:
        path = self.emails_dir / f"{document.document_id}.txt"
        recipient = extraction.email or "(email address not available in source document)"
        header = f"To: {recipient}\nSubject: {email.subject}\n"
        if grounding_status:
            header += f"X-Grounding-Check: {grounding_status}\n"
        if unsupported_claims:
            header += "X-Review-Required: statements not found in source document:\n"
            header += "".join(f"  - {claim}\n" for claim in unsupported_claims)
        content = f"{header}\n{email.body.strip()}\n"
        path.write_text(content, encoding="utf-8")
        return path

    def write_case_summary(
        self, document: LoadedDocument, extraction: ComplaintExtraction, summary: CaseSummary
    ) -> Path:
        path = self.summaries_dir / f"{document.document_id}.md"
        content = (
            f"# Case Summary - {document.document_id}\n\n"
            f"| Field | Value |\n|---|---|\n"
            f"| Source file | {document.file_name} |\n"
            f"| Customer | {extraction.customer_name or 'Unknown'} |\n"
            f"| Category | {extraction.complaint_category.value} |\n"
            f"| Escalation required | {extraction.escalation_required} |\n"
            f"| Priority | {summary.priority} |\n\n"
            f"## Case Overview\n{summary.case_overview}\n\n"
            f"## Key Issue\n{summary.key_issue}\n\n"
            f"## Action Taken\n{summary.action_taken}\n\n"
            f"## Current Status\n{summary.current_status}\n\n"
            f"## Recommended Next Action\n{summary.recommended_next_action}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path

    def write_final_report(self, results: list["DocumentResult"]) -> Path:
        path = self.output_dir / "final_report.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
            writer.writeheader()
            for result in results:
                writer.writerow(self._report_row(result))
        logger.info("Final report written to %s", path)
        return path

    @staticmethod
    def _report_row(result: "DocumentResult") -> dict:
        row = {column: "" for column in REPORT_COLUMNS}
        row.update(
            file_name=result.file_name,
            file_type=result.file_type,
            processing_status=result.status,
            email_generated="Yes" if result.email else "No",
            email_grounding_check=result.email_grounding,
            unsupported_claims=" ; ".join(result.unsupported_claims),
            summary_generated="Yes" if result.summary else "No",
            processing_seconds=result.duration_seconds,
            errors=" ; ".join(result.errors),
        )
        if result.extraction:
            data = result.extraction.model_dump(mode="json")
            row.update({key: ("" if value is None else value) for key, value in data.items() if key in row})
        if result.summary:
            row["priority"] = result.summary.priority
        return row
