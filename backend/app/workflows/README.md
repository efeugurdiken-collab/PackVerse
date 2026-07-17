# workflows

Runtime implementation of the Workflows specified in the PackVerse OS
Obsidian vault (`06 Workflows/`). Empty through Sprint P6. Sprint P7
(Workflow Orchestration) populated this package: sequential execution of
a WorkflowDefinition's ordered steps, each step run through the Sprint
P6 AI Runtime (`app/runtime/`) - see `exceptions.py`, `models.py` (state
machines), `definition.py` (step-list parsing/validation),
`input_builder.py`, `service.py`, and `executor.py`. Parallel steps, DAG
branching, and cron/scheduled runs remain out of scope - see the Sprint
P7 report's Known Limitations.
