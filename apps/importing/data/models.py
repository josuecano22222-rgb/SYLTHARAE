"""
Data models for the domain import application.

These are the value objects every stage of the import pipeline exchanges:

    raw rows --DataParser--> Domain --TermProcessor--> database

``Domain`` groups the terms of one classification domain (it becomes a
category); ``Term`` is a single word or a multi-word phrase inside it;
``ImportResult``/``Statistics`` carry the per-domain and overall outcome.

Note: this module was missing from the repository (every consumer under
apps/importing imports it, but the file itself was never committed). It is
reconstructed from the exact contract its consumers use: Term.text /
term.term_type / is_single_word() / is_phrase() / word_count, Domain.name /
domain.terms / len(domain), and the ImportResult fields the orchestrator and
StatisticsManager read and write.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class TermType(Enum):
    """The two shapes a term can take."""
    WORD = "word"        # single word, no whitespace
    PHRASE = "phrase"    # two or more whitespace-separated words


@dataclass
class Term:
    """One importable term: a single word or a multi-word phrase."""
    text: str
    term_type: TermType = None

    def __post_init__(self):
        self.text = (self.text or "").strip()
        # The type is derived from the text unless the caller insists:
        # whitespace is what separates a word from a phrase.
        if self.term_type is None:
            self.term_type = (
                TermType.PHRASE if " " in self.text else TermType.WORD
            )

    def is_single_word(self) -> bool:
        return self.term_type == TermType.WORD

    def is_phrase(self) -> bool:
        return self.term_type == TermType.PHRASE

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Term({self.text!r}, {self.term_type.value})"


@dataclass
class Domain:
    """One classification domain and every term that belongs to it."""
    name: str
    terms: List[Term] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.terms)

    def __iter__(self):
        return iter(self.terms)

    def add_term(self, text: str) -> Term:
        term = Term(text)
        self.terms.append(term)
        return term

    def __repr__(self) -> str:
        return f"Domain({self.name!r}, {len(self.terms)} terms)"


@dataclass
class ImportResult:
    """The outcome of importing one domain."""
    domain_name: str
    total_terms: int = 0
    imported_words: int = 0
    imported_phrases: int = 0
    skipped: int = 0
    errors: int = 0

    @property
    def imported(self) -> int:
        return self.imported_words + self.imported_phrases

    def __repr__(self) -> str:
        return (
            f"ImportResult({self.domain_name!r}: {self.imported_words} words, "
            f"{self.imported_phrases} phrases, {self.skipped} skipped, "
            f"{self.errors} errors)"
        )


@dataclass
class Statistics:
    """Aggregated import outcome across every processed domain."""
    total_domains: int = 0
    total_terms: int = 0
    total_words: int = 0
    total_phrases: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    results: List[ImportResult] = field(default_factory=list)

    def add_result(self, result: ImportResult) -> None:
        """Fold one domain's result into the totals."""
        self.total_terms += result.total_terms
        self.total_words += result.imported_words
        self.total_phrases += result.imported_phrases
        self.total_skipped += result.skipped
        self.total_errors += result.errors
        self.results.append(result)
