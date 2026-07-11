"""
ConfirmationIntent — HIL（human-in-the-loop）確認への自由文回答を分類する

ボタンクリックとテキスト入力を同じ意味として扱うための共通部品。ボタンは常に
固定文言を送るが、ユーザーがテキストで「取り込む」「OK」等と打った場合も同じ
承認として扱いたい。キーワード一致では拾いきれない言い回し（「うん」「それで
お願い」等）があるため、軽量モデルで承認/取消/それ以外の3値に分類する。

現時点ではデータ取り込みの確認（IngestionPlanCard）からのみ呼ばれるが、対象を
特定の業務に結び付けない汎用実装にしてあり、将来 個別対応メール等の他の HIL
確認からも再利用できる。
"""

import logging
from typing import Literal

from google.genai import types
from pydantic import BaseModel

from config import get_settings
from genai_client import new_client
from metering import record_llm_response
from space import SpaceContext

logger = logging.getLogger(__name__)

ConfirmationIntentValue = Literal["approve", "cancel", "other"]


class _ConfirmationIntent(BaseModel):
    intent: ConfirmationIntentValue


async def classify_confirmation_intent(
    space: SpaceContext, message: str, context_summary: str
) -> ConfirmationIntentValue:
    """ユーザー発言を、保留中の確認に対する承認/取消/それ以外に分類する。

    分類に失敗した場合は "other" を返し、呼び出し側が通常のチャット応答へ
    フォールバックできるようにする（誤ってバッチ実行やキャンセルを走らせない）。
    """
    prompt = f"""\
ユーザーは直前に提示された次の提案への返答をしています。

【提案の内容】
{context_summary}

【ユーザーの発言】
{message}

この発言を分類してください。
- approve: 提案を承認し、実行してよいという意思表示（例:「取り込む」「OK」「お願いします」「それで進めて」）
- cancel: 提案を取り消したい、やめたいという意思表示（例:「キャンセル」「やめて」「やっぱりいい」）
- other: 承認でも取消でもない発言（質問・修正依頼・関係のない話題など）
"""
    _model = get_settings().model_agent
    try:
        response = await new_client().aio.models.generate_content(
            model=_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ConfirmationIntent,
            ),
        )
        record_llm_response(space, _model, response)
        return _ConfirmationIntent.model_validate_json(response.text).intent
    except Exception:
        logger.exception("classify_confirmation_intent failed: message=%s", message[:200])
        return "other"
