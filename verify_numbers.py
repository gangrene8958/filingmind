"""Verificació de xifres: detecta números del resum que no surten al document.

La comprovació és determinista (sense LLM): s'extreuen totes les xifres del
resum i es busquen entre les xifres del text del filing. Es considera que una
xifra està verificada si apareix al document tal qual o arrodonida, també
canviant d'escala (p. ex. "$391.0 billion" al resum i "391,035" milions al
document).

Limitacions conegudes:
- Una xifra calculada pel model (p. ex. un creixement que el document no
  esmenta) es marcarà com a no verificada encara que el càlcul sigui correcte.
- Les xifres petites (percentatges, números d'una o dues xifres) gairebé
  sempre coincideixen amb algun número del document, de manera que la
  comprovació és sobretot útil per a imports i xifres grans.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

# Un número: dígits amb separadors opcionals ("391,035", "12.5", "1.234,56").
NUMBER_RE = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?![\w])")

SCALE_EXPONENTS = (0, 3, 6, 9)
CONTEXT_CHARS = 40

# Paraules que indiquen que una xifra petita és un import (i no un comptador).
AMOUNT_HINTS = re.compile(
    r"^\s*(%|per\s*cent|percent|punts?|bps|basis|milions?|million|mil\s+milions|"
    r"billions?|bilions?|trillions?|bn|mn|m\b|b\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UnverifiedNumber:
    """Xifra del resum que no s'ha trobat al document."""

    token: str
    context: str


def _parse_with(token: str, thousands: str, decimal: str) -> tuple[Decimal, int] | None:
    """Interpreta `token` amb els separadors indicats.

    Retorna (valor, decimals escrits) o None si el número no encaixa amb la notació.
    """
    if decimal in token:
        integer_part, _, fraction = token.rpartition(decimal)
        if thousands in fraction or decimal in integer_part:
            return None
    else:
        integer_part, fraction = token, ""

    if thousands in integer_part:
        groups = integer_part.split(thousands)
        if not (1 <= len(groups[0]) <= 3) or any(len(g) != 3 for g in groups[1:]):
            return None
        integer_part = "".join(groups)

    try:
        value = Decimal(f"{integer_part}.{fraction}" if fraction else integer_part)
    except InvalidOperation:
        return None
    return value, len(fraction)


def parse_number(token: str) -> set[tuple[Decimal, int]]:
    """Retorna les interpretacions possibles d'un número com a (valor, decimals).

    Accepta notació anglesa ("1,234.5") i catalana ("1.234,5"). Si el número és
    ambigu (p. ex. "391.035") retorna totes dues interpretacions.
    """
    if token.isdigit():
        return {(Decimal(token), 0)}
    results = {_parse_with(token, ",", "."), _parse_with(token, ".", ",")}
    results.discard(None)
    return results  # type: ignore[return-value]


def extract_source_numbers(text: str) -> set[Decimal]:
    """Totes les xifres (en valor absolut) que apareixen al document."""
    values: set[Decimal] = set()
    for match in NUMBER_RE.finditer(text):
        values |= {value for value, _ in parse_number(match.group())}
    return values


def _is_ignorable(token: str, text: str, start: int, end: int) -> bool:
    """Decideix si una xifra del resum no s'ha de verificar.

    S'ignoren anys, referències a formularis i items ("10-K", "Item 1A") i
    números petits sense unitats (comptadors com "3 riscos" o numeració de llistes).
    """
    before = text[max(0, start - 12):start]
    after = text[end:end + 20]

    if re.match(r"-[KQ]\b", after) or re.search(r"item\s*$", before, re.IGNORECASE):
        return True
    if token.isdigit():
        value = int(token)
        if len(token) == 4 and 1990 <= value <= 2100:
            return True
        if value <= 10 and not before.rstrip().endswith("$") and not AMOUNT_HINTS.match(after):
            return True
    return False


class _SourceIndex:
    """Xifres del document, amb les versions arrodonides i escalades precalculades."""

    def __init__(self, values: set[Decimal]) -> None:
        self.values = values
        self._rounded: dict[int, set[Decimal]] = {}

    def _rounded_values(self, places: int) -> set[Decimal]:
        if places not in self._rounded:
            quantum = Decimal(1).scaleb(-places)
            rounded = set()
            for value in self.values:
                for exponent in SCALE_EXPONENTS:
                    scaled = value.scaleb(-exponent)
                    for rounding in (ROUND_HALF_UP, ROUND_DOWN):
                        try:
                            rounded.add(scaled.quantize(quantum, rounding=rounding))
                        except InvalidOperation:  # Valor massa gran per a la precisió.
                            pass
            self._rounded[places] = rounded
        return self._rounded[places]

    def contains(self, value: Decimal, places: int) -> bool:
        """Cert si `value` apareix al document, tal qual o arrodonit/escalat."""
        return value in self.values or value in self._rounded_values(places)


def find_unverified_numbers(summary: str, source_text: str) -> list[UnverifiedNumber]:
    """Retorna les xifres del resum que no es poden trobar al document."""
    source = _SourceIndex(extract_source_numbers(source_text))
    unverified: list[UnverifiedNumber] = []
    seen: set[str] = set()

    for match in NUMBER_RE.finditer(summary):
        token = match.group()
        if token in seen or _is_ignorable(token, summary, match.start(), match.end()):
            continue
        seen.add(token)

        if not any(source.contains(v, places) for v, places in parse_number(token)):
            start = max(0, match.start() - CONTEXT_CHARS)
            context = summary[start:match.end() + CONTEXT_CHARS].replace("\n", " ").strip()
            unverified.append(UnverifiedNumber(token=token, context=context))

    return unverified
