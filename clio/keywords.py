from enum import Enum


class Keyword(str, Enum):
    """Closed enum of every keyword recognized by the lexer.

    Populated incrementally as each phase adds language features.
    """
    STEP = "STEP"
    MODE = "MODE"
    EXACT = "exact"
