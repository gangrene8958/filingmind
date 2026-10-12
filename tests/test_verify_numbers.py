"""Tests de la verificació de xifres i del flux de correcció del resum."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from verify_numbers import find_unverified_numbers, parse_number  # noqa: E402

SOURCE = """
Item 7. Management's Discussion and Analysis
Total net sales increased 2% or $7.8 billion during 2024 compared to 2023.
| Segment | 2024 | Change | 2023 |
| --- | --- | --- | --- |
| iPhone | $201,183 | — % | $200,583 |
| Services | $96,169 | 13 % | $85,200 |
| Total net sales | $391,035 | 2 % | $383,285 |
Gross margin percentage was 46.2% in 2024. The Company repurchased
$95.0 billion of its common stock and paid dividends of $15.2 billion.
Item 1A. Risk Factors
The Company's operations are subject to risks across 3 segments.
"""


def tokens(unverified):
    return [u.token for u in unverified]


# --- parse_number ----------------------------------------------------------

def test_parse_number_english_and_catalan():
    assert parse_number("391,035") == {(Decimal("391035"), 0), (Decimal("391.035"), 3)}
    assert parse_number("12.5") == {(Decimal("12.5"), 1)}
    assert parse_number("1.234,5") == {(Decimal("1234.5"), 1)}
    assert parse_number("1,234.5") == {(Decimal("1234.5"), 1)}
    assert parse_number("42") == {(Decimal("42"), 0)}


# --- Xifres correctes --------------------------------------------------------

def test_exact_figures_are_verified():
    summary = "Ingressos de $391,035 milions; Services $96,169 milions; marge brut 46.2%."
    assert find_unverified_numbers(summary, SOURCE) == []


def test_rounded_and_rescaled_figures_are_verified():
    # 391,035 milions = 391.0 / 391 mil milions; 96,169 milions ≈ 96.2 mil milions.
    summary = "Vendes de $391.0 billion (≈ $391 mil milions) i Services de $96.2 billion."
    assert find_unverified_numbers(summary, SOURCE) == []


def test_catalan_notation_is_verified():
    summary = "Ingressos de 391.035 milions de dòlars i marge brut del 46,2%."
    assert find_unverified_numbers(summary, SOURCE) == []


def test_years_counters_and_form_names_are_ignored():
    summary = (
        "1. El 10-K de 2024 destaca 3 riscos principals (Item 1A).\n"
        "2. Comparat amb 2023."
    )
    assert find_unverified_numbers(summary, SOURCE) == []


# --- Xifres inventades -------------------------------------------------------

def test_invented_amount_is_detected():
    summary = "Ingressos de $391,035 milions i un benefici net de $93,736 milions."
    result = find_unverified_numbers(summary, SOURCE)
    assert tokens(result) == ["93,736"]
    assert "benefici net" in result[0].context


def test_invented_percentage_is_detected():
    summary = "El marge brut va ser del 47.8%."
    assert tokens(find_unverified_numbers(summary, SOURCE)) == ["47.8"]


def test_wrong_rounding_is_detected():
    # 391,035 milions arrodonit a 1 decimal és 391.0, no 392.0.
    summary = "Vendes de $392.0 billion."
    assert tokens(find_unverified_numbers(summary, SOURCE)) == ["392.0"]


def test_small_amount_with_currency_is_checked():
    summary = "Recompres per $9 billion."
    assert tokens(find_unverified_numbers(summary, SOURCE)) == ["9"]


def test_duplicates_reported_once():
    summary = "Benefici de $93,736 milions. Repeteixo: $93,736 milions."
    assert tokens(find_unverified_numbers(summary, SOURCE)) == ["93,736"]


# --- Flux de summarize_filing -----------------------------------------------

FILING = {"ticker": "AAPL", "form_type": "10-K", "title": "10-K", "updated": "2024-11-01", "id": "x"}


def fake_message(text):
    return SimpleNamespace(
        stop_reason="end_turn",
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
    )


def run_summarize(responses):
    """Executa summarize_filing amb un client fals que retorna `responses` en ordre."""
    client = mock.MagicMock()
    stream = client.beta.messages.stream.return_value.__enter__.return_value
    stream.get_final_message.side_effect = [fake_message(r) for r in responses]
    result = main.summarize_filing(client, "claude-sonnet-5-5", FILING, SOURCE)
    return result, client.beta.messages.stream


def test_clean_summary_needs_one_call():
    result, stream = run_summarize(["Vendes de $391,035 milions."])
    assert result == ("Vendes de $391,035 milions.", [])
    assert stream.call_count == 1


def test_invented_number_triggers_correction():
    result, stream = run_summarize([
        "Vendes de $391,035 milions i benefici de $93,736 milions.",
        "Vendes de $391,035 milions.",
    ])
    assert result == ("Vendes de $391,035 milions.", [])
    assert stream.call_count == 2
    correction_request = stream.call_args_list[1].kwargs["messages"][-1]["content"]
    assert "93,736" in correction_request


def test_uncorrected_number_is_flagged_in_message():
    result, stream = run_summarize([
        "Benefici de $93,736 milions.",
        "Benefici de $93,736 milions.",
    ])
    assert stream.call_count == 2
    summary, unverified = result
    assert summary == "Benefici de $93,736 milions."
    assert tokens(unverified) == ["93,736"]
    warning = main.unverified_warning(unverified)
    assert "⚠️ Unverified figures" in warning and "93,736" in warning


def test_document_is_cached_for_correction():
    _, stream = run_summarize(["Vendes de $391,035 milions."])
    first_block = stream.call_args.kwargs["messages"][0]["content"][0]
    assert first_block["cache_control"] == {"type": "ephemeral"}
    assert "<document>" in first_block["text"]


# --- Extracció de seccions ---------------------------------------------------

def test_cross_reference_is_not_taken_as_section():
    risk = "Real risk factor text. " * 40
    mdna = "Revenue grew. " * 60
    text = (
        "Table of Contents\nItem 1A. Risk Factors 10\nItem 1B. Unresolved 20\n"
        f"Item 1A. Risk Factors\n{risk}\nItem 1B. Unresolved Staff Comments\nNone.\n"
        "Item 7. Management's Discussion and Analysis\n"
        f"{mdna} For more, see Item 1A. Risk Factors of this report. {mdna}\n"
        "Item 7A. Quantitative and Qualitative Disclosures\n"
    )
    section = main.extract_section(text, *main.ITEM_PATTERNS["10-K"][0][1:])
    assert section.startswith("Item 1A. Risk Factors\nReal risk factor text.")
    assert "Revenue grew" not in section
