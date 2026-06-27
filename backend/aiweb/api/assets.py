"""素材库：上传 / 列表 / 删除。重名按覆盖处理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from aiweb.db import session_scope
from aiweb.models.asset import Asset
from aiweb.storage import get_storage

router = APIRouter(tags=["assets"])


def _guard():
    from aiweb.api import auth_guard
    return Depends(auth_guard)


@router.post("/assets", dependencies=[_guard()])
async def upload_asset(file: UploadFile = File(...), name: str | None = Form(default=None)):
    storage = get_storage()
    asset_name = name or file.filename
    if not asset_name:
        raise HTTPException(status_code=400, detail="缺少文件名")
    data = await file.read()
    rel = storage.save_asset(asset_name, data)
    async with session_scope() as s:
        existing = (await s.execute(select(Asset).where(Asset.name == asset_name))).scalars().first()
        if existing:
            existing.path = rel
            existing.size = len(data)
            existing.mime = file.content_type
            asset = existing
        else:
            asset = Asset(name=asset_name, path=rel, size=len(data), mime=file.content_type)
            s.add(asset)
        await s.flush()
        return {"id": asset.id, "name": asset.name, "url": storage.url_for(rel), "size": asset.size, "mime": asset.mime}


@router.get("/assets", dependencies=[_guard()])
async def list_assets():
    storage = get_storage()
    async with session_scope() as s:
        rows = (await s.execute(select(Asset).order_by(Asset.created_at.desc()))).scalars().all()
        return [{
            "id": a.id, "name": a.name, "url": storage.url_for(a.path),
            "size": a.size, "mime": a.mime, "createdAt": a.created_at.isoformat(),
        } for a in rows]


@router.delete("/assets/{asset_id}", dependencies=[_guard()])
async def delete_asset(asset_id: str):
    async with session_scope() as s:
        asset = await s.get(Asset, asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="asset not found")
        get_storage().delete_asset(asset.name)
        await s.delete(asset)
        return {"deleted": asset_id}
