"""Operator-only managed files API. Never exposed as Agent tools."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import paths, managed
from ..asset_store import AssetStore

router = APIRouter(prefix='/api/storage')


class AssetOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str = ''
    name: str = ''
    destination: str = ''
    revision: str | None = None


class HubDownload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repository: str
    revision: str = 'main'


class HubCredential(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str | None = None


class AssetInstall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    version: str


@router.get('/hub/credential')
def hub_credential_status():
    from ..vlm.store import CredentialStore
    return CredentialStore(Path(paths.NERV_HOME)).describe('HUGGINGFACE_TOKEN')


@router.get('/policy-catalog')
def policy_catalog():
    from ..installation import read_manifest
    from ..policy_catalog import entries
    try:
        manifest, _ = read_manifest()
        return {'policies': entries(manifest)}
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


@router.post('/hub/credential')
def hub_credential(body: HubCredential):
    from ..vlm.store import CredentialStore
    try:
        CredentialStore(Path(paths.NERV_HOME)).set('HUGGINGFACE_TOKEN', body.token)
        return {'configured': bool(body.token)}
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


@router.post('/hub/download')
def hub_download(body: HubDownload):
    from .module_jobs import start_policy
    try:
        return start_policy(body.repository, body.revision)
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


def store():
    return AssetStore(paths.NERV_HOME)


@router.get('/environments')
def environments():
    return {'storage_root': str(managed.storage_root(paths.NERV_HOME)),
            'data_home': str(Path(paths.NERV_HOME)), 'environments': managed.environments()}


@router.get('/assets')
def assets():
    records = store().list(include_removed=True)
    return {'assets': [row for row in records if not row.get('removed')],
            'archived': [row for row in records if row.get('removed')]}


@router.get('/asset-catalog')
def asset_catalog():
    from ..asset_catalog import entries
    from ..installation import read_manifest
    try:
        manifest, _ = read_manifest()
        return {'assets': entries(manifest)}
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


@router.post('/asset-catalog/install')
def install_asset(body: AssetInstall):
    from .module_jobs import start_asset
    try:
        return start_asset(body.id, body.version)
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


@router.post('/assets/import')
def import_asset(body: AssetOperation):
    try:
        return store().import_asset(body.source)
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error


@router.post('/assets/{key}/{operation}')
def operate(key: str, operation: str, body: AssetOperation):
    try:
        library = store()
        if operation in ('remove', 'restore'):
            return library.remove(key, restore=operation == 'restore')
        if operation == 'clone':
            return library.clone(key, body.name or None)
        if operation == 'save':
            return library.save(key)
        if operation == 'verify':
            _, result = library.resolve(key, body.revision)
            return result
        if operation == 'check':
            from ..asset_runtime import check
            from .registry import Registry
            return check(paths.NERV_HOME, key, body.revision, Registry().bodies)
        if operation == 'export':
            return library.export(key, body.destination, body.revision)
        if operation == 'inspect':
            return library.read(key)
        raise ValueError('Unknown asset operation')
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
