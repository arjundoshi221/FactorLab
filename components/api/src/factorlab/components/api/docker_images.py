"""Read the host-produced Docker inventory without Docker socket access.

The host collector (deploy/scripts/collect-docker-images.py) writes ``snapshot.json``
and ``snapshot.v2.json``; v2 adds per-component rows. This API prefers v2 and falls
back to v1. Every model ignores unknown fields, so a newer collector never breaks an
older API.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from factorlab.components.api.hub import HubBuild, current_build

SNAPSHOT_DIR = Path("/run/docker-images")
_TOLERANT = ConfigDict(extra="ignore")


class DockerContainer(BaseModel):
    model_config = _TOLERANT
    name: str
    status: str
    started_at: datetime | None
    service: str | None = None
    image_ref: str | None = None


class DockerImageLabels(BaseModel):
    model_config = _TOLERANT
    revision: str | None = None
    version: str | None = None
    source: str | None = None
    created: str | None = None
    title: str | None = None
    component: str | None = None


class DockerImage(BaseModel):
    model_config = _TOLERANT
    id: str
    tags: list[str]
    digests: list[str]
    size_bytes: int
    created_at: datetime
    containers: list[DockerContainer]
    last_container_start_at: datetime | None
    release_activated_at: datetime | None
    current_release: bool = False
    platform: str | None = None
    labels: DockerImageLabels = Field(default_factory=DockerImageLabels)


class DockerRelease(BaseModel):
    model_config = _TOLERANT
    id: str
    commit: str | None = None
    image: str | None = None
    previous_release: str | None = None
    previous_image: str | None = None
    activated_at: datetime | None = None


class RunningService(BaseModel):
    model_config = _TOLERANT
    service: str
    container: str
    status: str
    started_at: datetime | None = None
    version: str | None = None
    image_id: str | None = None


class DeployedComponent(BaseModel):
    """A component the host deployer manages (``version`` is None while seeded)."""

    model_config = _TOLERANT
    component: str
    version: str | None = None
    image: str | None = None
    services: list[RunningService] = Field(default_factory=list)


class DockerImagesSnapshot(BaseModel):
    model_config = _TOLERANT
    snapshot_at: datetime
    release_id: str | None
    images: list[DockerImage]
    release: DockerRelease | None = None
    releases: list[DockerRelease] = Field(default_factory=list)
    platform_version: str | None = None
    components: list[DeployedComponent] = Field(default_factory=list)


class DockerImagesResponse(DockerImagesSnapshot):
    stale: bool
    api_build: HubBuild


def snapshot_path() -> Path:
    configured = os.getenv("FACTORLAB_DOCKER_IMAGES_SNAPSHOT")
    if configured:
        return Path(configured)
    v2 = SNAPSHOT_DIR / "snapshot.v2.json"
    return v2 if v2.exists() else SNAPSHOT_DIR / "snapshot.json"


def read_docker_images_snapshot(
    path: Path | None = None, *, now: datetime | None = None
) -> DockerImagesResponse:
    location = path or snapshot_path()
    snapshot = DockerImagesSnapshot.model_validate(json.loads(location.read_text(encoding="utf-8")))
    current = now or datetime.now(UTC)
    if snapshot.snapshot_at.tzinfo is None:
        raise ValueError("snapshot_at must include a timezone")
    return DockerImagesResponse(
        **snapshot.model_dump(),
        stale=current - snapshot.snapshot_at > timedelta(minutes=3),
        api_build=current_build(),
    )
