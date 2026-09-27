"""
Parameter files and their provenance tags (CLAUDE.md rule 2).

Every numeric parameter in params/*.yaml is a mapping with a ``value`` and a ``tag``.
The tag decides what else must be present:

    [SOURCED]      source          — the named publication
    [CALIBRATED]   target          — what it was fitted against (the fit goes in sources.md)
    [MODELLED]     reasoning       — the assumption, in words
    [PLACEHOLDER]  —               — allowed while building; blocks published output

A bare number anywhere in a params file is an error: there is no untagged value.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

TAGS = {
    "SOURCED": "source",
    "CALIBRATED": "target",
    "MODELLED": "reasoning",
    "PLACEHOLDER": None,
}
# Keys that describe a parameter rather than being parameters themselves.
META_KEYS = {"value", "unit", "tag", "source", "target", "reasoning", "note",
             "price_year", "range"}


class ParamError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    path: str
    value: object
    tag: str
    unit: str | None


def _walk(node, path: str, out: list[Param]) -> None:
    if isinstance(node, dict) and "value" in node:
        tag = node.get("tag")
        if tag not in TAGS:
            raise ParamError(f"{path}: tag {tag!r} is not one of {sorted(TAGS)}")
        need = TAGS[tag]
        if need and not node.get(need):
            raise ParamError(f"{path}: a [{tag}] value needs a non-empty `{need}`")
        if tag == "SOURCED" and "price_year" not in node and node.get("unit", "").startswith("GBP"):
            raise ParamError(f"{path}: a sourced money value needs a `price_year`")
        extra = set(node) - META_KEYS
        if extra:
            raise ParamError(f"{path}: unexpected keys {sorted(extra)} beside `value`")
        out.append(Param(path, node["value"], tag, node.get("unit")))
    elif isinstance(node, dict):
        for k, v in node.items():
            if k.startswith("_"):     # _meta blocks: file-level notes, not parameters
                continue
            _walk(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        raise ParamError(f"{path}: bare number {node!r} — every value needs a tag")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]", out)


def load(path: Path) -> list[Param]:
    doc = yaml.safe_load(path.read_text()) or {}
    out: list[Param] = []
    _walk(doc, "", out)
    return out


def placeholders(params: list[Param]) -> list[Param]:
    return [p for p in params if p.tag == "PLACEHOLDER"]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
