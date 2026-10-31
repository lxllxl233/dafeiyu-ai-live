"""性格层叠加：个人（常驻）→ 直播（额外）→ 游戏（再额外）。

目录约定：

```
persona/<id>/card.json          # 个人性格卡
persona/<id>/style.md           # 个人性格说话规则（常驻）
persona/live/card.json          # 直播性格卡（可选）
persona/live/style.md           # 直播时叠加的规则
games/<game-id>/persona/layer.md  # 玩该游戏时再叠加的规则
```

叠加顺序是固定的：``personal`` → ``live`` → ``game``。后一层只补充、不推翻前一层；
拼出来的文本放在系统提示词最前面，游戏/聊天自己的规则跟在后面。这样同一个模型
既能保持底色，又能在直播、打牌时换语气。

游戏层属于游戏包（``games/<id>/persona/layer.md``），核心不写死任何游戏 id；
找不到就自动跳过，不影响游戏运行。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from .card import PersonaCard

_MAX_TEXT = 6000
_SCOPE_LABEL = {"personal": "个人性格", "live": "直播性格", "game": "游戏性格"}
_SCOPE_NOTE = {
    "personal": "常驻",
    "live": "叠加在个人性格上，只在直播/上台时加载",
    "game": "叠加在直播性格上，只在玩这个游戏时加载",
}


@dataclass(frozen=True, slots=True)
class Layer:
    scope: str
    id: str
    name: str
    title: str
    text: str
    source: str
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "label": _SCOPE_LABEL.get(self.scope, self.scope),
            "note": _SCOPE_NOTE.get(self.scope, ""),
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "text": self.text,
            "source": self.source,
            "active": bool(self.active),
            "chars": len(self.text),
        }


def _read_text(path: Path, limit: int = _MAX_TEXT) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()[:limit]
    except OSError:
        return ""


def _load_card(folder: Path, fallback_id: str) -> PersonaCard | None:
    card_path = folder / "card.json"
    if not card_path.is_file():
        return None
    try:
        raw = json.loads(card_path.read_text(encoding="utf-8"))
        raw.setdefault("id", fallback_id)
        return PersonaCard.from_dict(raw, source=paths.rel(card_path))
    except (OSError, ValueError, TypeError):
        return None


class PersonaStack:
    """按顺序叠好的性格层；`prompt()` 给 LLM，`to_dict()` 给设置页/控制台。"""

    def __init__(self, card: PersonaCard, layers: list[Layer]) -> None:
        self.card = card
        self.layers = layers

    @classmethod
    def load(cls, persona_id: str = "dafeiyu", game_id: str = "",
             live: bool = False, *, include_inactive: bool = False,
             base: Path | None = None, root: Path | None = None) -> "PersonaStack":
        base = Path(base or paths.PERSONA)
        root = Path(root or paths.ROOT)

        card = None
        base_dir = base / str(persona_id or "dafeiyu")
        if (base_dir / "card.json").is_file():
            card = _load_card(base_dir, persona_id)
        if card is None:
            card = PersonaCard.load(persona_id, base=base)
        layers = [Layer(
            scope="personal", id=card.id, name=card.name, title=card.title,
            text=_read_text(base_dir / "style.md"),
            source=paths.rel(base_dir / "style.md") if (base_dir / "style.md").is_file() else "",
            active=True,
        )]

        live_dir = base / "live"
        live_card = _load_card(live_dir, "live")
        live_text = _read_text(live_dir / "style.md")
        if live_card is not None or live_text:
            layers.append(Layer(
                scope="live",
                id=(live_card.id if live_card else "live"),
                name=(live_card.name if live_card else card.name),
                title=(live_card.title if live_card else "直播"),
                text=live_text, source=paths.rel(live_dir / "style.md") if live_text else "",
                active=bool(live),
            ))

        game_text = _game_text(root, game_id)
        if game_text or (include_inactive and game_id):
            game_name = _game_name(root, game_id)
            layers.append(Layer(
                scope="game", id=str(game_id or ""), name=game_name,
                title=game_name, text=game_text,
                source=_game_source(root, game_id, game_text),
                active=bool(game_id and game_text),
            ))

        if not include_inactive:
            layers = [layer for layer in layers if layer.active]
        return cls(card, layers)

    # ---------------------------------------------------------------- 拼接
    def active(self) -> list[Layer]:
        return [layer for layer in self.layers if layer.active and layer.text.strip()]

    def prompt(self) -> str:
        """把各层拼成一段系统提示词前缀；没有额外层时只返回个人层。"""
        blocks: list[str] = []
        for layer in self.active():
            head = f"【{_SCOPE_LABEL.get(layer.scope, layer.scope)}】"
            if layer.scope == "live":
                head += "（叠加在个人性格上，只在直播/上台时生效）"
            elif layer.scope == "game":
                head += f"（叠加在直播性格上，只在玩《{layer.name}》时生效）"
            else:
                head += "（常驻）"
            blocks.append(f"{head}\n{layer.text.strip()}")
        return "\n\n".join(blocks).strip()

    def to_dict(self) -> dict:
        return {
            "persona": self.card.id,
            "name": self.card.name,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    def __len__(self) -> int:
        return len(self.layers)


# ---------------------------------------------------------------- 游戏层
def _game_dir(root: Path, game_id: str) -> Path:
    return root / "games" / str(game_id or "") / "persona"


def _game_text(root: Path, game_id: str) -> str:
    if not game_id:
        return ""
    folder = _game_dir(root, game_id)
    for name in ("layer.md", "layer.txt"):
        path = folder / name
        if path.is_file():
            return _read_text(path)
    alt = root / "persona" / "games" / str(game_id) / "layer.md"
    if alt.is_file():
        return _read_text(alt)
    return ""


def _game_source(root: Path, game_id: str, text: str) -> str:
    if not game_id or not text:
        return ""
    for path in (_game_dir(root, game_id) / "layer.md",
                 _game_dir(root, game_id) / "layer.txt",
                 root / "persona" / "games" / str(game_id) / "layer.md"):
        if path.is_file():
            return paths.rel(path)
    return ""


def _game_name(root: Path, game_id: str) -> str:
    if not game_id:
        return ""
    try:
        raw = json.loads((root / "games" / str(game_id) / "game.json")
                         .read_text(encoding="utf-8"))
        return str(raw.get("name") or raw.get("label") or game_id)
    except (OSError, ValueError):
        return str(game_id)


def prompt_prefix(cfg, game_id: str = "", live: bool = False) -> str:
    """给提示词用的便捷入口：读 cfg.persona / cfg.persona_live。"""
    persona_id = str(getattr(cfg, "persona", "dafeiyu") or "dafeiyu")
    allow_live = bool(getattr(cfg, "persona_live", True)) if live else False
    stack = PersonaStack.load(persona_id, game_id=game_id, live=allow_live)
    return stack.prompt()


__all__ = ["Layer", "PersonaStack", "prompt_prefix"]
