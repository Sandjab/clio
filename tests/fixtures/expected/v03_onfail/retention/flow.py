"""FLOW retention.

Auto-generated. Calls steps in chain order, threading state through a dict.
"""

from .steps import load_customers as load_customers_mod
from .steps import detect_churn as detect_churn_mod


def run(**initial: object) -> dict:
    state: dict = dict(initial)
    state['customers'] = load_customers_mod.load_customers(file='customers.csv')
    state['risks'] = detect_churn_mod.detect_churn(customers=state['customers'])
    return state
