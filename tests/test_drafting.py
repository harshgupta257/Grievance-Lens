"""Drafting: no invented facts, explicit placeholders, language, factuality screen and verified statistics."""

from src.drafting.drafter import DraftRequest, build_draft, screen
from src.drafting.factcheck import check, factual_tokens
from src.performance.stats import department_performance

COMPLAINT = (
    "My PF final settlement claim filed on 12 March 2026 is still under process. Claim ID PF-2026-0457. "
    "The regional office helpline does not answer."
)


def test_draft_keeps_the_complaint_verbatim_and_extracts_details_from_it():
    d = build_draft(
        DraftRequest(complaint=COMPLAINT, department_name="Ministry of Labour and Employment"),
        with_statistics=False,
    )
    assert d.sections[0].body == COMPLAINT and d.sections[0].from_user
    refs = next(s for s in d.sections if s.title.startswith("3."))
    assert "Claim ID PF-2026-0457" in refs.body and not refs.placeholder
    timeline = next(s for s in d.sections if s.title.startswith("2."))
    assert "12 March 2026" in timeline.body and "PF-2026-0457" not in timeline.body
    assert d.factcheck.passed


def test_missing_details_become_placeholders_never_invented_values():
    d = build_draft(
        DraftRequest(complaint="My pension has not come for a long time, please help."), with_statistics=False
    )
    assert "[ADD APPLICATION NUMBER / REFERENCE NUMBER, if any]" in d.text
    assert "[STATE CLEARLY WHAT YOU WANT DONE]" in d.text and "[CHOOSE THE DEPARTMENT" in d.text
    assert sum(s.placeholder for s in d.sections) >= 4 and d.factcheck.passed


def test_clarifications_are_used_as_given():
    d = build_draft(
        DraftRequest(
            complaint=COMPLAINT,
            clarifications={
                "action_requested": "Settle my claim and credit the amount.",
                "unknown_field": "ignored 999",
            },
        ),
        with_statistics=False,
    )
    assert "Settle my claim and credit the amount." in d.text and "999" not in d.text


def test_hindi_draft_uses_hindi_template_and_keeps_text():
    text = "राशन कार्ड से मेरे बच्चों के नाम बिना बताए हटा दिए गए।"
    d = build_draft(DraftRequest(complaint=text, language="hi"), with_statistics=False)
    assert (
        d.sections[0].title.startswith("1. शिकायत")
        and text in d.text
        and "सीपीग्राम्स" in "\n".join(d.review_checklist)
    )


def test_factual_tokens_normalise_digits_and_separators():
    assert factual_tokens("रु १०,००० on 5 June") == {"10000", "5", "june"}
    assert "18000" in factual_tokens("Rs 18,000")


def test_screen_flags_invented_numbers_dates_and_allegations():
    req = DraftRequest(complaint=COMPLAINT)
    d = build_draft(req, with_statistics=False)
    bad = d.text + "\nPlease pay Rs 45,000 by 30 June or I will take legal action against the corrupt staff."
    fc = screen(bad, req)
    assert set(fc.unsupported_tokens) == {"45000", "30", "june"}
    assert set(fc.unsupported_claim_words) == {"legal action", "corrupt"}
    assert not fc.passed


def test_claim_words_written_by_the_user_are_not_flagged():
    fc = check("The officer asked for a bribe.", ["The officer asked for a bribe at the counter."], [])
    assert fc.passed


def test_statistics_are_verified_sourced_and_separate():
    p = department_performance("department_of_posts")
    assert p.available and p.latest_reporting_month == "2026-09"
    assert p.backlog_trend and p.backlog_trend["period"] == "2024-06 to 2026-01"
    assert all(m.source and m.period for m in p.metrics) and p.sources
    d = build_draft(
        DraftRequest(
            complaint=COMPLAINT, department_key="department_of_posts", department_name="Department of Posts"
        )
    )
    assert d.department_statistics is not None
    assert str(p.backlog_trend["pending_end"]) not in d.text  # statistics never enter the draft text


def test_unknown_department_has_no_fabricated_statistics():
    p = department_performance("no_such_department")
    assert not p.available and not p.metrics and p.unavailable
