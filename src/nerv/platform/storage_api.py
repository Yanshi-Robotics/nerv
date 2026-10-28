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


@router.get('/hub/credential')
def hub_credential_status():
    from ..vlm.store import CredentialStore
    return CredentialStore(Path(paths.NERV_HOME)).describe('HUGGINGFACE_TOKEN')


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
    return {'assets': store().list()}


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
        if operation == 'clone':
            return library.clone(key, body.name or None)
        if operation == 'save':
            return library.save(key)
        if operation == 'verify':
            _, result = library.resolve(key, body.revision)
            return result
        if operation == 'export':
            return library.export(key, body.destination, body.revision)
        if operation == 'inspect':
            return library.read(key)
        raise ValueError('Unknown asset operation')
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
