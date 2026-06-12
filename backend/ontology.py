from enum import Enum
from typing import List

from pydantic import BaseModel


class ProductSegment(str, Enum):
    PRODUCT_A = "プロダクトA"
    PRODUCT_B = "プロダクトB"


class ContentType(str, Enum):
    SEMINAR_UPCOMING = "未来のセミナー（募集中）"
    EVENT_UPCOMING = "未来のイベント（募集中）"
    WHITE_PAPER = "資料・ホワイトペーパー"
    CASE_STUDY = "導入事例"


class LeadSegment(str, Enum):
    APPOINTMENT_BOOKED = "アポ獲得済み"
    HIGH_INTENT = "アポなし・感度高"
    NURTURING = "通常リード"


class BlockType(str, Enum):
    GREETING = "1_展示会のお礼と挨拶"
    SCHEDULE_PROPOSAL = "2_日程調整・候補日打診"
    CASE_STUDY_INTRO = "3_導入事例の紹介"
    PRODUCT_MATERIAL_INTRO = "4_プロダクト資料・ホワイトペーパーの紹介"
    SEMINAR_INTRO = "5_未来の募集中のセミナー案内"
    CLOSING = "6_結びの挨拶"


class StructuredLead(BaseModel):
    name: str
    company_name: str
    department: str
    job_title: str
    segment: LeadSegment
    interested_products: List[ProductSegment]
    extracted_challenge: str


class EmailBlock(BaseModel):
    block_type: BlockType
    reason_for_inclusion: str
    associated_content_ids: List[str] = []
    block_text: str


class TotalTailoredEmail(BaseModel):
    subject: str
    email_blocks: List[EmailBlock]


class ContentItem(BaseModel):
    id: str
    content_type: ContentType
    name: str
    description: str
    url: str
