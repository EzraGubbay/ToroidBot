from pydantic import BaseModel, Field


class RootResponse(BaseModel):
    service: str = Field(description="Service identifier.")
    version: str = Field(description="API version string.")
    status: str = Field(description="Liveness indicator, e.g. 'ok'.")
