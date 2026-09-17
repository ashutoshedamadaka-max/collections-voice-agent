from __future__ import annotations

from datetime import date

from collections_agent.voice.prompt_template import allowed_facts, render_system_prompt
from collections_agent.voice.speakable import amount_to_words, invoice_number_to_words


def test_renders_company_and_contact_names(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Acme Supplies" in prompt
    assert sample_context_pack.contact_name in prompt
    assert sample_context_pack.customer_name in prompt


def test_renders_invoice_facts(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    inv = sample_context_pack.invoices[0]
    assert inv.invoice_number in prompt
    assert inv.due_date.isoformat() in prompt


def test_invoice_facts_include_spoken_form_alongside_the_raw_value(sample_context_pack):
    """The model must keep the raw invoice number/amount (for tool-call arguments) but speak
    the pre-computed word form instead of transforming digits itself — see docs/FAILURES.md."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    inv = sample_context_pack.invoices[0]
    assert invoice_number_to_words(inv.invoice_number) in prompt
    assert amount_to_words(inv.outstanding()) in prompt


def test_total_outstanding_includes_spoken_form(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert amount_to_words(sample_context_pack.total_outstanding) in prompt


def test_bundled_question_phrasing_is_gone(sample_context_pack):
    """Regression guard for the 2026-09-17 bundled-questions bug: these two phrases bundled
    multiple facts into one implied question and contradicted the one-question-per-turn rule."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "pin down amount + date + method" not in prompt
    assert "Ask once for the specific date and amount" not in prompt


def test_will_pay_branch_asks_sequentially(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "three separate questions" in prompt
    assert "combine them" in prompt


def test_discount_refusal_branch_gives_a_natural_example(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Asked for a discount or waiver" in prompt
    assert "not something I can adjust" in prompt


def test_does_not_leak_foreign_account_facts(base_account, make_invoice, as_of):
    from collections_agent.precall.context_pack import build_context_pack

    inv = make_invoice(base_account.account_id, days_overdue=10)
    foreign_inv = make_invoice("ACC-OTHER", days_overdue=10, invoice_id="INV-FOREIGN")
    pack = build_context_pack(base_account, [inv, foreign_inv], [], [], [], as_of=as_of)

    prompt = render_system_prompt(pack, "Acme Supplies")

    assert "INV-FOREIGN" not in prompt
    assert foreign_inv.invoice_number not in prompt


def test_prompt_contains_hard_prohibitions(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Never offer, hint at, or negotiate a discount" in prompt
    assert "Never mention legal action, credit holds, agencies, or consequences" in prompt


def test_allowed_facts_matches_prompt_content(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    for fact in allowed_facts(sample_context_pack):
        assert fact in prompt


def test_empty_prior_promises_renders_none(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Prior promises: none" in prompt


def test_empty_open_disputes_renders_none(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Open disputes: none" in prompt


def test_current_date_is_injected(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies", current_date=date(2026, 9, 16))
    assert "Today's date is 2026-09-16" in prompt


def test_prompt_instructs_bare_day_resolves_forward_never_past(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies", current_date=date(2026, 9, 16))
    assert "never a date in the past" in prompt


def test_current_date_defaults_to_today_when_not_given(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert f"Today's date is {date.today().isoformat()}" in prompt


def test_english_account_gets_english_instruction(sample_context_pack):
    """sample_context_pack's account has preferred_language="en"."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Respond in English" in prompt


def test_hindi_account_gets_hindi_instruction(base_account, make_invoice, as_of):
    from collections_agent.precall.context_pack import build_context_pack

    hindi_account = base_account.model_copy(update={"preferred_language": "hi"})
    pack = build_context_pack(
        hindi_account, [make_invoice(hindi_account.account_id, days_overdue=10)], [], [], [], as_of=as_of
    )

    prompt = render_system_prompt(pack, "Acme Supplies")

    assert "Respond in Hindi" in prompt


def test_hinglish_account_gets_code_switching_instruction(base_account, make_invoice, as_of):
    from collections_agent.precall.context_pack import build_context_pack

    hinglish_account = base_account.model_copy(update={"preferred_language": "hinglish"})
    pack = build_context_pack(
        hinglish_account,
        [make_invoice(hinglish_account.account_id, days_overdue=10)],
        [],
        [],
        [],
        as_of=as_of,
    )

    prompt = render_system_prompt(pack, "Acme Supplies")

    assert "code-switch" in prompt.lower()
