from fastapi import FastAPI

from api.schemas import RootResponse

app = FastAPI(
    title="ToroidBot REST API",
    version="0.1.0",
    description="REST surface for the CTF challenge generator.",
)


@app.get("/", response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(service="toroidbot-api", version=app.version, status="ok")
