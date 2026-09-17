"""Task 3: Document + extracted data -> CaseSummary."""

from src import prompts
from src.ingestion.document_loader import LoadedDocument
from src.llm.base import LLMClient
from src.schemas import CaseSummary, ComplaintExtraction


def generate_case_summary(
    llm: LLMClient, document: LoadedDocument, extraction: ComplaintExtraction
) -> CaseSummary:
    user_prompt = prompts.SUMMARY_USER.format(
        extracted_json=extraction.model_dump_json(indent=2),
        document_text=document.text,
    )
    return llm.generate_structured(
        system_prompt=prompts.SUMMARY_SYSTEM,
        user_prompt=user_prompt,
        schema=CaseSummary,
        task_name=f"{document.document_id}:summary",
    )
