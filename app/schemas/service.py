from typing import Any, Optional, List
from pydantic import BaseModel


class CreateServiceRequest(BaseModel):
    name: str
    description: str = ""
    model: str
    system_prompt_version_id: Optional[str] = None
    user_profile_version_id: Optional[str] = None
    allowed_docs: List[str] = ["*"]
    allowed_scripts: List[str] = ["*"]
    capabilities: List[str] = []
    research_tools: bool = False
    published: bool = True
    max_conversations: int = 1000


class UpdateServiceRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    system_prompt_version_id: Optional[str] = None
    user_profile_version_id: Optional[str] = None
    allowed_docs: Optional[List[str]] = None
    allowed_scripts: Optional[List[str]] = None
    capabilities: Optional[List[str]] = None
    research_tools: Optional[bool] = None
    published: Optional[bool] = None
    max_conversations: Optional[int] = None


class CreateKeyRequest(BaseModel):
    name: str = "default"


class ConsumerChatRequest(BaseModel):
    """Custom SSE chat — mirrors admin /api/chat format."""
    conversation_id: str
    message: Any  # str or multimodal list


class ConsumerCompletionsRequest(BaseModel):
    """OpenAI-compatible /v1/chat/completions."""
    messages: list
    stream: bool = True
    conversation_id: Optional[str] = None


class CreateConsumerConversationRequest(BaseModel):
    title: str = ""
