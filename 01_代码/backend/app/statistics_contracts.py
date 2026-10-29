"""Cross-survey results are grouped by one immutable version in owned surveys."""
from typing import Annotated, Literal

from pydantic import Field, StrictStr

from .models import ApiModel, Numeric, ObjectIdString, Revision, StableId
from .views import Count, Page, QuestionKind


class StatisticQuestion(ApiModel):
    questionId: ObjectIdString
    title: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    displayVersionId: ObjectIdString
    displayVersionNo: Revision
    surveyCount: Count
    usedVersionCount: Count


class StatisticQuestionPage(Page):
    items: list[StatisticQuestion]


class StatisticVersion(ApiModel):
    questionId: ObjectIdString
    versionId: ObjectIdString
    versionNo: Revision
    title: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    type: QuestionKind
    surveyCount: Count


class StatisticVersionPage(Page):
    items: list[StatisticVersion]


class OptionStatistic(ApiModel):
    optionId: StableId
    label: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    count: Count
    ratio: Annotated[float, Field(ge=0, le=1)] | None


class StatisticsBase(ApiModel):
    questionId: ObjectIdString
    versionId: ObjectIdString
    versionNo: Revision
    title: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    surveyCount: Count
    totalResponses: Count
    answeredCount: Count
    unansweredCount: Count


class SingleStatistics(StatisticsBase):
    type: Literal['single']
    options: Annotated[list[OptionStatistic], Field(min_length=2, max_length=50)]


class MultipleStatistics(StatisticsBase):
    type: Literal['multiple']
    options: Annotated[list[OptionStatistic], Field(min_length=2, max_length=50)]


class TextStatistics(StatisticsBase):
    type: Literal['text']


class NumberStatistics(StatisticsBase):
    type: Literal['number']
    minimum: Numeric | None
    maximum: Numeric | None
    average: Numeric | None


VersionStatistics = Annotated[SingleStatistics | MultipleStatistics | TextStatistics | NumberStatistics, Field(discriminator='type')]


class TextValue(ApiModel):
    value: StrictStr


class NumberValue(ApiModel):
    value: Numeric


class TextValues(Page):
    questionId: ObjectIdString
    versionId: ObjectIdString
    type: Literal['text']
    items: list[TextValue]


class NumberValues(Page):
    questionId: ObjectIdString
    versionId: ObjectIdString
    type: Literal['number']
    items: list[NumberValue]


StatisticValuesPage = Annotated[TextValues | NumberValues, Field(discriminator='type')]
