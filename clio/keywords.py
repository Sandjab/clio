from enum import Enum


class Keyword(str, Enum):
    """Closed enum of every keyword recognized by the lexer.

    Populated incrementally as each phase adds language features.
    """
    STEP = "STEP"
    MODE = "MODE"
    EXACT = "exact"
    JUDGMENT = "judgment"
    TAKES = "TAKES"
    GIVES = "GIVES"
    INT = "int"
    FLOAT = "float"
    STR = "str"
    BOOL = "bool"
    LIST = "List"
    ENUM = "enum"
    CONTRACT = "CONTRACT"
    SHAPE = "SHAPE"
    CSV = "CSV"
    FLOW = "FLOW"
    ASSERT = "ASSERT"
