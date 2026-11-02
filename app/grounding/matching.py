import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.schemas.response_models import MatchStatus

_MAX_CANDIDATES = 5
_MIN_TYPO_WORD_LENGTH = 5

_CATERPILLAR_BRANDS = ("cat", "caterpillar")
_LEGAL_SUFFIX_RE = re.compile(r"[ ,]+(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company)\.?$")

# A bare number needs one of these right after it. "320 excavator" names a model, but
# "140 psi" or "320 hours" doesn't, and CAT 140 and CAT 320 are both real machines.
_MACHINE_NOUNS = (
    r"(?:excavators?|dozers?|bulldozers?|loaders?|backhoes?|skid steers?|graders?|trucks?|"
    r"telehandlers?|compactors?|rollers?|generators?|gensets?|machines?|units?)"
)


@dataclass(frozen=True)
class EquipmentMatch:
    status: MatchStatus
    record: Optional[dict[str, Any]] = None
    candidates: list[dict[str, Any]] = field(default_factory=list)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _has_identifier(text: str, identifier: str) -> bool:
    # Not \b: a hyphen is a word boundary, so "CAT320-SE-1042" would match inside
    # "CAT320-SE-1042-B", a different identifier.
    return re.search(rf"(?<![a-z0-9-]){re.escape(identifier.lower())}(?![a-z0-9-])", text) is not None


def _has_model(text: str, model: str) -> bool:
    *brand_words, code = _normalize(model).split(" ")
    brand = " ".join(brand_words)

    # Anchored with \b on both ends: a plain substring check would let "CAT 320" match
    # inside "CAT 3200", a different, real piece of equipment. Exact only, deliberately
    # with no fuzzy tolerance: CAT 320 and CAT 330 are one character apart and both real.
    code_pattern = re.sub(r"([a-z]{2,})(\d)", r"\1[ -]?\2", re.escape(code))
    brand_pattern = "(?:cat|caterpillar)" if brand in _CATERPILLAR_BRANDS else re.escape(brand)

    if brand and re.search(rf"\b{brand_pattern}[ -]?{code_pattern}\b", text):
        return True
    if code.isdigit():
        return re.search(rf"\b{code_pattern}(?= {_MACHINE_NOUNS}\b)", text) is not None
    return re.search(rf"\b{code_pattern}\b", text) is not None


def _customer_phrase(customer: str) -> str:
    return _LEGAL_SUFFIX_RE.sub("", _normalize(customer))


def _has_customer(text: str, customer: str) -> bool:
    return re.search(rf"\b{re.escape(_customer_phrase(customer))}\b", text) is not None


def _is_one_typo_away(typed: str, expected: str) -> bool:
    # One missing, extra, wrong or swapped letter, deliberately not a similarity ratio:
    # "Southridge" and "Northridge" score around 90% alike but are different companies.
    # The first letter must match, and short words and anything with digits stay exact.
    if typed == expected:
        return True
    if len(expected) < _MIN_TYPO_WORD_LENGTH or not expected.isalpha() or typed[0] != expected[0]:
        return False

    if abs(len(typed) - len(expected)) > 1:
        return False
    i = next((k for k, (a, b) in enumerate(zip(typed, expected)) if a != b), min(len(typed), len(expected)))
    if len(typed) != len(expected):
        longer, shorter = (typed, expected) if len(typed) > len(expected) else (expected, typed)
        return longer[i + 1 :] == shorter[i:]
    is_substitution = typed[i + 1 :] == expected[i + 1 :]
    is_swap = typed[i : i + 2] == expected[i : i + 2][::-1] and typed[i + 2 :] == expected[i + 2 :]
    return is_substitution or is_swap


def _has_misspelled_customer(text: str, customer: str) -> bool:
    words = re.findall(r"[a-z0-9]+", text)
    expected = _customer_phrase(customer).split(" ")
    if len(expected) < 2:
        return False
    windows = (words[i : i + len(expected)] for i in range(len(words) - len(expected) + 1))
    return any(all(_is_one_typo_away(t, e) for t, e in zip(window, expected)) for window in windows)


def _is_named_by_identifier(text: str, record: dict[str, Any]) -> bool:
    serial_number = record.get("serial_number")
    return _has_identifier(text, record["equipment_id"]) or bool(serial_number and _has_identifier(text, serial_number))


def _shortlist(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda r: (r["customer"], r["model"], r["equipment_id"]))
    return ordered[:_MAX_CANDIDATES]


def match_equipment(records: list[dict[str, Any]], request_text: str) -> EquipmentMatch:
    """Link free text to a record only on strong evidence: an equipment ID or serial
    number alone, or model and customer together. A model or customer alone is never
    enough. Most models and several customers sit on just one machine, so "the only
    candidate" would otherwise attach that machine's history to a request about something
    else entirely, which is worse than no grounding because it looks authoritative.
    Partial evidence comes back as candidates for a person to confirm."""
    text = _normalize(request_text)

    named = [r for r in records if _is_named_by_identifier(text, r)]
    if len(named) == 1:
        return EquipmentMatch("matched", record=named[0])
    if named:
        return EquipmentMatch("needs_confirmation", candidates=_shortlist(named))

    by_model = {r["equipment_id"] for r in records if _has_model(text, r["model"])}
    by_customer = {r["equipment_id"] for r in records if _has_customer(text, r["customer"])}
    # Only when no customer is named exactly, so a typo can never outvote a correct name.
    # Model numbers never get this tolerance: CAT 320 and CAT 330 are both real.
    if not by_customer:
        by_customer = {r["equipment_id"] for r in records if _has_misspelled_customer(text, r["customer"])}

    both = [r for r in records if r["equipment_id"] in by_model & by_customer]
    if len(both) == 1:
        return EquipmentMatch("matched", record=both[0])

    partial = both or [r for r in records if r["equipment_id"] in by_model | by_customer]
    if partial:
        return EquipmentMatch("needs_confirmation", candidates=_shortlist(partial))
    return EquipmentMatch("no_match")
