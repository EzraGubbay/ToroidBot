from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import RootResponse

from api.routes.generation import router as generation_router
from api.routes.runs import router as runs_router
from api.routes.artifacts import router as artifacts_router
from api.routes.stream import router as stream_router
from api.routes.skills import router as skills_router
from api.routes.debug import router as debug_router
from api.routes.kb import router as kb_router
from api.routes.agents import router as agents_router
from api.routes.settings import router as settings_router
from api.routes.presets import router as presets_router


app = FastAPI(
    title="ToroidBot REST API",
    version="0.1.0",
    description="REST surface for the CTF challenge generator.",
)


app.add_middleware(
    CORSMiddleware,
    # TODO: restrict origins in production.
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/', response_model=RootResponse)
def root() -> RootResponse:
    return RootResponse(service='toroidbot-api', version=app.version, status='ok')


# include routers
app.include_router(generation_router)
app.include_router(runs_router)
app.include_router(artifacts_router)
app.include_router(stream_router)
app.include_router(skills_router)
app.include_router(debug_router)
app.include_router(kb_router)
app.include_router(agents_router)
app.include_router(settings_router)
app.include_router(presets_router)

