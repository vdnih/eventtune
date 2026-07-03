"""
Users Router — /api/users

利用者本人のプロフィールと利用規約への同意状態を扱う。

同意記録（terms_accepted_version / terms_accepted_at）は users/{uid} に保持する。
このコレクションは（members とは別に）「アプリとしてのユーザー」を表す唯一のドキュメントで、
スペース未所属のユーザーでも存在しうる。書込は本 API（Admin SDK）経由のみ
（Firestore rules でクライアント write は拒否）。

セキュリティ: uid は検証済みトークンから取得し、リクエストボディは信用しない。
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import firestore
from pydantic import BaseModel

from dependencies import get_current_user
from legal import CURRENT_TERMS_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


class AcceptTermsRequest(BaseModel):
    version: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    """本人のプロフィールと同意状態を返す。

    users/{uid} が存在しなければ未同意（terms_accepted_version=None）として返す。
    current_terms_version はサーバの現行バージョンで、フロントはこれと
    terms_accepted_version の一致で同意ゲートの要否を判定する。
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="No uid in token")

    db = firestore.client()
    doc = db.document(f"users/{uid}").get()
    data = doc.to_dict() if doc.exists else {}
    return {
        "uid": uid,
        "email": user.get("email", ""),
        "terms_accepted_version": data.get("terms_accepted_version"),
        "terms_accepted_at": data.get("terms_accepted_at"),
        "current_terms_version": CURRENT_TERMS_VERSION,
    }


@router.post("/me/accept-terms")
async def accept_terms(body: AcceptTermsRequest, user: dict = Depends(get_current_user)):
    """利用規約への同意を記録する。

    クライアントは現行バージョンを送る。サーバの CURRENT_TERMS_VERSION と一致しない
    場合は 400（古い/未知のバージョンへの同意は受け付けない）。同意日時はサーバ時刻。
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="No uid in token")

    if body.version != CURRENT_TERMS_VERSION:
        raise HTTPException(status_code=400, detail="Unsupported terms version")

    db = firestore.client()
    now = _now_iso()
    db.document(f"users/{uid}").set(
        {
            "user_id": uid,
            "email": user.get("email", ""),
            "terms_accepted_version": body.version,
            "terms_accepted_at": now,
            "updated_at": now,
        },
        merge=True,
    )
    return {
        "terms_accepted_version": body.version,
        "terms_accepted_at": now,
        "current_terms_version": CURRENT_TERMS_VERSION,
    }
