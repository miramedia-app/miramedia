"""Unit tests for settings nested override application."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from miramedia.settings.service import _apply_nested_overrides


class SampleEnum(Enum):
    alpha = "a"
    beta = "b"


class NestedModel(BaseModel):
    label: str = "default"


class OverrideTarget(BaseModel):
    scalar: int = 1
    flag: bool = False
    kind: SampleEnum = SampleEnum.alpha
    optional_kind: Optional[SampleEnum] = None  # noqa: UP045 — Optional[] matches _apply_nested_overrides Union introspection
    path_field: Path = Path("/var/data")
    nested: NestedModel = NestedModel()
    items: list[NestedModel] = []


def test_scalar_override() -> None:
    obj = OverrideTarget()
    _apply_nested_overrides(obj, {"scalar": 42})
    assert obj.scalar == 42


def test_enum_string_override() -> None:
    obj = OverrideTarget()
    _apply_nested_overrides(obj, {"kind": "beta"})
    assert obj.kind is SampleEnum.beta


def test_optional_enum_string_override() -> None:
    obj = OverrideTarget()
    _apply_nested_overrides(obj, {"optional_kind": "alpha"})
    assert obj.optional_kind is SampleEnum.alpha


def test_nested_model_override() -> None:
    obj = OverrideTarget()
    _apply_nested_overrides(obj, {"nested": {"label": "custom"}})
    assert obj.nested.label == "custom"


def test_list_of_models_override() -> None:
    obj = OverrideTarget()
    _apply_nested_overrides(obj, {"items": [{"label": "one"}, {"label": "two"}]})
    assert len(obj.items) == 2
    assert obj.items[0].label == "one"
    assert obj.items[1].label == "two"


def test_path_string_override() -> None:
    obj = OverrideTarget()
    _apply_nested_overrides(obj, {"path_field": "/var/data"})
    assert obj.path_field == Path("/var/data")


def test_unknown_key_is_ignored() -> None:
    obj = OverrideTarget()
    before = obj.model_dump()
    _apply_nested_overrides(obj, {"bogus": "value"})
    assert obj.model_dump() == before
