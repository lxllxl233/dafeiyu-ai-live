"""性格层：把「说什么」从「怎么打/怎么播」里拆出来。

    from dafeiyu.persona import Persona
    p = Persona.load("dafeiyu")
    text = p.compose("它 21，我最多挡 15", "差 6，嗨呀")   # 拼句 + 去前缀
    issues = p.lint(text)                                  # 出口前三问的可执行版

人设卡在 `persona/<id>/card.json`，禁语/招牌/长度都在卡里，不在代码里。
"""

from __future__ import annotations

from .card import PersonaCard, Style
from .lint import LintIssue, clip, compose, sentences, summarize
from .lint import lint as lint_text
from .pacing import Pacing


class Persona:
    def __init__(self, card: PersonaCard | None = None, pacing: Pacing | None = None) -> None:
        self.card = card or PersonaCard.load("dafeiyu")
        self.pacing = pacing or Pacing(style=self.card.style)

    @classmethod
    def load(cls, persona_id: str = "dafeiyu") -> "Persona":
        return cls(PersonaCard.load(persona_id))

    # ---------------------------------------------------------------- 台词
    def compose(self, thought: str = "", meme: str = "") -> str:
        return compose(thought, meme, self.card.style)

    def lint(self, text: str) -> list[LintIssue]:
        card = self.card
        return lint_text(
            text, card.style, card.quips,
            banned_prefixes=card.banned_prefixes,
            banned_labels=card.banned_labels,
            repeat_words=card.repeat_words,
        )

    def lint_report(self, text: str) -> dict:
        return summarize(self.lint(text))

    def check(self, text: str) -> tuple[bool, list[LintIssue]]:
        issues = self.lint(text)
        return (not any(i.level == "FAIL" for i in issues), issues)

    def render(self, thought: str = "", meme: str = "") -> dict:
        text = self.compose(thought, meme)
        issues = self.lint(text)
        return {
            "text": text,
            "lines": sentences(text, self.card.style.max_lines),
            "lint": summarize(issues),
            "persona": self.card.id,
            "pacing": self.pacing.advice(text, self.card.quips),
        }

    def note_sent(self, text: str) -> dict:
        return self.pacing.note_sent(text, self.card.quips)

    # ---------------------------------------------------------------- 展示
    def to_dict(self) -> dict:
        out = self.card.to_dict()
        out["pacing"] = self.pacing.stats()
        return out


__all__ = ["Persona", "PersonaCard", "Style", "LintIssue", "Pacing"]
