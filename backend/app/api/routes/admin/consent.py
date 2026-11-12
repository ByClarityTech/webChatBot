from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from app.api.deps.auth import require_access
from app.core.permissions import Area
from app.db.models.consent import list_consent_texts
from app.db.models.user import UserRecord
from app.schemas.consent import ConsentTextItem, ConsentTextListResponse
from app.services import consent as consent_service
from app.services.admin_audit import log_admin_action

router = APIRouter(prefix="/admin/consent-texts", tags=["admin-consent"])

ConsentActor = Annotated[UserRecord, Depends(require_access(Area.consent_texts))]


def _item(text) -> ConsentTextItem:
    return ConsentTextItem(
        purpose=text.purpose,
        version=text.version,
        body=text.body,
        approved_at=text.approved_at,
        created_at=text.created_at,
    )


@router.get("", response_model=ConsentTextListResponse)
async def list_texts(actor: ConsentActor) -> ConsentTextListResponse:
    return ConsentTextListResponse(texts=[_item(text) for text in await list_consent_texts()])


@router.post("/{purpose}/{version}/approve", response_model=ConsentTextItem)
async def approve_consent_text(
    request: Request,
    actor: ConsentActor,
    purpose: Literal["share_contact", "callback"],
    version: Annotated[int, Path(ge=1)],
) -> ConsentTextItem:
    """Marks one wording version as approved, so it can be shown to visitors. Once only."""
    text = await consent_service.approve(purpose, version)
    if text is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consent text not found")
    await log_admin_action(
        "consent_text.approve",
        request=request,
        resource_type="consent_text",
        resource_id=f"{purpose}:{version}",
    )
    return _item(text)
