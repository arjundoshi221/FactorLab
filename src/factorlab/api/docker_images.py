"""Read the host-produced Docker inventory without Docker socket access."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class DockerContainer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    status: str
    started_at: datetime | None


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


class DockerImagesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_at: datetime
    release_id: str | None
    images: list[DockerImage]


class DockerImagesResponse(DockerImagesSnapshot):
    stale: bool


def read_docker_images_snapshot(
    path: Path | None = None, *, now: datetime | None = None
) -> DockerImagesResponse:
    location = path or Path(os.getenv("FACTORLAB_DOCKER_IMAGES_SNAPSHOT", "/run/docker-images/snapshot.json"))
    snapshot = DockerImagesSnapshot.model_validate(json.loads(location.read_text(encoding="utf-8")))
    current = now or datetime.now(UTC)
    if snapshot.snapshot_at.tzinfo is None:
        raise ValueError("snapshot_at must include a timezone")
    return DockerImagesResponse(
        **snapshot.model_dump(), stale=current - snapshot.snapshot_at > timedelta(minutes=3)
    )
