from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum



class LeadStatus(str, Enum):
    PENDING  = "PENDING"
    SENT     = "SENT"
    REPLIED  = "REPLIED"
    SKIPPED  = "SKIPPED"


class LeadChannel(str, Enum):
    EMAIL     = "EMAIL"
    WHATSAPP  = "WHATSAPP"
    BOTH      = "BOTH"


class LeadSource(str, Enum):
    GOOGLE_MAPS  = "GOOGLE_MAPS"
    YELP         = "YELP"
    YELLOW_PAGES = "YELLOW_PAGES"


class LeadBase(BaseModel):
    business_name: str
    phone:   Optional[str] = None
    email:   Optional[str] = None
    website: Optional[str] = None
    niche:   Optional[str] = None
    city:    Optional[str] = None


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    business_name:    Optional[str]         = None
    phone:            Optional[str]         = None
    email:            Optional[str]         = None
    website:          Optional[str]         = None
    niche:            Optional[str]         = None
    city:             Optional[str]         = None
    ai_whatsapp_msg:  Optional[str]         = None
    ai_email_subject: Optional[str]         = None
    ai_email_body:    Optional[str]         = None
    ai_followup_msg:  Optional[str]         = None
    status:           Optional[LeadStatus]  = None
    channel:          Optional[LeadChannel] = None


class Lead(LeadBase):
    id:               int
    ai_whatsapp_msg:  Optional[str]         = None
    ai_email_subject: Optional[str]         = None
    ai_email_body:    Optional[str]         = None
    ai_followup_msg:  Optional[str]         = None
    status:           LeadStatus            = LeadStatus.PENDING
    channel:          Optional[LeadChannel] = None
    created_at:       datetime
    sent_at:          Optional[datetime]    = None
    followup_sent_at: Optional[datetime]    = None

    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    items:       List[Lead]
    total:       int
    page:        int
    page_size:   int
    total_pages: int


class ScraperRequest(BaseModel):
    query:       str
    city:        str
    niche:       str
    max_results: int = 20

    @field_validator("max_results")
    @classmethod
    def cap_results(cls, v: int) -> int:
        return min(v, 100)


class AIGenerateRequest(BaseModel):
    lead_id:      int
    message_type: str = "all"


class BulkAIRequest(BaseModel):
    lead_ids:     Optional[List[int]] = None
    message_type: str = "all"


class CampaignSendRequest(BaseModel):
    lead_ids: List[int]
    channel:  LeadChannel


class DashboardStats(BaseModel):
    total_leads:         int
    pending:             int
    sent:                int
    replied:             int
    skipped:             int
    email_sent_today:    int
    whatsapp_sent_today: int
    sent_today:          int
    reply_rate:          float
    estimated_revenue:   float
    engine_status:       str           # "idle" | "running" | "scheduled"
    next_run:            Optional[str]


class OllamaStatus(BaseModel):
    connected:        bool
    model:            str
    available_models: List[str]


class SettingsUpdate(BaseModel):
    key:   str
    value: str


class CampaignLogEntry(BaseModel):
    id:        int
    lead_id:   Optional[int]
    channel:   str
    action:    str
    success:   bool
    error_msg: Optional[str]
    timestamp: datetime


class StatusUpdate(BaseModel):
    status: LeadStatus


class CampaignStartRequest(BaseModel):
    niche:     str
    city:      str
    channel:   LeadChannel
    daily_cap: int              = 20
    sources:   List[LeadSource] = [LeadSource.GOOGLE_MAPS]
    headless:  bool             = False   # False = visible Chrome window

    @field_validator("daily_cap")
    @classmethod
    def clamp_cap(cls, v: int) -> int:
        return max(1, min(200, v))

    @field_validator("sources")
    @classmethod
    def at_least_one_source(cls, v: List[LeadSource]) -> List[LeadSource]:
        return v if v else [LeadSource.GOOGLE_MAPS]
