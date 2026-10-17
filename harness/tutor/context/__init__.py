"""
Context orchestration layer: decides WHAT the model sees and WHICH model sees it.

  cues.py       read the child's signals (deterministic)
  planner.py    plan the turn up front (one cheap call, or none)
  router.py     choose the Claude tier for the reply
  assembler.py  build a cache-friendly, token-budgeted prompt
  memory.py     recall before, keep after
  prompts/      the prompt files, documented and versioned

This layer never opens a socket or a cursor. It receives the runtime's LLM
and Store interfaces and calls them.
"""
