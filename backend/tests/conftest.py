"""テスト共通設定。

config.Settings は GOOGLE_CLOUD_PROJECT / FIREBASE_PROJECT_ID を必須とするため、
テスト対象モジュールの import より先にダミー値を注入する。`demo-` プレフィックスは
Firebase エミュレータ専用のプロジェクトIDであり、実プロジェクトへの誤接続を防ぐ。
"""

import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "demo-eventtune")
os.environ.setdefault("FIREBASE_PROJECT_ID", "demo-eventtune")
