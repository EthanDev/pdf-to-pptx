"""Narrow job-scoped callbacks; maintenance requires a separate Google identity."""
import os

import cachecontrol
import requests
from fastapi import APIRouter, Header, HTTPException
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token
from pydantic import BaseModel, Field

import jobs
from cloud_state import Busy, Rejected

router = APIRouter()
_google_request = Request(session=cachecontrol.CacheControl(requests.Session()))


def token(header):
    if not header or not header.startswith('Bearer '):
        raise HTTPException(401, 'A credential is required.')
    return header[7:]


def invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Rejected as error:
        raise HTTPException(403, str(error)) from None
    except Busy:
        raise HTTPException(409, 'An attempt is already active.') from None
    except Exception:
        raise HTTPException(503, 'The service is temporarily unavailable.') from None


class Attempt(BaseModel):
    attempt_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class Upload(Attempt):
    size: int = Field(gt=0, le=300_000_000, strict=True)


class Finish(Attempt):
    filename: str = Field(max_length=100)
    report: dict


class Failure(Attempt):
    error: str = Field(max_length=400)
    retry: bool = False


@router.post('/internal/jobs/{jid}/claim')
def claim(jid: str, authorization: str | None = Header(None)):
    return invoke(jobs.backend.claim, jid, token(authorization))


@router.post('/internal/jobs/{jid}/upload')
def upload(jid: str, body: Upload, authorization: str | None = Header(None)):
    uri = invoke(jobs.backend.upload, jid, body.attempt_id, token(authorization), body.size)
    return {'upload_url': uri}


@router.post('/internal/jobs/{jid}/finish')
def finish(jid: str, body: Finish, authorization: str | None = Header(None)):
    import json
    if len(json.dumps(body.report)) > 256_000:
        raise HTTPException(400, 'The report is too large.')
    invoke(jobs.backend.finish, jid, body.attempt_id, token(authorization), body.report, body.filename)
    return {'ok': True}


@router.post('/internal/jobs/{jid}/fail')
def fail(jid: str, body: Failure, authorization: str | None = Header(None)):
    invoke(jobs.backend.fail, jid, body.attempt_id, token(authorization), body.error, retry=body.retry)
    return {'ok': True}


@router.post('/internal/maintenance')
def maintenance(authorization: str | None = Header(None)):
    try:
        claims = verify_oauth2_token(token(authorization), _google_request, audience=os.environ['SERVICE_URL'])
        if not claims.get('email_verified') or claims.get('email') != os.environ['MAINTENANCE_IDENTITY']:
            raise ValueError('Wrong identity')
    except Exception:
        raise HTTPException(403, 'A maintenance identity is required.') from None
    invoke(jobs.backend.maintain)
    return {'ok': True}
