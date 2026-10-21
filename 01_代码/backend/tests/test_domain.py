from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.domain import DomainError, preview_answers, validate_questions, validate_submission
from app.models import Answer, RegisterRequest, SubmitRequest, SurveyCreate, SurveyPatch


def text(qid="q1", required=True, **validation):
    return {"id": qid, "type": "text", "title": " 说明 ", "required": required, "validation": validation}


def single(qid="q1", required=True, rules=None):
    return {
        "id": qid, "type": "single", "title": "选择", "required": required,
        "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "jumpRules": rules or [],
    }


def multiple(qid="q1", required=True, rules=None, **validation):
    question = single(qid, required)
    question.update(type="multiple", validation=validation, jumpRules=rules or [])
    question["options"].append({"id": "c", "label": "C"})
    return question


def number(qid="q1", required=True, rules=None, **validation):
    return {
        "id": qid, "type": "number", "title": "数值", "required": required,
        "validation": validation, "jumpRules": rules or [],
    }


def rule(condition, target="end", rid="r1"):
    return {
        "id": rid, "condition": condition,
        "target": {"kind": "end"} if target == "end" else {"kind": "question", "questionId": target},
    }


def answer(qid="q1", value="内容"):
    return {"questionId": qid, "value": value}


def test_question_defaults_and_trim_are_complete_for_database():
    result = validate_questions([text(), number("q2"), single("q3")])
    assert result[0] == {
        "id": "q1", "type": "text", "title": "说明", "description": "", "required": True,
        "validation": {}, "jumpRules": [],
    }
    assert result[1]["validation"] == {"integerOnly": False}
    assert result[2]["validation"] == {}


def test_draft_may_be_empty_but_publication_requires_question():
    assert validate_questions([]) == []
    with pytest.raises(DomainError):
        validate_questions([], publish=True)


@pytest.mark.parametrize("questions", [
    [text(), text()],
    [dict(text(), title="  ")],
    [dict(text(), unknown=1)],
    [dict(text(), options=[])],
    [dict(text(), jumpRules=[rule({"operator": "eq", "value": "a"})])],
    [dict(single(), options=[{"id": "a", "label": "A"}])],
    [dict(single(), options=[{"id": "a", "label": "A"}, {"id": "a", "label": "B"}])],
    [dict(single(), options=[{"id": "a", "label": "A"}, {"id": "b", "label": " A "}])],
    [multiple(minSelections=3, maxSelections=2)],
    [multiple(maxSelections=4)],
    [multiple(maxSelections=0)],
    [text(minLength=5, maxLength=4)],
    [text(maxLength=0)],
    [number(min=5, max=4)],
    [number(integerOnly=True, min=1.2, max=1.9)],
    [number(integerOnly=True, min=1e20)],
    [number(min=float("inf"))],
    [number(max=float("nan"))],
    [number(min=True)],
    [multiple(minSelections=True)],
    [multiple(minSelections=None)],
    [multiple(maxSelections=None)],
    [text(minLength=None)],
    [text(maxLength=None)],
    [number(min=None)],
    [number(max=None)],
    [single(rules=[rule({"operator": "eq", "value": "missing"})])],
    [single(rules=[rule({"operator": "eq", "value": "a"}, "missing")])],
    [single(rules=[rule({"operator": "eq", "value": "a"}, "q1")])],
    [text(), single("q2", rules=[rule({"operator": "eq", "value": "a"}, "q1")])],
    [single(rules=[rule({"operator": "eq", "value": "a"}), rule({"operator": "eq", "value": "b"})])],
    [multiple(rules=[rule({"operator": "containsAny", "values": ["a", "a"]})])],
    [multiple(rules=[rule({"operator": "containsAll", "values": ["missing"]})])],
    [number(rules=[rule({"operator": "between", "min": 3, "max": 2})])],
])
def test_invalid_configuration_rejected(questions):
    with pytest.raises(DomainError):
        validate_questions(questions)


def test_configuration_capacity_and_input_not_mutated():
    questions = [text(f"q{i}") for i in range(100)]
    original = deepcopy(questions)
    assert len(validate_questions(questions)) == 100
    assert questions == original
    with pytest.raises(DomainError):
        validate_questions(questions + [text("q100")])


def test_skipped_required_question_does_not_block_submission():
    questions = [single(rules=[rule({"operator": "eq", "value": "b"}, "q3")]), text("q2"), number("q3", min=0)]
    result = validate_submission(questions, [answer("q3", 2.5), answer(value="b")])
    assert result == [answer(value="b"), answer("q3", 2.5)]


def test_injected_skipped_answer_is_rejected():
    questions = [single(rules=[rule({"operator": "eq", "value": "b"})]), text("q2")]
    with pytest.raises(DomainError, match="路径"):
        validate_submission(questions, [answer(value="b"), answer("q2")])


def test_first_matching_rule_wins():
    questions = [multiple(rules=[
        rule({"operator": "containsAny", "values": ["a"]}, "q3", "r1"),
        rule({"operator": "containsAll", "values": ["a", "b"]}, "q2", "r2"),
    ]), text("q2"), text("q3")]
    assert validate_submission(questions, [answer(value=["b", "a"]), answer("q3")]) == [answer(value=["a", "b"]), answer("q3")]


@pytest.mark.parametrize("operator,selected,hit", [
    ("containsAny", ["b"], True), ("containsAny", ["c"], False),
    ("containsAll", ["a", "b", "c"], True), ("containsAll", ["a"], False),
    ("setEquals", ["b", "a"], True), ("setEquals", ["a", "b", "c"], False),
])
def test_multiple_rule_semantics(operator, selected, hit):
    questions = [multiple(rules=[rule({"operator": operator, "values": ["b", "a"]})]), text("q2", False)]
    result = validate_submission(questions, [answer(value=selected)])
    assert len(result) == (1 if hit else 2)


@pytest.mark.parametrize("condition,value,hit", [
    ({"operator": "eq", "value": 2}, 2, True),
    ({"operator": "gt", "value": 2}, 2, False),
    ({"operator": "gte", "value": 2}, 2, True),
    ({"operator": "lt", "value": 2}, 1, True),
    ({"operator": "lte", "value": 2}, 2, True),
    ({"operator": "between", "min": 2, "max": 5}, 2, True),
    ({"operator": "between", "min": 2, "max": 5}, 5, True),
    ({"operator": "between", "min": 2, "max": 5}, 6, False),
])
def test_number_rule_semantics(condition, value, hit):
    questions = [number(rules=[rule(condition)]), text("q2", False)]
    assert len(validate_submission(questions, [answer(value=value)])) == (1 if hit else 2)


def test_optional_blanks_normalize_to_null_and_skip_value_limits():
    questions = [text(required=False, minLength=5), multiple("q2", False, minSelections=2), number("q3", False, min=100)]
    assert validate_submission(questions, [answer(value="  "), answer("q2", [])]) == [answer(value=None), answer("q2", None), answer("q3", None)]


def test_null_does_not_match_number_rule():
    questions = [number(required=False, rules=[rule({"operator": "eq", "value": 0})]), text("q2", False)]
    assert len(validate_submission(questions, [])) == 2


@pytest.mark.parametrize("question,value", [
    (single(), None), (single(), ["a"]), (single(), "missing"),
    (multiple(), ["a", "a"]), (multiple(), ["missing"]), (multiple(), "a"),
    (multiple(minSelections=2), ["a"]), (multiple(maxSelections=1), ["a", "b"]),
    (text(minLength=3), "ab"), (text(maxLength=2), "abc"), (text(), 12),
    (number(), "12"), (number(), True), (number(), float("nan")),
    (number(), float("inf")), (number(), 2**60), (number(), 10**1000),
    (number(integerOnly=True), 1.5), (number(integerOnly=True), 1e20),
    (number(min=0), -1), (number(max=10), 11),
])
def test_invalid_answer_rejected(question, value):
    with pytest.raises(DomainError):
        validate_submission([question], [answer(value=value)])


def test_unicode_length_and_integer_boundaries():
    assert validate_submission([text(minLength=2, maxLength=2)], [answer(value="  中😀  ")]) == [answer(value="中😀")]
    assert validate_submission([number(integerOnly=True)], [answer(value=float(2**53 - 1))]) == [answer(value=2**53 - 1)]
    assert validate_submission([number(min=-2, max=2)], [answer(value=-2)]) == [answer(value=-2)]


def test_malformed_unicode_is_rejected_before_bson_encoding():
    with pytest.raises(DomainError):
        validate_submission([text()], [answer(value="\ud800")])
    with pytest.raises(ValidationError):
        SurveyCreate.model_validate({"title": "\udfff", "settings": {"anonymous": True}})


@pytest.mark.parametrize("answers", [
    [answer(), answer()], [answer("missing")], [{"questionId": "q1"}],
    [{"questionId": "q1", "value": "x", "injected": True}],
])
def test_bad_answer_envelope_rejected(answers):
    with pytest.raises(DomainError):
        validate_submission([text()], answers)


def test_preview_requires_explicit_optional_skip_and_returns_next_question():
    questions = [text(required=False), text("q2")]
    assert preview_answers(questions, []) == {
        "answers": [], "nextQuestionId": "q1", "complete": False, "clearedQuestionIds": [],
    }
    assert preview_answers(questions, [answer(value=None)])["nextQuestionId"] == "q2"


def test_editing_answer_clears_all_later_answers_then_recomputes():
    questions = [single(rules=[rule({"operator": "eq", "value": "b"}, "q3")]), text("q2"), text("q3")]
    result = preview_answers(questions, [answer(value="b"), answer("q2"), answer("q3")], "q1")
    assert result == {
        "answers": [answer(value="b")], "nextQuestionId": "q3", "complete": False,
        "clearedQuestionIds": ["q2", "q3"],
    }


def test_preview_without_edit_keeps_existing_answers():
    questions = [text(), text("q2")]
    assert preview_answers(questions, [answer(), answer("q2")]) == {
        "answers": [answer(), answer("q2")], "nextQuestionId": None, "complete": True, "clearedQuestionIds": [],
    }


@pytest.mark.parametrize("answers,changed", [
    ([answer("q2")], None),
    ([answer()], "missing"),
    ([answer()], "q2"),
])
def test_preview_rejects_gaps_and_invalid_change_marker(answers, changed):
    with pytest.raises(DomainError):
        preview_answers([text(), text("q2")], answers, changed)


def test_api_contract_rejects_extra_fields_bool_numbers_and_naive_dates():
    for value in [True, {"x": 1}, [1], float("inf")]:
        with pytest.raises(ValidationError):
            Answer.model_validate(answer(value=value))
    with pytest.raises(ValidationError):
        SurveyCreate.model_validate({"title": "调查", "settings": {"anonymous": True}, "ownerId": "x"})
    with pytest.raises(ValidationError):
        SurveyCreate.model_validate({"title": "调查", "settings": {"anonymous": True}, "deadlineAt": "2026-10-18T12:00:00"})
    with pytest.raises(ValidationError):
        SurveyPatch.model_validate({"revision": True})
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({"submissionKey": "x" * 64, "submissionToken": "token", "answers": []})


def test_patch_omission_and_explicit_null_are_distinct():
    assert SurveyPatch.model_validate({"revision": 1}).model_fields_set == {"revision"}
    assert SurveyPatch.model_validate({"revision": 1, "deadlineAt": None}).model_fields_set == {"revision", "deadlineAt"}
    with pytest.raises(ValidationError):
        SurveyPatch.model_validate({"revision": 1, "questions": None})


def test_registration_trims_username_but_never_password():
    request = RegisterRequest.model_validate({"username": " Alice ", "password": " password "})
    assert request.username == "Alice"
    assert request.password == " password "


@pytest.mark.parametrize("model", [SurveyCreate, SurveyPatch])
def test_deadline_iso_timezone_normalized_to_mongo_milliseconds(model):
    data = {"title": "调查", "settings": {"anonymous": True}} if model is SurveyCreate else {"revision": 1}
    value = model.model_validate({**data, "deadlineAt": "2026-10-18T12:30:00.123987+08:00"})
    assert value.deadlineAt == datetime(2026, 10, 18, 4, 30, 0, 123000, tzinfo=timezone.utc)
    assert value.deadlineAt.tzinfo is timezone.utc


@pytest.mark.parametrize("model", [SurveyCreate, SurveyPatch])
def test_deadline_accepts_aware_datetime_and_preserves_explicit_null(model):
    data = {"title": "调查", "settings": {"anonymous": True}} if model is SurveyCreate else {"revision": 1}
    original = datetime(2026, 10, 18, 12, 30, 0, 999999, tzinfo=timezone(timedelta(hours=8)))
    value = model.model_validate({**data, "deadlineAt": original})
    assert value.deadlineAt == datetime(2026, 10, 18, 4, 30, 0, 999000, tzinfo=timezone.utc)
    empty = model.model_validate({**data, "deadlineAt": None})
    assert empty.deadlineAt is None
    assert "deadlineAt" in empty.model_fields_set


@pytest.mark.parametrize("model", [SurveyCreate, SurveyPatch])
def test_deadline_rejects_timestamp_coercion_and_naive_datetime(model):
    data = {"title": "调查", "settings": {"anonymous": True}} if model is SurveyCreate else {"revision": 1}
    for invalid in (0, 1792305600.25, True, False, "1792305600", datetime(2026, 10, 18), "2026-10-18T12:00:00"):
        with pytest.raises(ValidationError):
            model.model_validate({**data, "deadlineAt": invalid})
