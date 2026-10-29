"""Personal question bank contracts, including private unavailable entries."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictStr, field_validator, model_validator

from .models import ApiModel, ObjectIdString, Revision
from .views import Count, Page, QuestionSummary


BankName = Annotated[StrictStr, Field(min_length=1, max_length=200)]
BankDescription = Annotated[StrictStr, Field(max_length=2000)]


class BankCreate(ApiModel):
    name: BankName
    description: BankDescription = ""

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class BankPatch(ApiModel):
    revision: Revision
    name: BankName | None = None
    description: BankDescription | None = None

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def changed_fields(self):
        fields = self.model_fields_set - {"revision"}
        if not fields:
            raise ValueError("至少需要更新题库名称或说明")
        if any(getattr(self, name) is None for name in fields):
            raise ValueError("题库名称和说明不能设置为 null")
        return self


class BankView(ApiModel):
    id: ObjectIdString = Field(alias="_id")
    ownerId: ObjectIdString
    name: BankName
    description: BankDescription
    revision: Revision
    itemCount: Count
    createdAt: AwareDatetime
    updatedAt: AwareDatetime


class BankPage(Page):
    items: list[BankView]


class UnavailableBankItem(ApiModel):
    questionId: ObjectIdString
    available: Literal[False]


class AvailableBankItem(ApiModel):
    questionId: ObjectIdString
    available: Literal[True]
    question: QuestionSummary


BankItem = Annotated[AvailableBankItem | UnavailableBankItem, Field(discriminator="available")]


class BankItemPage(Page):
    items: list[BankItem]


class BankDeleted(ApiModel):
    bankId: ObjectIdString
    deleted: Literal[True]


class BankItemAdded(ApiModel):
    bankId: ObjectIdString
    questionId: ObjectIdString
    created: StrictBool


class BankItemRemoved(ApiModel):
    bankId: ObjectIdString
    questionId: ObjectIdString
    removed: StrictBool
