"""Summarization module for generating meeting summaries using AI."""

from .generator import SummaryGenerator, SummaryError
from .templates import SummaryTemplate, TemplateRegistry

__all__ = [
    'SummaryGenerator',
    'SummaryError',
    'SummaryTemplate',
    'TemplateRegistry',
]
