"""RAG (Retrieval-Augmented Generation) utilities (Sprint P10B1 onward).

Deterministic text chunking (app/rag/chunking.py), text extraction
(app/rag/extraction.py, Sprint P10B2), and pure retrieval-scoring logic
(app/rag/retrieval.py, Sprint P10B4). Ingestion orchestration lives in
app/services/ingestion_service.py and similarity search lives in
app/services/retrieval_service.py, not here - this package stays
DB/FastAPI-free, same convention as app/llm/ and app/storage/. No
runtime RAG prompt injection, chat/agent integration, or answer
generation lives here yet - see app/services/retrieval_service.py's
docstring for exact current scope.
"""
