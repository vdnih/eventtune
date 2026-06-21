"""
Spaces Router — /api/spaces

スペース（テナント）の作成・一覧・設定、メンバー管理（招待/削除/role変更）、利用状況。

セキュリティ（Space-ID Trust Boundary）:
- スペース作成時の owner は、リクエストボディではなく検証済み uid から決定する。
- メンバーの書込（招待/削除/role変更）は owner 専用（require_owner）。クライアントは
  自分を勝手にメンバー追加できない（Firestore rules でも client write を全拒否）。
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth, firestore
from pydantic import BaseModel

from dependencies import get_current_user, get_space_context, require_owner
from plans import compute_credits, monthly_credit_limit
from space import SpaceContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── スキーマ ──────────────────────────────────────────────────────────────────

class CreateSpaceRequest(BaseModel):
    name: str
    description: str = ""


class UpdateSpaceRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    plan: str | None = None


class InviteMemberRequest(BaseModel):
    email: str
    role: str = "member"


class UpdateMemberRequest(BaseModel):
    role: str


# ── スペース CRUD ─────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_space(body: CreateSpaceRequest, user: dict = Depends(get_current_user)):
    """新規スペースを作成し、作成者を owner メンバーとして登録する。

    owner は検証済み uid から決定（リクエストボディは信用しない）。スペース doc と
    owner membership をバッチで原子的に作成する。
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="No uid in token")

    db = firestore.client()
    now = _now_iso()
    space_id = f"space_{uuid.uuid4().hex[:12]}"

    space_doc = {
        "space_id": space_id,
        "name": body.name,
        "plan": "free",
        "owner_uid": uid,
        "description": body.description,
        "created_at": now,
        "updated_at": now,
    }
    member_doc = {
        "user_id": uid,
        "email": user.get("email", ""),
        "role": "owner",
        "space_id": space_id,
        "space_name": body.name,
        "joined_at": now,
    }

    batch = db.batch()
    batch.set(db.document(f"spaces/{space_id}"), space_doc)
    batch.set(db.document(f"spaces/{space_id}/members/{uid}"), member_doc)
    batch.commit()

    return space_doc


@router.get("")
async def list_my_spaces(user: dict = Depends(get_current_user)):
    """自分が所属するスペース一覧を返す。

    members コレクショングループを user_id で横断クエリし、所属する全スペースを得る。
    """
    uid = user.get("uid")
    db = firestore.client()
    member_docs = (
        db.collection_group("members").where("user_id", "==", uid).get()
    )
    spaces = []
    for m in member_docs:
        data = m.to_dict()
        spaces.append({
            "space_id": data.get("space_id"),
            "name": data.get("space_name"),
            "role": data.get("role"),
        })
    spaces.sort(key=lambda s: s.get("name") or "")
    return {"spaces": spaces, "count": len(spaces)}


@router.get("/{space_id}")
async def get_space(space_id: str, space: SpaceContext = Depends(get_space_context)):
    """スペース詳細を返す（メンバーのみ）。パスの space_id とヘッダの整合も検証する。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")
    doc = space.db.document(f"spaces/{space_id}").get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Space not found")
    return {**doc.to_dict(), "role": space.role}


@router.patch("/{space_id}")
async def update_space(
    space_id: str,
    body: UpdateSpaceRequest,
    space: SpaceContext = Depends(require_owner),
):
    """スペース設定を更新する（owner のみ）。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = _now_iso()
    ref = space.db.document(f"spaces/{space_id}")
    ref.update(updates)

    # name 変更時は members の非正規化 space_name も同期する
    if "name" in updates:
        for m in space.col("members").get():
            m.reference.update({"space_name": updates["name"]})

    return {**ref.get().to_dict(), "role": space.role}


@router.delete("/{space_id}", status_code=204)
async def delete_space(space_id: str, space: SpaceContext = Depends(require_owner)):
    """スペースと配下の全データを再帰削除する（owner のみ）。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")
    space.db.recursive_delete(space.db.document(f"spaces/{space_id}"))
    return None


# ── メンバー管理 ──────────────────────────────────────────────────────────────

@router.get("/{space_id}/members")
async def list_members(space_id: str, space: SpaceContext = Depends(get_space_context)):
    """スペースのメンバー一覧を返す（メンバーのみ）。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")
    members = [m.to_dict() for m in space.col("members").get()]
    members.sort(key=lambda m: (m.get("role") != "owner", m.get("email") or ""))
    return {"members": members, "count": len(members)}


@router.post("/{space_id}/members", status_code=201)
async def invite_member(
    space_id: str,
    body: InviteMemberRequest,
    space: SpaceContext = Depends(require_owner),
):
    """email でユーザーを招待してメンバーに追加する（owner のみ）。

    email から Firebase uid を解決する。対象ユーザーが未登録なら 404 を返す。
    """
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")

    try:
        invited = auth.get_user_by_email(body.email)
    except Exception:
        raise HTTPException(status_code=404, detail="No user found with that email")

    role = body.role if body.role in ("owner", "member") else "member"
    member_ref = space.doc(f"members/{invited.uid}")
    if member_ref.get().exists:
        raise HTTPException(status_code=409, detail="User is already a member")

    space_doc = space.db.document(f"spaces/{space_id}").get().to_dict() or {}
    member_doc = {
        "user_id": invited.uid,
        "email": body.email,
        "role": role,
        "space_id": space_id,
        "space_name": space_doc.get("name", ""),
        "joined_at": _now_iso(),
    }
    member_ref.set(member_doc)
    return member_doc


@router.patch("/{space_id}/members/{member_uid}")
async def update_member(
    space_id: str,
    member_uid: str,
    body: UpdateMemberRequest,
    space: SpaceContext = Depends(require_owner),
):
    """メンバーの role を変更する（owner のみ）。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")
    if body.role not in ("owner", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")

    ref = space.doc(f"members/{member_uid}")
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Member not found")
    # owner を member に降格する場合、最後の owner を失わないよう保護する
    if body.role == "member":
        owners = [m for m in space.col("members").get() if m.to_dict().get("role") == "owner"]
        if len(owners) <= 1 and any(o.id == member_uid for o in owners):
            raise HTTPException(status_code=400, detail="Cannot demote the last owner")

    ref.update({"role": body.role})
    return ref.get().to_dict()


@router.delete("/{space_id}/members/{member_uid}", status_code=204)
async def remove_member(
    space_id: str,
    member_uid: str,
    space: SpaceContext = Depends(require_owner),
):
    """メンバーを削除する（owner のみ）。最後の owner は削除できない。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")
    ref = space.doc(f"members/{member_uid}")
    doc = ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Member not found")
    if doc.to_dict().get("role") == "owner":
        owners = [m for m in space.col("members").get() if m.to_dict().get("role") == "owner"]
        if len(owners) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last owner")
    ref.delete()
    return None


# ── 利用状況（メータリング） ──────────────────────────────────────────────────

@router.get("/{space_id}/usage")
async def get_usage(space_id: str, space: SpaceContext = Depends(get_space_context)):
    """当月のリソース消費（生実績）と換算後クレジット、プラン上限を返す。"""
    if space_id != space.space_id:
        raise HTTPException(status_code=400, detail="space_id mismatch with X-Space-Id")

    period = _period()
    usage_doc = space.col("usage").document(period).get()
    usage = usage_doc.to_dict() if usage_doc.exists else {}

    space_doc = space.db.document(f"spaces/{space_id}").get().to_dict() or {}
    plan = space_doc.get("plan", "free")

    credits_used = compute_credits(usage)
    limit = monthly_credit_limit(plan)
    return {
        "period": period,
        "plan": plan,
        "usage": {"llm": usage.get("llm", {}), "compute": usage.get("compute", {})},
        "credits_used": credits_used,
        "credit_limit": limit,
        "credit_remaining": round(max(limit - credits_used, 0), 4),
    }
