"""FLOW classify.

Auto-generated. Calls steps in chain order, threading state through a dict.
"""

from .steps import detect_topic as detect_topic_mod


def run(**initial: object) -> dict:
    state: dict = dict(initial)
    state['topic'] = detect_topic_mod.detect_topic(text='hello')
    return state
