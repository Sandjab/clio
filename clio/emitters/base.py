from abc import ABC, abstractmethod
from pathlib import Path

from clio.ir.graph import FlowGraph


class BaseEmitter(ABC):
    @abstractmethod
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
    ) -> None:
        """Emit a target project under `output_dir`.

        `source_path` is the absolute path to the originating `.clio` file, or
        None when the emitter is invoked programmatically (tests, scripts).
        Currently consumed only by `ClaudeSkillEmitter` for the `.clio/`
        sidecar; other emitters accept and ignore it."""
        ...
