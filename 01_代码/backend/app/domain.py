"""Question configuration, answer normalization, and forward-only routing.

These functions are independent of HTTP and MongoDB so the same rules govern
preview, publication, submission, and retry comparisons.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .models import Answer, MAX_SAFE_INTEGER, Questions, check_number


class DomainError(ValueError):
    def __init__(self, message: str, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


_questions_adapter = TypeAdapter(Questions)


def _invalid(message: str, question_id: str | None = None):
    raise DomainError(message, {"questionId": question_id} if question_id else None)


def _unique(values, message: str, question_id: str | None = None):
    if len(values) != len(set(values)):
        _invalid(message, question_id)


def validate_questions(questions: list[dict], publish: bool = False) -> list[dict]:
    """Return a complete normalized definition or reject invalid relationships."""
    try:
        validated = _questions_adapter.validate_python(questions)
    except ValidationError as exc:
        raise DomainError("题目配置格式不合法", exc.errors(include_url=False, include_context=False, include_input=False)) from exc
    result = [question.model_dump(exclude_none=True) for question in validated]
    if publish and not result:
        _invalid("发布问卷至少需要一道题目")
    _unique([question["id"] for question in result], "同一问卷内题目 ID 不能重复")
    positions = {question["id"]: index for index, question in enumerate(result)}

    for index, question in enumerate(result):
        qid, kind, constraints = question["id"], question["type"], question["validation"]
        option_ids = [option["id"] for option in question.get("options", [])]
        _unique(option_ids, "同一题目的选项 ID 不能重复", qid)
        _unique([option["label"] for option in question.get("options", [])], "同一题目的选项文字不能重复", qid)
        if kind == "multiple":
            low = constraints.get("minSelections", 0)
            high = constraints.get("maxSelections", len(option_ids))
            if low > high or high > len(option_ids) or low > len(option_ids):
                _invalid("多选数量上下限必须有序且不能超过选项数", qid)
            if question["required"] and high < max(1, low):
                _invalid("必答多选题至少需要允许选择一项", qid)
        elif kind == "text":
            low, high = constraints.get("minLength", 0), constraints.get("maxLength", 10000)
            if low > high or (question["required"] and high < 1):
                _invalid("文本长度上下限不合法", qid)
        elif kind == "number":
            low, high = constraints.get("min"), constraints.get("max")
            if low is not None and high is not None and low > high:
                _invalid("数字最小值不能大于最大值", qid)
            if constraints["integerOnly"]:
                integer_low = max(-MAX_SAFE_INTEGER, math.ceil(low)) if low is not None else -MAX_SAFE_INTEGER
                integer_high = min(MAX_SAFE_INTEGER, math.floor(high)) if high is not None else MAX_SAFE_INTEGER
                if integer_low > integer_high:
                    _invalid("整数题的范围内不存在可填写的安全整数", qid)

        rules = question["jumpRules"]
        _unique([rule["id"] for rule in rules], "同一题目的跳转规则 ID 不能重复", qid)
        for rule in rules:
            target, condition = rule["target"], rule["condition"]
            if target["kind"] == "question":
                position = positions.get(target["questionId"])
                if position is None or position <= index:
                    _invalid("跳转目标必须是当前问卷中位于后面的题目", qid)
            if kind == "single" and condition["value"] not in option_ids:
                _invalid("跳转条件引用了不存在的选项", qid)
            elif kind == "multiple":
                _unique(condition["values"], "多选跳转条件不能包含重复选项", qid)
                if not set(condition["values"]).issubset(option_ids):
                    _invalid("跳转条件引用了不存在的选项", qid)
                selected = set(condition["values"])
                condition["values"] = [item for item in option_ids if item in selected]
            elif kind == "number" and condition["operator"] == "between":
                if condition["min"] > condition["max"]:
                    _invalid("跳转区间下限不能大于上限", qid)
    return result


def _answer_map(questions: list[dict], answers: list[dict]) -> dict[str, Any]:
    if not isinstance(answers, list) or len(answers) > 100:
        _invalid("答案必须是最多 100 项的数组")
    known = {question["id"] for question in questions}
    indexed = {}
    for answer in answers:
        try:
            parsed = Answer.model_validate(answer)
        except ValidationError as exc:
            raise DomainError("答案格式不合法", exc.errors(include_url=False, include_context=False, include_input=False)) from exc
        if parsed.questionId not in known:
            _invalid("答案引用了不存在的题目", parsed.questionId)
        if parsed.questionId in indexed:
            _invalid("同一题目不能提交多个答案", parsed.questionId)
        indexed[parsed.questionId] = parsed.value
    return indexed


def _normalize_answer(question: dict, value: Any):
    qid, kind, constraints = question["id"], question["type"], question["validation"]
    if isinstance(value, str) and not value.strip():
        value = None
    if isinstance(value, list) and not value:
        value = None
    if value is None:
        if question["required"]:
            _invalid("必答题不能为空", qid)
        return None

    if kind == "single":
        if not isinstance(value, str) or value not in {option["id"] for option in question["options"]}:
            _invalid("单选答案必须是一个有效选项 ID", qid)
    elif kind == "multiple":
        option_ids = [option["id"] for option in question["options"]]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            _invalid("多选答案必须是选项 ID 数组", qid)
        _unique(value, "多选答案不能包含重复选项", qid)
        if not set(value).issubset(option_ids):
            _invalid("多选答案引用了不存在的选项", qid)
        low = max(1 if question["required"] else 0, constraints.get("minSelections", 0))
        high = constraints.get("maxSelections", len(option_ids))
        if not low <= len(value) <= high:
            _invalid(f"选择数量应在 {low} 至 {high} 项之间", qid)
        selected = set(value)
        value = [option for option in option_ids if option in selected]
    elif kind == "text":
        if not isinstance(value, str):
            _invalid("文本答案必须是字符串", qid)
        value = value.strip()
        low = max(1 if question["required"] else 0, constraints.get("minLength", 0))
        high = constraints.get("maxLength", 10000)
        if not low <= len(value) <= high:
            _invalid(f"文本长度应在 {low} 至 {high} 字符之间", qid)
    elif kind == "number":
        try:
            value = check_number(value)
        except (ValueError, TypeError, OverflowError) as exc:
            raise DomainError("数字答案必须是有限数字，整数须在安全范围内", {"questionId": qid}) from exc
        if constraints["integerOnly"]:
            if (isinstance(value, float) and not value.is_integer()) or abs(value) > MAX_SAFE_INTEGER:
                _invalid("此题必须填写安全范围内的整数", qid)
            value = int(value)
        if "min" in constraints and value < constraints["min"]:
            _invalid("数字答案小于允许的最小值", qid)
        if "max" in constraints and value > constraints["max"]:
            _invalid("数字答案大于允许的最大值", qid)
    return value


def _matches(condition: dict, value: Any) -> bool:
    if value is None:
        return False
    operator = condition["operator"]
    if operator == "eq":
        return value == condition["value"]
    if operator in ("containsAny", "containsAll", "setEquals"):
        wanted, selected = set(condition["values"]), set(value)
        if operator == "containsAny":
            return bool(wanted & selected)
        if operator == "containsAll":
            return wanted <= selected
        return wanted == selected
    if operator == "between":
        return condition["min"] <= value <= condition["max"]
    if operator == "gt":
        return value > condition["value"]
    if operator == "gte":
        return value >= condition["value"]
    if operator == "lt":
        return value < condition["value"]
    if operator == "lte":
        return value <= condition["value"]
    raise DomainError("不支持的跳转操作符")


def _next_index(questions: list[dict], positions: dict[str, int], index: int, value: Any) -> int:
    for rule in questions[index]["jumpRules"]:
        if _matches(rule["condition"], value):
            target = rule["target"]
            return len(questions) if target["kind"] == "end" else positions[target["questionId"]]
    return index + 1


def validate_submission(questions: list[dict], answers: list[dict]) -> list[dict]:
    """Validate the complete server-derived path, rejecting skipped injection."""
    questions = validate_questions(questions, publish=True)
    supplied = _answer_map(questions, answers)
    positions = {question["id"]: index for index, question in enumerate(questions)}
    canonical = []
    index = 0
    while index < len(questions):
        question = questions[index]
        value = _normalize_answer(question, supplied.get(question["id"]))
        canonical.append({"questionId": question["id"], "value": value})
        index = _next_index(questions, positions, index, value)
    used = {answer["questionId"] for answer in canonical}
    outside = [qid for qid in supplied if qid not in used]
    if outside:
        raise DomainError("不能提交实际填写路径以外的答案", {"questionIds": outside})
    return canonical


def preview_answers(questions: list[dict], answers: list[dict], changed_question_id: str | None = None) -> dict:
    """Validate a prefix and optionally discard all answers after an edited one.

    Preview keeps no server-side draft. The client sends the current answer set;
    explicitly sending null advances past an empty optional question.
    """
    questions = validate_questions(questions, publish=True)
    supplied = _answer_map(questions, answers)
    positions = {question["id"]: index for index, question in enumerate(questions)}
    cleared = []
    if changed_question_id is not None:
        if changed_question_id not in positions or changed_question_id not in supplied:
            _invalid("修改题目必须存在且包含当前答案", changed_question_id)
        changed_position = positions[changed_question_id]
        cleared = [question["id"] for question in questions if question["id"] in supplied and positions[question["id"]] > changed_position]
        for qid in cleared:
            del supplied[qid]
    canonical = []
    index = 0
    while index < len(questions):
        question = questions[index]
        if question["id"] not in supplied:
            break
        value = _normalize_answer(question, supplied[question["id"]])
        canonical.append({"questionId": question["id"], "value": value})
        index = _next_index(questions, positions, index, value)
    used = {answer["questionId"] for answer in canonical}
    outside = [qid for qid in supplied if qid not in used]
    if outside:
        raise DomainError("答案不构成连续的实际填写路径", {"questionIds": outside})
    return {
        "answers": canonical,
        "nextQuestionId": questions[index]["id"] if index < len(questions) else None,
        "complete": index >= len(questions),
        "clearedQuestionIds": cleared,
    }
