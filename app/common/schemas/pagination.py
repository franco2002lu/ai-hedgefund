from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=500, description="Max items to return")
    offset: int = Field(default=0, ge=0, description="Number of items to skip")


class PaginatedResponse(BaseModel):
    items: list
    total: int
    limit: int
    offset: int
    has_more: bool
