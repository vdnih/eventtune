"""
認証・認可ディペンデンシ — トラスト境界

- get_current_user: Firebase IDトークンを検証し uid（信頼できるアイデンティティ）を得る。
- get_space_context: クライアント提示の X-Space-Id（非信頼な主張）を、検証済み uid との
  組で membership 照合し、メンバーであれば SpaceContext を生成する。ここが唯一の
  「テナント確定＝トラスト境界」であり、下流コードはテナンシーを再判断しない。
- require_owner: owner 専用操作のガード。

セキュリティ方針（Space-ID Trust Boundary, docs/PHILOSOPHY_AND_NAMING.md）:
クライアントが送る space_id / role は信頼しない。認可は常に
「検証済み uid × サーバ保持の members ドキュメント」から再導出する。
"""

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth, firestore

from space import SpaceContext

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    try:
        decoded = auth.verify_id_token(credentials.credentials)
        return decoded
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


async def get_space_context(
    x_space_id: str = Header(..., alias="X-Space-Id"),
    user: dict = Depends(get_current_user),
) -> SpaceContext:
    """X-Space-Id（主張）を検証済み uid で membership 照合し SpaceContext を返す。

    members ドキュメントが存在しなければ 403。これにより、他人のスペースIDを
    ヘッダに入れても、その uid に対する membership が無い限り弾かれる。
    role はクライアント申告ではなく members ドキュメントから取得する。
    """
    uid = user.get("uid")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No uid in token")

    db = firestore.client()
    member_doc = db.document(f"spaces/{x_space_id}/members/{uid}").get()
    if not member_doc.exists:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this space",
        )

    role = member_doc.to_dict().get("role", "member")
    return SpaceContext(space_id=x_space_id, uid=uid, role=role, db=db)


async def require_owner(
    space: SpaceContext = Depends(get_space_context),
) -> SpaceContext:
    """owner 専用操作（メンバー管理・設定変更・スペース削除）のガード。"""
    if not space.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires the owner role",
        )
    return space
