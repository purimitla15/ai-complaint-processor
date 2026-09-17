"""Workflow orchestration.

Per document:

    load text ──► [1] structured extraction ──┬──► [2] customer email ──► grounding check ──► (revise once)
                                              └──► [3] internal case summary

Across documents: a thread pool processes up to MAX_WORKERS documents at once.
A failure in one document (or one task) never stops the rest of the batch.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion.document_loader import (
    DocumentLoadError,
    LoadedDocument,
    discover_documents,
    load_document,
)
from src.llm.base import LLMClient, LLMError
from src.logger import get_logger
from src.output.writers import OutputWriter
from src.schemas import CaseSummary, ComplaintExtraction, CustomerEmail
from src.tasks.case_summary import generate_case_summary
from src.tasks.email_generator import generate_customer_email
from src.tasks.extraction import extract_case_data
from src.tasks.grounding_check import check_email_grounding

logger = get_logger(__name__)


class ProcessingStatus:
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"  # extraction worked but email and/or summary failed
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # unsupported file type


class GroundingStatus:
    PASSED = "Passed"  # first draft fully supported by the document
    REVISED = "Revised"  # first draft had unsupported claims; revised draft passed
    FLAGGED = "Flagged"  # revised draft still has unsupported claims -> human review
    NOT_CHECKED = "Not checked"


@dataclass
class DocumentResult:
    file_name: str
    status: str
    file_type: str = ""
    extraction: ComplaintExtraction | None = None
    email: CustomerEmail | None = None
    summary: CaseSummary | None = None
    email_grounding: str = ""
    unsupported_claims: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class ComplaintProcessingWorkflow:
    def __init__(
        self,
        llm: LLMClient,
        writer: OutputWriter,
        max_workers: int = 2,
        grounding_check: bool = True,
    ) -> None:
        self.llm = llm
        self.writer = writer
        self.max_workers = max_workers
        self.grounding_check = grounding_check

    # -- batch level ---------------------------------------------------------
    def run(self, data_dir: Path) -> list[DocumentResult]:
        files, unsupported = discover_documents(data_dir)
        logger.info(
            "Found %d supported document(s) and %d unsupported file(s) in %s",
            len(files), len(unsupported), data_dir,
        )

        results = [
            DocumentResult(
                file_name=path.name,
                file_type=path.suffix.lstrip(".").lower(),
                status=ProcessingStatus.SKIPPED,
                errors=[f"Unsupported file type '{path.suffix}'"],
            )
            for path in unsupported
        ]
        for skipped in results:
            logger.warning("Skipping %s: %s", skipped.file_name, skipped.errors[0])

        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="doc") as pool:
            futures = {pool.submit(self.process_document, path): path for path in files}
            for future in as_completed(futures):
                results.append(future.result())

        results.sort(key=lambda r: r.file_name)
        self.writer.write_final_report(results)
        return results

    # -- document level ------------------------------------------------------
    def process_document(self, path: Path) -> DocumentResult:
        started = time.perf_counter()
        result = DocumentResult(
            file_name=path.name,
            file_type=path.suffix.lstrip(".").lower(),
            status=ProcessingStatus.FAILED,
        )
        logger.info("Processing %s", path.name)

        try:
            document = load_document(path)
            result.extraction = extract_case_data(self.llm, document)
            self.writer.write_structured_data(document, result.extraction)

            self._run_generation_tasks(document, result)
            result.status = (
                ProcessingStatus.SUCCESS if not result.errors else ProcessingStatus.PARTIAL
            )
        except DocumentLoadError as exc:
            result.errors.append(f"Load error: {exc}")
            logger.error("Could not load %s: %s", path.name, exc)
        except LLMError as exc:
            result.errors.append(f"Extraction error: {exc}")
            logger.error("Extraction failed for %s: %s", path.name, exc)
        except Exception as exc:  # last-resort guard so one bad file never kills the batch
            result.errors.append(f"Unexpected error: {type(exc).__name__}: {exc}")
            logger.exception("Unexpected error while processing %s", path.name)

        result.duration_seconds = round(time.perf_counter() - started, 2)
        logger.info(
            "Finished %s -> %s in %.1fs", path.name, result.status, result.duration_seconds
        )
        return result

    def _run_generation_tasks(self, document: LoadedDocument, result: DocumentResult) -> None:
        """Email and summary are independent of each other, so run them in parallel."""
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"{document.document_id}") as pool:
            email_future = pool.submit(self._produce_grounded_email, document, result)
            summary_future = pool.submit(generate_case_summary, self.llm, document, result.extraction)

            try:
                result.email = email_future.result()
                self.writer.write_customer_email(
                    document, result.extraction, result.email, result.email_grounding, result.unsupported_claims
                )
            except LLMError as exc:
                result.errors.append(f"Email generation error: {exc}")
                logger.error("Email generation failed for %s: %s", document.file_name, exc)

            try:
                result.summary = summary_future.result()
                self.writer.write_case_summary(document, result.extraction, result.summary)
            except LLMError as exc:
                result.errors.append(f"Summary generation error: {exc}")
                logger.error("Summary generation failed for %s: %s", document.file_name, exc)

    def _produce_grounded_email(self, document: LoadedDocument, result: DocumentResult) -> CustomerEmail:
        """Generate -> verify -> (revise once with reviewer feedback -> verify again)."""
        email = generate_customer_email(self.llm, document, result.extraction)
        if not self.grounding_check:
            result.email_grounding = GroundingStatus.NOT_CHECKED
            return email

        unsupported = check_email_grounding(self.llm, document, email).unsupported_claims(document.text, email.body)
        if not unsupported:
            result.email_grounding = GroundingStatus.PASSED
            return email

        logger.warning(
            "[%s] email draft has %d unsupported claim(s), revising: %s",
            document.document_id, len(unsupported), unsupported,
        )
        email = generate_customer_email(self.llm, document, result.extraction, rejected_claims=unsupported)
        unsupported = check_email_grounding(self.llm, document, email).unsupported_claims(document.text, email.body)

        if unsupported:
            result.email_grounding = GroundingStatus.FLAGGED
            result.unsupported_claims = unsupported
            logger.warning(
                "[%s] revised email still has unsupported claims - flagged for human review",
                document.document_id,
            )
        else:
            result.email_grounding = GroundingStatus.REVISED
        return email
