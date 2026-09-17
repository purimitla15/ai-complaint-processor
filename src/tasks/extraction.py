"""Task 1: Document -> ComplaintExtraction."""

from src import prompts
from src.ingestion.document_loader import LoadedDocument
from src.llm.base import LLMClient
from src.schemas import ComplaintExtraction


def extract_case_data(llm: LLMClient, document: LoadedDocument) -> ComplaintExtraction:
    user_prompt = prompts.EXTRACTION_USER.format(
        file_name=document.file_name, document_text=document.text
    )
    return llm.generate_structured(
        system_prompt=prompts.EXTRACTION_SYSTEM,
        user_prompt=user_prompt,
        schema=ComplaintExtraction,
        task_name=f"{document.document_id}:extraction",
    )
