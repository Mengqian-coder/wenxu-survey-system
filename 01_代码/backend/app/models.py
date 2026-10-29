"""Strict API contracts matching the MongoDB questionnaire document schema."""

from __future__ import annotations

import math
import unicodedata
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


MAX_SAFE_INTEGER = 2**53 - 1
StableId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
Numeric = StrictInt | StrictFloat
Revision = Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]


def _deadline_input(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and any(separator in value for separator in ("T", "t", " ")):
        return value
    raise ValueError("截止时间必须是带时区的 ISO 8601 日期时间或 null")


def _deadline_utc_milliseconds(value: datetime | None):
    if value is None:
        return None
    try:
        value = value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("截止时间超出 UTC 日期范围") from exc
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


DeadlineAt = Annotated[
    AwareDatetime | None,
    BeforeValidator(_deadline_input),
    AfterValidator(_deadline_utc_milliseconds),
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def check_number(value: int | float) -> int | float:
    """Do not silently round JSON integers before MongoDB/JavaScript see them."""
    if type(value) not in (int, float):
        raise ValueError("必须是有限数字，不能使用布尔值")
    if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("整数不得超出安全整数范围")
    if not math.isfinite(value):
        raise ValueError("必须是有限数字，不能使用布尔值")
    return value


class RegisterRequest(ApiModel):
    username: Annotated[StrictStr, Field(min_length=3, max_length=32)]
    password: Annotated[StrictStr, Field(min_length=8, max_length=128)]

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value):
        return value.strip() if isinstance(value, str) else value


class LoginRequest(ApiModel):
    username: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    password: Annotated[StrictStr, Field(min_length=1, max_length=128)]


class SurveySettings(ApiModel):
    anonymous: StrictBool
    allowRepeat: StrictBool = False


class Option(ApiModel):
    id: StableId
    label: Annotated[StrictStr, Field(min_length=1, max_length=500)]

    @field_validator("label", mode="before")
    @classmethod
    def trim_label(cls, value):
        return value.strip() if isinstance(value, str) else value


class QuestionTarget(ApiModel):
    kind: Literal["question"]
    questionId: StableId


class EndTarget(ApiModel):
    kind: Literal["end"]


JumpTarget = Annotated[QuestionTarget | EndTarget, Field(discriminator="kind")]


class SingleCondition(ApiModel):
    operator: Literal["eq"]
    value: StableId


class MultipleCondition(ApiModel):
    operator: Literal["containsAny", "containsAll", "setEquals"]
    values: Annotated[list[StableId], Field(min_length=1, max_length=50, strict=True)]


class NumberCompareCondition(ApiModel):
    operator: Literal["gt", "gte", "lt", "lte", "eq"]
    value: Numeric

    _finite = field_validator("value")(check_number)


class NumberBetweenCondition(ApiModel):
    operator: Literal["between"]
    min: Numeric
    max: Numeric

    _finite = field_validator("min", "max")(check_number)


NumberCondition = Annotated[
    NumberCompareCondition | NumberBetweenCondition,
    Field(discriminator="operator"),
]


class SingleRule(ApiModel):
    id: StableId
    condition: SingleCondition
    target: JumpTarget


class MultipleRule(ApiModel):
    id: StableId
    condition: MultipleCondition
    target: JumpTarget


class NumberRule(ApiModel):
    id: StableId
    condition: NumberCondition
    target: JumpTarget


class SingleValidation(ApiModel):
    pass


class OptionalBoundsValidation(ApiModel):
    @model_validator(mode="after")
    def reject_null_bounds(self):
        # None is an internal sentinel for an omitted limit, never a stored value.
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} 未限制时应省略字段，不能设置为 null")
        return self


class MultipleValidation(OptionalBoundsValidation):
    minSelections: Annotated[StrictInt, Field(ge=0, le=50)] | None = None
    maxSelections: Annotated[StrictInt, Field(ge=0, le=50)] | None = None


class TextValidation(OptionalBoundsValidation):
    minLength: Annotated[StrictInt, Field(ge=0, le=10000)] | None = None
    maxLength: Annotated[StrictInt, Field(ge=0, le=10000)] | None = None


class NumberValidation(OptionalBoundsValidation):
    integerOnly: StrictBool = False
    min: Numeric | None = None
    max: Numeric | None = None

    @field_validator("min", "max")
    @classmethod
    def finite(cls, value):
        return check_number(value) if value is not None else value


class QuestionBase(ApiModel):
    id: StableId
    title: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    description: Annotated[StrictStr, Field(max_length=2000)] = ""
    required: StrictBool = True

    @field_validator("title", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SingleQuestion(QuestionBase):
    type: Literal["single"]
    validation: SingleValidation = Field(default_factory=SingleValidation)
    options: Annotated[list[Option], Field(min_length=2, max_length=50, strict=True)]
    jumpRules: Annotated[list[SingleRule], Field(max_length=20, strict=True)] = Field(default_factory=list)


class MultipleQuestion(QuestionBase):
    type: Literal["multiple"]
    validation: MultipleValidation = Field(default_factory=MultipleValidation)
    options: Annotated[list[Option], Field(min_length=2, max_length=50, strict=True)]
    jumpRules: Annotated[list[MultipleRule], Field(max_length=20, strict=True)] = Field(default_factory=list)


class TextQuestion(QuestionBase):
    type: Literal["text"]
    validation: TextValidation = Field(default_factory=TextValidation)
    jumpRules: Annotated[list[SingleRule], Field(max_length=0, strict=True)] = Field(default_factory=list)


class NumberQuestion(QuestionBase):
    type: Literal["number"]
    validation: NumberValidation = Field(default_factory=NumberValidation)
    jumpRules: Annotated[list[NumberRule], Field(max_length=20, strict=True)] = Field(default_factory=list)


Question = Annotated[
    SingleQuestion | MultipleQuestion | TextQuestion | NumberQuestion,
    Field(discriminator="type"),
]
Questions = Annotated[list[Question], Field(max_length=100, strict=True)]


class ContentBase(ApiModel):
    title: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    description: Annotated[StrictStr, Field(max_length=2000)] = ""

    @field_validator("title", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SingleContent(ContentBase):
    type: Literal["single"]
    validation: SingleValidation = Field(default_factory=SingleValidation)
    options: Annotated[list[Option], Field(min_length=2, max_length=50, strict=True)]


class MultipleContent(ContentBase):
    type: Literal["multiple"]
    validation: MultipleValidation = Field(default_factory=MultipleValidation)
    options: Annotated[list[Option], Field(min_length=2, max_length=50, strict=True)]


class TextContent(ContentBase):
    type: Literal["text"]
    validation: TextValidation = Field(default_factory=TextValidation)


class NumberContent(ContentBase):
    type: Literal["number"]
    validation: NumberValidation = Field(default_factory=NumberValidation)


QuestionContent = Annotated[
    SingleContent | MultipleContent | TextContent | NumberContent,
    Field(discriminator="type"),
]
ObjectIdString = Annotated[StrictStr, Field(pattern=r"^[0-9a-fA-F]{24}$"), AfterValidator(str.lower)]


class QuestionCreate(ApiModel):
    content: QuestionContent


class QuestionVersionCreate(QuestionCreate):
    revision: Revision
    baseVersionId: ObjectIdString


class QuestionRestore(ApiModel):
    revision: Revision
    sourceVersionId: ObjectIdString


class QuestionCopy(ApiModel):
    versionId: ObjectIdString


class ArchiveRequest(ApiModel):
    revision: Revision
    archived: StrictBool


class ShareRequest(ApiModel):
    revision: Revision
    recipientUsername: Annotated[StrictStr, Field(min_length=1, max_length=128)]

    @field_validator("recipientUsername", mode="before")
    @classmethod
    def normalize_recipient(cls, value):
        # Bind the idempotency fingerprint to the same identity used by login.
        return unicodedata.normalize("NFKC", value).strip().lower() if isinstance(value, str) else value


class QuestionReference(ApiModel):
    id: StableId
    questionId: ObjectIdString
    versionId: ObjectIdString
    required: StrictBool = True
    jumpRules: Annotated[list[SingleRule | MultipleRule | NumberRule], Field(max_length=20, strict=True)] = Field(default_factory=list)


QuestionReferences = Annotated[list[QuestionReference], Field(max_length=100, strict=True)]


class SurveyCreate(ApiModel):
    title: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    description: Annotated[StrictStr, Field(max_length=5000)] = ""
    settings: SurveySettings
    deadlineAt: DeadlineAt = None
    questions: QuestionReferences = Field(default_factory=list)

    @field_validator("title", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SurveyPatch(ApiModel):
    revision: Revision
    title: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[StrictStr, Field(max_length=5000)] | None = None
    settings: SurveySettings | None = None
    deadlineAt: DeadlineAt = None
    questions: QuestionReferences | None = None

    @field_validator("title", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def explicit_nulls(self):
        for field in self.model_fields_set - {"deadlineAt"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} 不允许为 null")
        return self


class RevisionRequest(ApiModel):
    revision: Revision


class Answer(ApiModel):
    questionId: StableId
    value: StrictStr | Numeric | Annotated[list[StableId], Field(max_length=50, strict=True)] | None

    @field_validator("value")
    @classmethod
    def finite_value(cls, value):
        if type(value) in (int, float):
            return check_number(value)
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise ValueError("文本必须是合法的 Unicode 字符串") from exc
        return value


Answers = Annotated[list[Answer], Field(max_length=100, strict=True)]


class SubmitRequest(ApiModel):
    submissionKey: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    submissionToken: Annotated[StrictStr, Field(min_length=1, max_length=4096)]
    answers: Answers


class PreviewRequest(ApiModel):
    answers: Answers
    changedQuestionId: StableId | None = None
