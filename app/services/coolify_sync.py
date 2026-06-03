import logging
import os

import httpx
from sqlalchemy.orm import Session

from ..models import Project

logger = logging.getLogger(__name__)

COOLIFY_BASE_URL = os.environ.get("COOLIFY_BASE_URL", "").rstrip("/")
COOLIFY_TOKEN = os.environ.get("COOLIFY_TOKEN", "")


def is_configured() -> bool:
    return bool(COOLIFY_BASE_URL and COOLIFY_TOKEN)


async def _get(client: httpx.AsyncClient, path: str) -> list[dict]:
    resp = await client.get(
        f"{COOLIFY_BASE_URL}/api/v1/{path}",
        headers={"Authorization": f"Bearer {COOLIFY_TOKEN}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("data", [])


async def _build_env_project_map(client: httpx.AsyncClient) -> dict[int, str]:
    """Returns {environment_id: project_name} by fetching all projects with detail."""
    projects = await _get(client, "projects")
    mapping: dict[int, str] = {}
    for p in projects:
        detail_resp = await client.get(
            f"{COOLIFY_BASE_URL}/api/v1/projects/{p['uuid']}",
            headers={"Authorization": f"Bearer {COOLIFY_TOKEN}"},
            timeout=10,
        )
        if detail_resp.status_code != 200:
            logger.warning("coolify project detail failed: uuid=%s status=%s", p.get('uuid'), detail_resp.status_code)
            continue
        detail = detail_resp.json()
        project_name = detail.get("name", "")
        for env in detail.get("environments", []):
            env_id = env.get("id")
            if env_id and project_name:
                mapping[env_id] = project_name
    return mapping


async def sync_projects(db: Session) -> dict:
    created: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []

    async with httpx.AsyncClient() as client:
        apps = await _get(client, "applications")
        servers = await _get(client, "servers")
        env_project_map = await _build_env_project_map(client)

    try:
        # Sync applications
        for app in apps:
            uuid = app.get("uuid") or app.get("id", "")
            name = app.get("name") or uuid
            if not uuid or not name:
                continue
            env_id = app.get("environment_id")
            coolify_project_name = env_project_map.get(env_id, "") if env_id else ""

            p = db.query(Project).filter(Project.coolify_uuid == uuid).first()
            if not p:
                p = db.query(Project).filter(Project.name == name).first()
            if p:
                changed = False
                if not p.coolify_uuid:
                    p.coolify_uuid = uuid
                    changed = True
                if coolify_project_name and p.coolify_project_name != coolify_project_name:
                    p.coolify_project_name = coolify_project_name
                    changed = True
                if changed:
                    updated.append(name)
                else:
                    skipped.append(name)
            else:
                db.add(Project(name=name, coolify_uuid=uuid, coolify_project_name=coolify_project_name or None))
                created.append(name)

        # Sync servers
        for srv in servers:
            uuid = srv.get("uuid") or srv.get("id", "")
            name = srv.get("name") or uuid
            if not uuid or not name:
                continue
            server_project_name = f"{name} (server)"
            p = db.query(Project).filter(Project.coolify_server_uuid == uuid).first()
            if not p:
                p = db.query(Project).filter(Project.name == server_project_name).first()
            if p:
                if not p.coolify_server_uuid:
                    p.coolify_server_uuid = uuid
                    updated.append(server_project_name)
                else:
                    skipped.append(server_project_name)
            else:
                db.add(Project(name=server_project_name, coolify_server_uuid=uuid))
                created.append(server_project_name)

        db.commit()
    except Exception:
        db.rollback()
        raise

    return {"created": created, "updated": updated, "skipped": skipped}
