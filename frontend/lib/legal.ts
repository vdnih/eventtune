/**
 * 法務文書のバージョン・事業者情報（表示用）
 *
 * 同意判定の「真実」はバックエンド（backend/legal.py CURRENT_TERMS_VERSION）が持ち、
 * フロントは /api/users/me が返す current_terms_version と accepted_version を比較する。
 * ここの CURRENT_TERMS_VERSION は表示・POST 時の値として backend と一致させること。
 *
 * プレースホルダ（{{...}}）は公開前に確定させる。
 */
export const CURRENT_TERMS_VERSION = "2026-07-03";
export const TERMS_LAST_UPDATED = "2026年7月3日";
export const PRIVACY_LAST_UPDATED = "2026年7月3日";

/**
 * ハッカソン向け試作品である旨の注意喚起（UI 各所で共通利用し文言のブレを防ぐ）。
 * 短文版。利用規約・ヘルプ本文にはより詳しい版を記載する。
 */
export const HACKATHON_NOTICE =
  "本サービスはハッカソン向けの試作品（プロトタイプ）です。実在する個人情報（顧客リスト・ハウスリスト等）のアップロードはお控えください。動作確認にはダミーデータをご利用ください。";
