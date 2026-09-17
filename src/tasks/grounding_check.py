"""Verifier step for Task 2: check a generated email against the source document."""

from src import prompts
from src.ingestion.document_loader import LoadedDocument
from src.llm.base import LLMClient
from src.schemas import CustomerEmail, GroundingReport


def check_email_grounding(
    llm: LLMClient, document: LoadedDocument, email: CustomerEmail
) -> GroundingReport:
    user_prompt = prompts.GROUNDING_CHECK_USER.format(
        document_text=document.text, email_body=email.body
    )
    return llm.generate_structured(
        system_prompt=prompts.GROUNDING_CHECK_SYSTEM,
        user_prompt=user_prompt,
        schema=GroundingReport,
        task_name=f"{document.document_id}:grounding_check",
    )
