from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class RootResponse(BaseModel):
    service: str = Field(description="Service identifier.")
    version: str = Field(description="API version string.")
    status: str = Field(description="Liveness indicator, e.g. 'ok'.")


# Core request/response models
class PromptRequest(BaseModel):
    mode: str = Field(..., description='"intent"|"cve"')
    difficulty: Optional[str] = Field(None, description='beginner|easy|medium|hard')
    category: Optional[str] = Field(None, description='web|crypto|rev|pwn|misc')
    topic: Optional[str] = Field(None, description='free text vulnerability or tool')
    cve: Optional[str] = Field(None, description='CVE-YYYY-NNNN')
    constraints: Optional[Dict[str, object]] = Field(None, description='Optional constraints map')
    model: Optional[str] = Field(None, description='Model/provider string')
    # Allow the caller to attach an arbitrary event/payload separate from the prompt
    event: Optional[Dict[str, object]] = Field(None, description='Optional event JSON supplied by user')


class GenerateResponse(BaseModel):
    run_id: str
    status: str = Field(description='accepted|started|queued')
    message: Optional[str] = None


class RunSummary(BaseModel):
    run_id: str
    prompt: Optional[PromptRequest] = None
    status: str = Field(description='pending|running|failed|succeeded|cancelled')
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current_stage: Optional[str] = None


class StageStatus(BaseModel):
    name: str
    status: str = Field(description='queued|running|passed|failed')
    summary: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class ArtifactEntry(BaseModel):
    path: str
    role: str
    agent: str
    size: Optional[int] = None


class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str


class ValidationResult(BaseModel):
    passed: bool
    flag: Optional[str] = None
    logs: Optional[List[LogEntry]] = None
    error: Optional[str] = None


class RunDetail(BaseModel):
    summary: RunSummary
    stages: List[StageStatus]
    artifacts: List[ArtifactEntry]
    validation: Optional[ValidationResult] = None


class KBImportResponse(BaseModel):
    id: str
    count: int


class SkillFile(BaseModel):
    agent: str
    content: str
    sha256: Optional[str] = None


__all__ = [
    'RootResponse',
    'PromptRequest',
    'GenerateResponse',
    'RunSummary',
    'StageStatus',
    'RunDetail',
    'ArtifactEntry',
    'LogEntry',
    'ValidationResult',
    'KBImportResponse',
    'SkillFile',
]
