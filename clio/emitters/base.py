from abc import ABC, abstractmethod
from pathlib import Path

from clio.ir.graph import FlowGraph


class BaseEmitter(ABC):
    @abstractmethod
    def emit(self, graph: FlowGraph, output_dir: Path) -> None: ...
