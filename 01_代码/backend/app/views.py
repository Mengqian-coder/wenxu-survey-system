"""Typed response contracts; library references never enter public question DTOs."""
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, StrictStr

from .models import (
    ApiModel, MultipleContent, MultipleQuestion, NumberContent, NumberQuestion,
    ObjectIdString, QuestionContent, Questions, Revision, SingleContent,
    SingleQuestion, StableId, SurveySettings, TextContent, TextQuestion,
)


Count = Annotated[StrictInt, Field(ge=0)]
QuestionKind = Literal["single", "multiple", "text", "number"]
VersionOperation = Literal["create", "edit", "restore", "copy", "migrate"]


class Permissions(ApiModel):
    canManage: StrictBool
    canUse: StrictBool


class UsePermission(ApiModel):
    canUse: StrictBool


class VersionSummary(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    versionNo: Revision
    title: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    type: QuestionKind
    baseVersionId: ObjectIdString | None
    operation: VersionOperation
    createdAt: AwareDatetime
    archivedAt: AwareDatetime | None


class OriginalOrigin(ApiModel):
    kind: Literal["original"]


class CopyOrigin(ApiModel):
    kind: Literal["copy"]
    questionId: ObjectIdString
    versionId: ObjectIdString


class LegacyOrigin(ApiModel):
    kind: Literal["legacy"]
    surveyId: ObjectIdString
    instanceId: StableId


class PrivateLegacyOrigin(ApiModel):
    kind: Literal["legacy"]


class QuestionSummary(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    ownerId: ObjectIdString
    revision: Revision
    latestVersionId: ObjectIdString
    latestVersion: VersionSummary
    archivedAt: AwareDatetime | None
    createdAt: AwareDatetime
    updatedAt: AwareDatetime
    permissions: Permissions


class QuestionView(QuestionSummary):
    origin: OriginalOrigin | CopyOrigin | LegacyOrigin | PrivateLegacyOrigin


class QuestionVersion(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    questionId: ObjectIdString
    versionNo: Revision
    baseVersionId: ObjectIdString | None
    operation: VersionOperation
    content: QuestionContent
    createdBy: ObjectIdString
    createdAt: AwareDatetime
    archivedAt: AwareDatetime | None
    permissions: UsePermission


class QuestionVersionResult(ApiModel):
    question: QuestionView
    version: QuestionVersion


class VersionResult(ApiModel):
    questionRevision: Revision
    version: QuestionVersion


class Page(ApiModel):
    total: Count
    page: Annotated[StrictInt, Field(ge=1)]
    pageSize: Annotated[StrictInt, Field(ge=1, le=100)]


class QuestionPage(Page):
    items: list[QuestionSummary]


class VersionPage(Page):
    items: list[VersionSummary]
    questionRevision: Revision


class ShareRecipient(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    username: Annotated[StrictStr, Field(min_length=3, max_length=32)]


class ShareView(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    recipient: ShareRecipient
    grantedAt: AwareDatetime
    revokedAt: AwareDatetime | None
    createdAt: AwareDatetime
    updatedAt: AwareDatetime


class ShareResult(ApiModel):
    questionRevision: Revision
    share: ShareView


class SharePage(Page):
    questionRevision: Revision
    items: list[ShareView]


class UsageItem(ApiModel):
    surveyId: ObjectIdString
    title: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    status: Literal["draft", "published", "closed"]
    instanceId: StableId
    versionId: ObjectIdString
    versionNo: Revision


class UsagePage(Page):
    questionId: ObjectIdString
    totalSurveyCount: Count
    visibleSurveyCount: Count
    otherSurveyCount: Count
    items: list[UsageItem]


class ReferenceIdentity(ApiModel):
    questionId: ObjectIdString
    versionId: ObjectIdString
    versionNo: Revision


class ReferencedSingle(SingleQuestion, ReferenceIdentity):
    pass


class ReferencedMultiple(MultipleQuestion, ReferenceIdentity):
    pass


class ReferencedText(TextQuestion, ReferenceIdentity):
    pass


class ReferencedNumber(NumberQuestion, ReferenceIdentity):
    pass


ReferencedQuestion = Annotated[ReferencedSingle | ReferencedMultiple | ReferencedText | ReferencedNumber, Field(discriminator="type")]


class SurveyBase(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    ownerId: ObjectIdString
    publicId: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]
    title: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    description: Annotated[StrictStr, Field(max_length=5000)]
    settings: SurveySettings
    deadlineAt: AwareDatetime | None
    status: Literal["draft", "published", "closed"]
    revision: Revision
    createdAt: AwareDatetime
    updatedAt: AwareDatetime
    publishedAt: AwareDatetime | None
    closedAt: AwareDatetime | None
    closeReason: Literal["manual", "deadline"] | None


class LegacySurvey(SurveyBase):
    schemaVersion: Literal[1]
    questions: Questions


class ReferencedSurvey(SurveyBase):
    schemaVersion: Literal[2]
    questions: Annotated[list[ReferencedQuestion], Field(max_length=100)]


SurveyView = Annotated[LegacySurvey | ReferencedSurvey, Field(discriminator="schemaVersion")]


class SurveySummary(SurveyBase):
    schemaVersion: Literal[1, 2]
    questionCount: Annotated[Count, Field(le=100)]
    responseCount: Count


class SurveyPage(Page):
    items: list[SurveySummary]


class PublicIdentity(ApiModel):
    id: StableId
    required: StrictBool


class PublicSingle(SingleContent, PublicIdentity):
    pass


class PublicMultiple(MultipleContent, PublicIdentity):
    pass


class PublicText(TextContent, PublicIdentity):
    pass


class PublicNumber(NumberContent, PublicIdentity):
    pass


PublicQuestion = Annotated[PublicSingle | PublicMultiple | PublicText | PublicNumber, Field(discriminator="type")]


class PublicSurvey(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    publicId: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]
    title: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    description: Annotated[StrictStr, Field(max_length=5000)]
    status: Literal["published", "closed"]
    settings: SurveySettings
    deadlineAt: AwareDatetime | None
    questions: Annotated[list[PublicQuestion], Field(max_length=100)]
    canSubmit: StrictBool


class ErrorDetails(ApiModel):
    code: StrictStr
    message: StrictStr
    details: Any = None


class ErrorResponse(ApiModel):
    error: ErrorDetails
