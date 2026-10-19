"""Assistant module for Chronicle."""
from .openrouter_client import (
    OpenRouterClient,
    OpenRouterClientError,
    MissingAPIKeyError,
    APIRequestError,
    InvalidResponseError,
)
from .context_models import (
    AssistantContext,
    SessionCandidate,
    TranscriptExcerpt,
    SummaryExcerpt,
    ScreenshotReference,
)
from .session_resolver import (
    AssistantSessionResolver,
    ResolutionResult,
    ScopeResolution,
)
from .tools import (
    AssistantRetrievalTools,
)
from .service import (
    AssistantAnswerService,
    AnswerResponse,
)
