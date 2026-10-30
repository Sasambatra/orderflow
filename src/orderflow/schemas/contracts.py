import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt, model_validator

Quantity = Annotated[StrictInt, Field(ge=1, le=100)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128, repr=False)


class OrderLineInput(StrictModel):
    product_id: uuid.UUID
    quantity: Quantity


class OrderInput(StrictModel):
    items: list[OrderLineInput] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def reject_duplicates(self):
        if len({item.product_id for item in self.items}) != len(self.items):
            raise ValueError("Each product must appear only once")
        return self

    def canonical(self) -> dict:
        return {
            "items": sorted(
                self.model_dump(mode="json")["items"], key=lambda item: item["product_id"]
            )
        }


class ProductInput(StrictModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    price_minor: Annotated[StrictInt, Field(ge=1, le=100_000_000)]


class ProductPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    price_minor: Annotated[StrictInt, Field(ge=1, le=100_000_000)] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_values(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null update")
        return self


class StockInput(StrictModel):
    delta: Annotated[StrictInt, Field(ge=-1_000_000, le=1_000_000)]
    reason: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def nonzero(self):
        if self.delta == 0:
            raise ValueError("Stock delta must not be zero")
        return self


class PaymentWebhook(StrictModel):
    event_id: uuid.UUID
    type: Literal["payment.succeeded", "payment.failed"]
    payment_id: str = Field(min_length=1, max_length=80)
    order_id: uuid.UUID
    amount_minor: Annotated[StrictInt, Field(ge=1)]
    currency: str = Field(min_length=3, max_length=3)
