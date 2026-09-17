"""Task 2: Document + extracted data -> CustomerEmail."""

from src import prompts
from src.ingestion.document_loader import LoadedDocument
from src.llm.base import LLMClient
from src.schemas import ComplaintExtraction, CustomerEmail


def generate_customer_email(
    llm: LLMClient,
    document: LoadedDocument,
    extraction: ComplaintExtraction,
    rejected_claims: list[str] | None = None,
) -> CustomerEmail:
    """Generate the reply email. `rejected_claims` feeds back a failed grounding check."""
    user_prompt = prompts.EMAIL_USER.format(
        extracted_json=extraction.model_dump_json(indent=2),
        document_text=document.text,
    )
    if rejected_claims:
        user_prompt += prompts.EMAIL_REVISION_FEEDBACK.format(
            unsupported_claims="\n".join(f"- {claim}" for claim in rejected_claims)
        )
    return llm.generate_structured(
        system_prompt=prompts.EMAIL_SYSTEM,
        user_prompt=user_prompt,
        schema=CustomerEmail,
        task_name=f"{document.document_id}:email" + (":revision" if rejected_claims else ""),
    )
