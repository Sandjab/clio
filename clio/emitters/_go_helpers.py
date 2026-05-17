"""Go-specific renderers + embedded Go runtime templates.

Filled progressively across Phase 1-6. Imported by `go.py`.

CLAUDE.md rule "emitters never import from each other" continues to hold:
this module is a helper for `go.py` only; cross-emitter sharing happens via
`_shared_utils.py`.
"""
from __future__ import annotations
