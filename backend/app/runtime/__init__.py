"""AI Runtime (Sprint P6): executes an AgentDefinition through the LLM
Gateway (app.llm), managing AgentRun lifecycle and persistence.

No FastAPI or transport-layer imports in this package except where the
package boundary explicitly requires it (there is none here - see
app/api/v1/runs.py for the HTTP-facing layer). No autonomous multi-step
agent loops, MCP, RAG, or tool execution - this sprint executes exactly
one LLM Gateway call per run, per the sprint spec's scope.
"""
