from __future__ import annotations

from datetime import date

from collections_agent.voice.prompt_template import allowed_facts, render_system_prompt
from collections_agent.voice.speakable import amount_to_words, date_to_words, invoice_number_to_words


def test_renders_company_and_contact_names(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Acme Supplies" in prompt
    assert sample_context_pack.contact_name in prompt
    assert sample_context_pack.customer_name in prompt


def test_renders_invoice_facts(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    inv = sample_context_pack.invoices[0]
    assert inv.invoice_number in prompt
    assert date_to_words(inv.due_date) in prompt


class TestStructuralSpokenFormFix:
    """2026-09-29: giving the model both a raw form and a `(say "...")` spoken form side by
    side let it default back to reading the raw one — confirmed on real calls in both Hindi
    and English, on both gpt-4o-mini and gpt-4o. Structural fix: YOUR ONLY FACTS contains only
    spoken forms; the handful of raw values a tool call actually needs live in a separately
    labeled REFERENCE VALUES section. See docs/FAILURES.md."""

    def test_facts_section_has_no_raw_due_date_anywhere(self, sample_context_pack):
        """No tool call ever takes an invoice's due date as an argument, so it has no raw form
        left in the prompt at all — only the spoken one."""
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        inv = sample_context_pack.invoices[0]
        assert inv.due_date.isoformat() not in prompt

    def test_spoken_forms_appear_before_the_reference_marker(self, sample_context_pack):
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        inv = sample_context_pack.invoices[0]
        facts_section, _, reference_section = prompt.partition("# REFERENCE VALUES")
        assert reference_section  # the marker exists and split the prompt
        assert invoice_number_to_words(inv.invoice_number) in facts_section
        assert amount_to_words(inv.outstanding()) in facts_section

    def test_raw_invoice_number_and_amount_appear_only_in_reference_section(self, sample_context_pack):
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        inv = sample_context_pack.invoices[0]
        facts_section, _, reference_section = prompt.partition("# REFERENCE VALUES")
        assert inv.invoice_number not in facts_section
        assert inv.invoice_number in reference_section
        assert f"{inv.outstanding():,.2f}" not in facts_section
        assert f"{inv.outstanding():,.2f}" in reference_section

    def test_reference_section_says_never_speak(self, sample_context_pack):
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        assert "NEVER SPEAK THESE" in prompt


def test_total_outstanding_includes_spoken_form(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert amount_to_words(sample_context_pack.total_outstanding) in prompt


def test_date_speaking_rule_gives_the_pattern_to_replicate(sample_context_pack):
    """Regression guard for the exact 2026-09-26/29 finding: gpt-4o said "2026 September 13"
    for 2026-09-30 because nothing told it how to speak a date it resolves itself mid-call
    (the promised date isn't known in advance, so it can't get a pre-computed spoken form)."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "ordinal day, then month name, then" in prompt


def test_bundled_question_phrasing_is_gone(sample_context_pack):
    """Regression guard for the 2026-09-17 bundled-questions bug: these two phrases bundled
    multiple facts into one implied question and contradicted the one-question-per-turn rule."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "pin down amount + date + method" not in prompt
    assert "Ask once for the specific date and amount" not in prompt


def test_will_pay_branch_asks_sequentially(sample_context_pack):
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "One question at a time, never" in prompt
    assert "combined" in prompt


def test_will_pay_branch_asks_each_required_promise_field_explicitly(sample_context_pack):
    """Regression guard for the 2026-09-17 finding: the amount ask was silently missing from
    the Will-pay branch and it took a live call to notice, because nothing tested prompt
    content for "does it actually ask for X." One assertion per required field, so a future
    refactor can't drop one without a test failing."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Ask when they'll pay (a date)" in prompt  # date
    assert "whether they'll pay the full outstanding" in prompt  # amount
    assert "follow-up for the specific amount" in prompt  # partial amount
    assert "Then ask the method." in prompt  # method
    assert "never assume the full amount without asking" in prompt


def test_payment_delay_asks_once_without_requiring_a_date(sample_context_pack):
    prompt = " ".join(render_system_prompt(sample_context_pack, "Acme Supplies").split())
    assert "ask once when they expect payment" in prompt
    assert "a date is helpful, not required" in prompt
    assert "Do not press for a date if they cannot give one" in prompt
    assert "no dispute tool is needed" in prompt


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


def test_hinglish_labeled_account_falls_back_to_english_instruction(base_account, make_invoice, as_of):
    """Hinglish was dropped 2026-09-29 (docs/FAILURES.md) — a Sheet row still marked "hinglish"
    from before that change gets the English instruction, not a Hinglish-shaped one that no
    longer exists in the code."""
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

    assert "Respond in English" in prompt


def test_never_reask_rule_and_cannot_pay_now_branch_reference_it(sample_context_pack):
    """Regression guard for the 2026-09-17 finding: the customer volunteered a reason
    ("payment is under approval") before the agent ever asked for one, and the agent asked
    for the reason anyway. The branch must not ask again for something already given."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert "Never ask again" in prompt
    assert "If they have not given a reason, ask why once" in prompt


class TestPrimaryInvoiceOnly:
    """Regression coverage for the 2026-09-17 redesign: a call that tried to resolve every
    invoice at once ran out of time mid-negotiation on the second and stumbled
    (docs/FAILURES.md). A call should now work exactly one invoice to a complete outcome and
    schedule a callback for the rest."""

    def _two_invoice_pack(self, base_account, make_invoice, as_of):
        from collections_agent.precall.context_pack import build_context_pack

        primary = make_invoice(base_account.account_id, days_overdue=70, amount=500_000)
        other = make_invoice(base_account.account_id, days_overdue=10, amount=50_000)
        pack = build_context_pack(base_account, [primary, other], [], [], [], as_of=as_of)
        return pack, primary, other

    def test_single_invoice_account_has_no_other_invoices_section(self, sample_context_pack):
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        assert "Other overdue invoices" not in prompt

    def test_multi_invoice_account_separates_primary_from_others(
        self, base_account, make_invoice, as_of
    ):
        pack, primary, other = self._two_invoice_pack(base_account, make_invoice, as_of)
        prompt = render_system_prompt(pack, "Acme Supplies")

        assert pack.primary_invoice_id == primary.invoice_id
        assert "Other overdue invoices" in prompt
        # Both invoice numbers appear somewhere (allowed_facts covers both), but only the
        # primary section should tell the agent to work it to a complete outcome.
        assert primary.invoice_number in prompt
        assert other.invoice_number in prompt

    def test_multi_invoice_prompt_forbids_negotiating_other_invoices(
        self, base_account, make_invoice, as_of
    ):
        pack, _primary, _other = self._two_invoice_pack(base_account, make_invoice, as_of)
        prompt = render_system_prompt(pack, "Acme Supplies")
        assert "Do not raise or negotiate these" in prompt

    def test_multi_invoice_prompt_instructs_callback_before_ending(
        self, base_account, make_invoice, as_of
    ):
        pack, _primary, _other = self._two_invoice_pack(base_account, make_invoice, as_of)
        prompt = " ".join(render_system_prompt(pack, "Acme Supplies").split())
        assert "Call schedule_callback once with a reason naming the other invoice(s)" in prompt
        assert "no callback date is required" in prompt
        assert "Only if it succeeds, tell them someone will follow up" in prompt

    def test_single_invoice_prompt_has_no_callback_closing_instruction(self, sample_context_pack):
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        assert "Call schedule_callback once with a reason naming the other invoice(s)" not in prompt

    def test_goal_section_scopes_to_primary_invoice_only(self, sample_context_pack):
        prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
        assert "FOR THE PRIMARY INVOICE ONLY" in prompt
        assert "This call is about the primary invoice only" in prompt


def test_state_invoice_number_once_then_say_this_invoice(sample_context_pack):
    """2026-09-26 control-call finding: the agent repeated the full invoice number every turn
    (with inconsistent fragmentation each time) instead of saying "this invoice" after the
    first mention, which no real collector does."""
    prompt = render_system_prompt(sample_context_pack, "Acme Supplies")
    assert 'say "this invoice"' in prompt
    assert "never repeat the full number again" in prompt
