from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .models import Paper

_SECTIONS = {"AUTHOR", "TITLE", "ABSTRACT"}


@dataclass
class KeywordRules:
    author_patterns: List[re.Pattern]
    title_patterns: List[re.Pattern]
    abstract_patterns: List[re.Pattern]

    @property
    def keyword_count(self) -> int:
        return len(self.author_patterns) + len(self.title_patterns) + len(self.abstract_patterns)

    def has_rules(self) -> bool:
        return self.keyword_count > 0

    def matches(self, paper: Paper) -> bool:
        checks = []

        if self.author_patterns:
            checks.append(
                any(pattern.search(author) for pattern in self.author_patterns for author in paper.authors)
            )

        if self.title_patterns:
            checks.append(any(pattern.search(paper.title) for pattern in self.title_patterns))

        if self.abstract_patterns:
            checks.append(any(pattern.search(paper.abstract) for pattern in self.abstract_patterns))

        if not checks:
            return True
        return any(checks)


def load_keyword_rules(path: str) -> KeywordRules:
    file_path = Path(path)
    if not file_path.exists():
        return KeywordRules([], [], [])

    blocks = {"AUTHOR": [], "TITLE": [], "ABSTRACT": []}
    active_section = None

    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        upper = line.upper()
        if upper in _SECTIONS:
            active_section = upper
            continue

        if active_section is None:
            continue
        blocks[active_section].append(line)

    return KeywordRules(
        author_patterns=[_compile_pattern(pattern) for pattern in blocks["AUTHOR"]],
        title_patterns=[_compile_pattern(pattern) for pattern in blocks["TITLE"]],
        abstract_patterns=[_compile_pattern(pattern) for pattern in blocks["ABSTRACT"]],
    )


def filter_papers_by_keywords(papers: Iterable[Paper], rules: KeywordRules) -> List[Paper]:
    items = list(papers)
    if not rules.has_rules():
        return items
    return [paper for paper in items if rules.matches(paper)]


def _compile_pattern(keyword: str) -> re.Pattern:
    try:
        return re.compile(keyword, flags=re.IGNORECASE)
    except re.error:
        escaped = re.escape(keyword).replace(r"\*", ".*")
        return re.compile(escaped, flags=re.IGNORECASE)
