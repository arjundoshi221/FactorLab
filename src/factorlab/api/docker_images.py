"""Read the host-produced Docker inventory without Docker socket access."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from factorlab.api.hub import HubBuild, current_build


class DockerContainer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    status: str
    started_at: datetime | None
    service: str | None = None
    image_ref: str | None = None


class DockerImageLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: str | None = None
    version: str | None = None
    source: str | None = None
    created: str | None = None
    title: str | None = None


class DockerImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    model_config = ConfigDict(extra="forbid")
    id: str
    commit: str | None = None
    image: str | None = None
    previous_release: str | None = None
    previous_image: str | None = None
    activated_at: datetime | None = None


class DockerImagesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_at: datetime
    release_id: str | None
    images: list[DockerImage]
    release: DockerRelease | None = None
    releases: list[DockerRelease] = Field(default_factory=list)


class DockerImagesResponse(DockerImagesSnapshot):
    stale: bool
    api_build: HubBuild


def read_docker_images_snapshot(
    path: Path | None = None, *, now: datetime | None = None
) -> DockerImagesResponse:
    location = path or Path(os.getenv("FACTORLAB_DOCKER_IMAGES_SNAPSHOT", "/run/docker-images/snapshot.json"))
    snapshot = DockerImagesSnapshot.model_validate(json.loads(location.read_text(encoding="utf-8")))
    current = now or datetime.now(UTC)
    if snapshot.snapshot_at.tzinfo is None:
        raise ValueError("snapshot_at must include a timezone")
    return DockerImagesResponse(
        **snapshot.model_dump(),
        stale=current - snapshot.snapshot_at > timedelta(minutes=3),
        api_build=current_build(),
    )
