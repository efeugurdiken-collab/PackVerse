"""Workflow Orchestration (Sprint P7): executes a WorkflowDefinition's
ordered steps, each through the Sprint P6 AI Runtime.

No FastAPI or transport-layer imports in this package (see
app/api/v1/workflow_runs.py for the HTTP-facing layer). Never imports
app.llm or app.services.llm_service directly - every LLM call happens
through app.runtime, per the sprint's "Do not bypass the P6 runtime" /
"The executor must not call provider adapters or the LLM Gateway
directly". Sequential execution only - no parallel steps, no DAG
branching, no cron/scheduled runs, no distributed queue, per this
sprint's explicit scope.
"""
