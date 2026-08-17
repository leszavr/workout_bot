"""FSM-состояния анкеты.

Состояния генерируются автоматически из единого списка QUESTIONS —
порядок и набор состояний не могут разойтись с описанием вопросов.
"""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup, StatesGroupMeta

from src.application.questionnaire.questions import QUESTIONS

_namespace: dict[str, State] = {q.id: State() for q in QUESTIONS}
_namespace["review"] = State()
_namespace["confirm"] = State()

QuestionnaireStates = StatesGroupMeta("QuestionnaireStates", (StatesGroup,), _namespace)
