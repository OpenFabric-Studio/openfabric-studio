"""CRUD for timeline-editor projects.

A project is an opaque JSON blob (lanes/clips/effect settings) the frontend
owns the shape of - unlike tracks, a project isn't tied to one generated
track's row, since its clips can reference audio from many different tracks/
stems at once, so it gets its own top-level table instead of living in a
column like stems_json/mix_settings_json do on `tracks`.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db

from ..client_contracts import ProjectCreateRequest, ProjectUpdateRequest, ProjectFullResponse, ProjectsResponse

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "name": row["name"],
        "data": json.loads(row["data_json"] or "{}"),
    }


@router.post("", response_model=ProjectFullResponse)
async def create_project(body: ProjectCreateRequest):
    project_id = db.insert_project(name=body.name, data=body.data)
    return _row_to_dict(db.get_project(project_id))


@router.get("", response_model=ProjectsResponse)
async def list_projects():
    return {"data": [dict(id=r["id"], created_at=r["created_at"], updated_at=r["updated_at"], name=r["name"]) for r in db.list_projects()]}


@router.get("/{project_id}", response_model=ProjectFullResponse)
async def get_project(project_id: int):
    row = db.get_project(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="project not found")
    return _row_to_dict(row)


@router.put("/{project_id}", response_model=ProjectFullResponse)
async def update_project(project_id: int, body: ProjectUpdateRequest):
    if not db.get_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    db.update_project(project_id, name=body.name, data=body.data)
    return _row_to_dict(db.get_project(project_id))


@router.delete("/{project_id}")
async def delete_project(project_id: int):
    if not db.delete_project(project_id):
        raise HTTPException(status_code=404, detail="project not found")
    return {"deleted": True}
