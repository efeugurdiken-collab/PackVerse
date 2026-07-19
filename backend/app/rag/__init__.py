"""RAG (Retrieval-Augmented Generation) utilities (Sprint P10B1 onward).

Deterministic text chunking (app/rag/chunking.py) and text extraction
(app/rag/extraction.py, Sprint P10B2). Ingestion orchestration itself
lives in app/services/ingestion_service.py, not here - this package stays
DB/FastAPI-free, same convention as app/llm/ and app/storage/. No
retrieval code lives here yet - see app/services/ingestion_service.py's
docstring for exact current scope.
"""
