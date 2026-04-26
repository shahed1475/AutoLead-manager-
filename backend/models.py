from pydantic import BaseModel, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class LeadStatus(str, Enum):
    PENDING        = "PENDING"
    ENRICHED       = "ENRICHED"
    SCORED         = "SCORED"
    MESSAGES_READY = "MESSAGES_READY"
    SENT           = "SENT"
    REPLIED        = "REPLIED"
    SKIPPED        = "SKIPPED"


class LeadChannel(str, Enum):
    EMAIL    = "EMAIL"
    WHATSAPP = "WHATSAPP"
    BOTH     = "BOTH"


class LeadSource(str, Enum):
    GOOGLE_MAPS   = "GOOGLE_MAPS"
    YELP          = "YELP"
    YELLOW_PAGES  = "YELLOW_PAGES"
    GOOGLE_SEARCH = "GOOGLE_SEARCH"
    BING_MAPS     = "BING_MAPS"


class ScoreLabel(str, Enum):
    HOT  = "HOT"
    WARM = "WARM"
    COLD = "COLD"


class ReplyIntent(str, Enum):
    POSITIVE   = "POSITIVE"
    NEGATIVE   = "NEGATIVE"
    NEUTRAL    = "NEUTRAL"
    INTERESTED = "INTERESTED"
    SPAM       = "SPAM"


class MessageType(str, Enum):
    EMAIL    = "email"
    WHATSAPP = "whatsapp"


class MessageStatus(str, Enum):
    PENDING = "PENDING"
    SENT    = "SENT"
    FAILED  = "FAILED"


class CampaignStatus(str, Enum):
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    STOPPED   = "STOPPED"
    FAILED    = "FAILED"


# ─────────────────────────────────────────────────────────────────────────────
# Lead models
# ─────────────────────────────────────────────────────────────────────────────

class LeadBase(BaseModel):
    business_name: str
    phone:   Optional[str] = None
    email:   Optional[str] = None
    website: Optional[str] = None
    niche:   Optional[str] = None
    city:    Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None
    source:  Optional[str] = None


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    business_name:        Optional[str]         = None
    phone:                Optional[str]         = None
    email:                Optional[str]         = None
    website:              Optional[str]         = None
    niche:                Optional[str]         = None
    city:                 Optional[str]         = None
    country:              Optional[str]         = None
    address:              Optional[str]         = None
    status:               Optional[LeadStatus]  = None
    channel:              Optional[LeadChannel] = None
    source:               Optional[str]         = None
    rating:               Optional[float]       = None
    review_count:         Optional[int]         = None
    reviews_count:        Optional[int]         = None
    score:                Optional[int]         = None
    score_label:          Optional[str]         = None
    score_category:       Optional[str]         = None
    # Legacy AI messages
    ai_whatsapp_msg:      Optional[str]         = None
    ai_email_subject:     Optional[str]         = None
    ai_email_body:        Optional[str]         = None
    ai_followup_msg:      Optional[str]         = None
    ai_follow_up_1:       Optional[str]         = None
    ai_follow_up_2:       Optional[str]         = None
    ai_follow_up_3:       Optional[str]         = None
    # Legacy enrichment
    website_summary:      Optional[str]         = None
    business_gaps:        Optional[str]         = None
    pain_points:          Optional[str]         = None
    personalization_hook: Optional[str]         = None


class Lead(LeadBase):
    model_config = ConfigDict(from_attributes=True)

    id:      int
    status:  LeadStatus  = LeadStatus.PENDING
    channel: Optional[LeadChannel] = None

    # Scoring
    score:          int          = 0
    score_label:    str          = "COLD"
    score_category: Optional[str] = None

    # Data quality
    rating:          Optional[float] = None
    review_count:    Optional[int]   = None
    reviews_count:   Optional[int]   = None
    verified_email:  int             = 0
    has_social_links: int            = 0

    # Legacy AI message columns (moving to messages table in Upgrade 1)
    ai_whatsapp_msg:   Optional[str] = None
    ai_email_subject:  Optional[str] = None
    ai_email_body:     Optional[str] = None
    ai_followup_msg:   Optional[str] = None
    ai_follow_up_1:    Optional[str] = None
    ai_follow_up_2:    Optional[str] = None
    ai_follow_up_3:    Optional[str] = None

    # Legacy enrichment columns (moving to enriched_data table in Upgrade 1)
    enriched_at:          Optional[datetime] = None
    website_summary:      Optional[str]      = None
    business_gaps:        Optional[str]      = None
    pain_points:          Optional[str]      = None
    personalization_hook: Optional[str]      = None

    # Timestamps
    sent_at:             Optional[datetime] = None
    followup_sent_at:    Optional[datetime] = None
    follow_up_1_sent_at: Optional[datetime] = None
    follow_up_2_sent_at: Optional[datetime] = None
    follow_up_3_sent_at: Optional[datetime] = None
    created_at:          Optional[datetime] = None


class LeadListResponse(BaseModel):
    items:       List[Lead]
    total:       int
    page:        int
    page_size:   int
    total_pages: int


# ─────────────────────────────────────────────────────────────────────────────
# Enriched data model (new table)
# ─────────────────────────────────────────────────────────────────────────────

class EnrichedDataCreate(BaseModel):
    business_summary:    Optional[str]       = None
    target_audience:     Optional[str]       = None
    service_level:       Optional[str]       = None
    brand_positioning:   Optional[str]       = None
    marketing_gaps:      Optional[List[str]] = None
    growth_potential:    Optional[str]       = None
    best_pitch_strategy: Optional[str]       = None
    website_text:        Optional[str]       = None


class EnrichedData(EnrichedDataCreate):
    model_config = ConfigDict(from_attributes=True)

    id:          int
    lead_id:     int
    enriched_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Score model (new table)
# ─────────────────────────────────────────────────────────────────────────────

class ScoreCreate(BaseModel):
    digital_score:       float            = 0
    website_score:       float            = 0
    business_score:      float            = 0
    opportunity_score:   float            = 0
    final_score:         float            = 0
    category:            str              = "COLD"
    key_problems:        Optional[List[str]] = None
    opportunity_summary: Optional[str]    = None
    pitch_angle:         Optional[str]    = None


class Score(ScoreCreate):
    model_config = ConfigDict(from_attributes=True)

    id:        int
    lead_id:   int
    scored_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Message model (new table)
# ─────────────────────────────────────────────────────────────────────────────

class MessageCreate(BaseModel):
    lead_id:       int
    sequence_step: int                    = 1
    message_type:  str                            # 'email' | 'whatsapp'
    subject:       Optional[str]          = None
    body:          str
    scheduled_for: Optional[datetime]     = None


class Message(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            int
    lead_id:       int
    sequence_step: int              = 1
    message_type:  str
    subject:       Optional[str]   = None
    body:          Optional[str]   = None
    status:        str             = "PENDING"
    sent_at:       Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    created_at:    Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Reply model (new table)
# ─────────────────────────────────────────────────────────────────────────────

class ReplyCreate(BaseModel):
    lead_id:         Optional[int]            = None
    message_id:      Optional[int]            = None
    reply_text:      Optional[str]            = None
    detected_intent: Optional[str]            = None
    raw_email_data:  Optional[Dict[str, Any]] = None


class Reply(ReplyCreate):
    model_config = ConfigDict(from_attributes=True)

    id:          int
    received_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Campaign model (new table)
# ─────────────────────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    niche:     Optional[str]       = None
    city:      Optional[str]       = None
    country:   Optional[str]       = None
    sources:   Optional[List[str]] = None
    channel:   Optional[str]       = None
    daily_cap: Optional[int]       = None


class Campaign(CampaignCreate):
    model_config = ConfigDict(from_attributes=True)

    id:            int
    leads_found:   int             = 0
    leads_sent:    int             = 0
    leads_replied: int             = 0
    status:        str             = "RUNNING"
    started_at:    Optional[datetime] = None
    completed_at:  Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Reply inbox model (backwards compat)
# ─────────────────────────────────────────────────────────────────────────────

class ReplyInboxItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            int
    lead_id:       Optional[int]  = None
    from_email:    str
    subject:       Optional[str]  = None
    body_snippet:  Optional[str]  = None
    received_at:   Optional[str]  = None
    intent:        str            = "NEUTRAL"
    processed:     Any            = False      # bool (PG) or int (legacy)
    created_at:    Optional[datetime] = None
    business_name: Optional[str]  = None
    niche:         Optional[str]  = None
    city:          Optional[str]  = None
    lead_status:   Optional[str]  = None


class ReplyInboxResponse(BaseModel):
    items:       List[ReplyInboxItem]
    total:       int
    page:        int
    page_size:   int
    total_pages: int


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────

class EnrichRequest(BaseModel):
    lead_id: int


class ScoreRequest(BaseModel):
    lead_ids: Optional[List[int]] = None


class StatusUpdate(BaseModel):
    status: LeadStatus


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


class CampaignStartRequest(BaseModel):
    niche:     str
    city:      str
    channel:   LeadChannel
    daily_cap: int              = 20
    sources:   List[LeadSource] = [LeadSource.GOOGLE_MAPS]
    headless:  bool             = False

    @field_validator("daily_cap")
    @classmethod
    def clamp_cap(cls, v: int) -> int:
        return max(1, min(200, v))

    @field_validator("sources")
    @classmethod
    def at_least_one_source(cls, v: List[LeadSource]) -> List[LeadSource]:
        return v if v else [LeadSource.GOOGLE_MAPS]


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
    hot_leads:           int           = 0
    warm_leads:          int           = 0
    cold_leads:          int           = 0
    unread_replies:      int           = 0
    engine_status:       str           = "idle"
    next_run:            Optional[str] = None


class OllamaStatus(BaseModel):
    connected:        bool
    model:            str
    available_models: List[str]


class SettingsUpdate(BaseModel):
    key:   str
    value: str


class CampaignLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:        int
    lead_id:   Optional[int]
    channel:   str
    action:    str
    success:   bool
    error_msg: Optional[str]
    timestamp: datetime
