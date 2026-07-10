"""google-genai クライアント生成 — 429/5xx への再試行をデフォルトで有効化する。

google-genai は http_options.retry_options が None だと一切リトライしない
（stop_after_attempt(1)）。Vertex AI のレート制限(429)は一時的なことが多いため、
SDK 既定のバックオフ（最大5回試行・1.0→60.0秒指数バックオフ）を明示的に有効化する。
"""

from google import genai
from google.genai import types

RETRY_OPTIONS = types.HttpRetryOptions()


def new_client() -> genai.Client:
    return genai.Client(http_options=types.HttpOptions(retry_options=RETRY_OPTIONS))
