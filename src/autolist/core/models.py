"""Data models shared by all modules (phase-0 set).

Store configuration models require every key to be present. Values documented as
"filled later" are typed ``X | None``: present-but-null means unknown, and code that
needs the value calls ``StoreConfig.require`` which raises ``ConfigMissing``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autolist.core.errors import ConfigMissing


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- store config


class StoreInfo(_Strict):
    name: str
    domain: str
    timezone: str


class IStoresConfig(_Strict):
    base_url: str
    company_id: int
    user_agent: str
    rate_limit_per_second: float
    languages: Any | None
    required_name_languages: list[Any] | None
    stock_statuses: Any | None


class GcpConfig(_Strict):
    project_id: str
    region: str | None
    reference_bucket: str | None


class SecretsConfig(_Strict):
    istores_token: str
    anthropic_api_key: str
    gemini_api_key: str
    gmail_oauth_client: str
    gmail_refresh_token: str


class EmailAuthRequirement(_Strict):
    dkim: Literal["pass"]
    dmarc: Literal["pass"]


class EmailChannelConfig(_Strict):
    agent_mailbox: str
    owner_address: str
    allowed_senders: list[str] = Field(min_length=1)
    require: EmailAuthRequirement
    pubsub_topic: str
    pubsub_subscription: str
    text_direction: Literal["rtl", "ltr"]
    language: str  # BCP 47 tag for the HTML part, e.g. "he"


class ChannelsConfig(_Strict):
    email: EmailChannelConfig


class Phase0Budget(_Strict):
    anthropic: Decimal = Field(ge=0)
    gemini: Decimal = Field(ge=0)


class CostsConfig(_Strict):
    currency: Literal["USD"]
    monthly_cap: Decimal = Field(gt=0)
    per_job_cap: Decimal = Field(gt=0)
    alert_thresholds: list[Decimal]
    phase0_budget: Phase0Budget

    @model_validator(mode="after")
    def _thresholds_below_cap(self) -> CostsConfig:
        if any(t <= 0 or t >= self.monthly_cap for t in self.alert_thresholds):
            raise ValueError("alert_thresholds must be > 0 and below monthly_cap")
        if list(self.alert_thresholds) != sorted(self.alert_thresholds):
            raise ValueError("alert_thresholds must be sorted ascending")
        return self


class LlmModels(_Strict):
    extract: str | None
    write: str | None
    classify: str | None


class LlmConfig(_Strict):
    provider: str
    models: LlmModels
    bakeoff_candidates: list[str] | None
    prices: dict[str, Any] | None


class ImageModelPrices(_Strict):
    """Standard (non-batch) prices from the provider's official page, per model."""

    input_usd_per_mtok: Decimal = Field(ge=0)
    per_image_usd: dict[str, Decimal]  # output image size label ("1K", "2K") -> USD


class ImagesConfig(_Strict):
    provider: str
    model: str | None
    generate_size: str | None
    bakeoff_candidates: list[str]
    est_input_tokens_per_reference: int | None  # authorize estimate only; real usage is recorded
    prices: dict[str, ImageModelPrices]
    output_px: int = Field(gt=0)
    variations_per_role: int = Field(gt=0)


class PublishingConfig(_Strict):
    image_url_ttl_s: int = Field(gt=0, le=3600)  # signed URL lifetime for store image fetches
    unhide_enabled: bool
    enabled_by: str | None
    enabled_at: str | None

    @model_validator(mode="after")
    def _enabled_needs_who_and_when(self) -> PublishingConfig:
        if self.unhide_enabled and (not self.enabled_by or not self.enabled_at):
            raise ValueError("unhide_enabled requires enabled_by and enabled_at")
        return self


class AgentConfig(_Strict):
    max_revision_rounds: int = Field(gt=0)
    reminder_after_days: int = Field(gt=0)
    expire_after_days: int = Field(gt=0)
    approval_token_ttl_hours: int = Field(gt=0)
    job_id_prefix: str = Field(min_length=1)  # e.g. "YTS-"
    first_job_number: int = Field(gt=0)
    job_step_timeout_s: int = Field(gt=0, le=3000)  # active work limit per job step
    developer_contact: str | None  # shown to the owner in receipts; gets failure alerts


class IntakeConfig(_Strict):
    """Limits on what an owner email may carry (attachment guard)."""

    image_types: list[
        Literal["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]
    ] = Field(min_length=1)
    max_attachments: int = Field(gt=0)
    max_attachment_bytes: int = Field(gt=0)
    max_image_pixels: int = Field(gt=0)  # refuses decompression bombs before decoding
    llm_image_max_edge_px: int = Field(gt=0)  # photos are downscaled before an LLM sees them


class DeployConfig(_Strict):
    stage: Literal["dev", "prod"]  # prod refuses secrets labelled stage=dev (infra/SPEC.md)


class StoreConfig(_Strict):
    store: StoreInfo
    istores: IStoresConfig
    gcp: GcpConfig
    secrets: SecretsConfig
    channels: ChannelsConfig
    costs: CostsConfig
    llm: LlmConfig
    images: ImagesConfig
    publishing: PublishingConfig
    agent: AgentConfig
    intake: IntakeConfig
    deploy: DeployConfig

    def require(self, path: str) -> Any:
        """Return the value at a dotted path; raise ``ConfigMissing`` if it is null."""
        node: Any = self
        for part in path.split("."):
            if isinstance(node, BaseModel):
                if part not in type(node).model_fields:
                    raise ConfigMissing(path)
                node = getattr(node, part)
            elif isinstance(node, dict):
                if part not in node:
                    raise ConfigMissing(path)
                node = node[part]
            else:
                raise ConfigMissing(path)
        if node is None:
            raise ConfigMissing(path)
        return node


# ---------------------------------------------------------------- provenance


class Provenance(_Strict):
    source: Literal[
        "client_email", "supplier_invoice", "playbook", "research", "store_api", "system"
    ]
    ref: str = Field(min_length=1)
    confidence: Literal["certain", "high", "medium", "low"]
    observed_at: datetime


class SourcedValue[T](BaseModel):
    """A value with its sources. ``None`` = unknown: it becomes a question, never a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: T | None
    provenance: list[Provenance] = Field(default_factory=list)


# ---------------------------------------------------------------- costs


CostCategory = Literal["llm", "image", "search"]


class CostEntry(_Strict):
    at: datetime
    job_id: str | None
    provider: str
    category: CostCategory  # set by the caller that knows what it paid for; no default
    operation: str
    units: float = Field(ge=0)
    unit: str
    usd: Decimal = Field(ge=0)
    estimated: bool

    @model_validator(mode="after")
    def _aware(self) -> CostEntry:
        if self.at.tzinfo is None:
            raise ValueError("CostEntry.at must be timezone-aware")
        return self


class CostDecision(_Strict):
    allowed: bool
    reason: str


class CostThresholdCrossed(_Strict):
    """Monthly spend crossed an alert threshold. Emitted once per threshold per month."""

    kind: Literal["threshold"] = "threshold"
    month: str
    threshold: Decimal
    month_spend: Decimal
    monthly_cap: Decimal


class CostCapReached(_Strict):
    """Monthly spend reached the monthly cap. Emitted once per month; paid calls stop."""

    kind: Literal["cap"] = "cap"
    month: str
    month_spend: Decimal
    monthly_cap: Decimal


CostAlert = Annotated[CostThresholdCrossed | CostCapReached, Field(discriminator="kind")]


class CostSummary(_Strict):
    """Read-only month figures for owner emails. Every number comes from recorded entries."""

    month: str
    month_spend: Decimal
    by_category: dict[CostCategory, Decimal]
    jobs_count: int
    monthly_cap: Decimal
    next_threshold: Decimal  # next alert threshold above spend; monthly_cap when none is left
    reset_date: date  # first day of next month, store timezone


# ---------------------------------------------------------------- images


class ReferenceImage(_Strict):
    name: str
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: bytes


class ImageRequest(_Strict):
    job_id: str | None
    model: str
    role: Literal["main", "lifestyle"]
    prompt_name: str
    variables: dict[str, str]
    references: list[ReferenceImage] = Field(min_length=1, max_length=4)


class GeneratedImage(_Strict):
    model: str
    role: Literal["main", "lifestyle"]
    mime_type: str
    data: bytes
    size_px: tuple[int, int]
    text_parts: list[str]
    finish_reason: str | None
    usd: Decimal
    estimated_cost: bool


# ---------------------------------------------------------------- storage and drafts


class ImageRef(_Strict):
    """An object in the private reference bucket."""

    bucket: str
    name: str
    content_type: Literal["image/jpeg", "image/png", "image/webp"]


class ProductDraft(_Strict):
    """Everything the publisher needs to create one hidden, disabled product."""

    job_id: str
    name: str = Field(min_length=1)
    description_html: str  # sanitized by the caller
    price: Decimal = Field(ge=0)
    quantity: int = Field(ge=0)
    sku: str = Field(min_length=1)
    model: str = Field(min_length=1)
    upc: str | None
    main_image: ImageRef
    extra_images: list[ImageRef]


# ---------------------------------------------------------------- jobs (skeleton)


class JobState(StrEnum):
    """Job states. Transitions are in ``core.state_machine`` (fast-track design, 14.11.2026).

    RESEARCHING, REVISING, APPROVED and PUBLISHED stay defined and unused until their phases.
    """

    RECEIVED = "RECEIVED"
    NEEDS_INFO = "NEEDS_INFO"
    RESEARCHING = "RESEARCHING"
    GENERATING = "GENERATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    REVISING = "REVISING"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    SAVED_HIDDEN = "SAVED_HIDDEN"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class JobFact(_Strict):
    """One fact about the product, with the exact words it came from."""

    key: str  # e.g. "price", "quantity", "model", "name"
    value: str
    quote: str  # exact substring of the source text
    source: Literal["email", "reply"]
    gmail_id: str


class JobVersion(_Strict):
    """One preview the owner was shown. Approval binds to a version's preview email."""

    number: int = Field(gt=0)
    created_at: datetime
    title: str
    description_html: str
    image_choice: Literal[1, 2]
    preview_rfc_message_id: str | None  # Message-ID of the preview email, set once sent


class Job(_Strict):
    job_id: str  # e.g. "YTS-1001"
    number: int = Field(gt=0)
    state: JobState
    source_gmail_id: str  # the owner's email that opened the job
    source_thread_id: str
    product_index: int = Field(ge=0)  # position of this product in that email
    thread_id: str | None  # the job's own thread, set when the agent opens it
    facts: list[JobFact] = Field(default_factory=list)
    printed_text: bool | None = None
    versions: list[JobVersion] = Field(default_factory=list)
    revision_count: int = Field(default=0, ge=0)
    istores_product_id: int | None = None
    sent_messages: list[str] = Field(default_factory=list)  # gmail ids of agent emails
    created_at: datetime
    updated_at: datetime
    active_since: datetime | None = None  # start of the current active step (watchdog)
    error: str | None = None

    def fact(self, key: str) -> JobFact | None:
        """The latest fact for ``key`` (a reply overrides the original email)."""
        found = [f for f in self.facts if f.key == key]
        return found[-1] if found else None


# ---------------------------------------------------------------- intake


class ProductRequest(_Strict):
    """One product from an owner email: quoted facts, its photos, and what is missing."""

    index: int = Field(ge=0)
    facts: list[JobFact]
    photo_indexes: list[int]  # 1-based positions in the email's accepted image list
    printed_text: bool | None
    missing_required: list[str]  # required detail keys with no fact (playbook list)
    unverified_fields: list[str]  # values the model gave without an exact quote: dropped, asked


class ParsedRequest(_Strict):
    items: list[ProductRequest]
    clearly_separated: bool
    unassigned_photos: list[int]
    questions: list[str]  # Hebrew, from the model plus the parser's own
    rejected_attachments: list[str]  # file names refused by the guard, with the reason
    accepted_images: list[ReferenceImage]  # the guarded, downscaled photos, in index order


# ---------------------------------------------------------------- channels


class Attachment(_Strict):
    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    data: bytes | None = None


class ChannelMessage(_Strict):
    """An inbound message after sender authentication (channel-neutral)."""

    channel: Literal["email"]
    message_id: str
    thread_id: str
    sender: str
    authenticated: bool
    received_at: datetime
    subject: str | None
    text: str
    attachments: list[Attachment] = Field(default_factory=list)


class OutboundMessage(_Strict):
    """A message to send. Body text comes only from a reply template in the store repo."""

    channel: Literal["email"]
    to: str
    template: str
    variables: dict[str, str] = Field(default_factory=dict)
    thread_id: str | None = None
    in_reply_to: str | None = None  # Message-ID of the message being answered
    attachments: list[Attachment] = Field(default_factory=list)


class HealthStatus(_Strict):
    ok: bool
    checked_at: datetime
    problems: list[str] = Field(default_factory=list)
    details: dict[str, str] = Field(default_factory=dict)
