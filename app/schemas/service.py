from typing import Any, Optional, List, Literal
from pydantic import BaseModel


class ServiceRuntimeChoice(BaseModel):
    runtime: Literal['deepagents', 'codex', 'cursor'] = 'deepagents'
    profile_id: Optional[str] = None
    model: Optional[str] = None


class CreateServiceRequest(BaseModel):
    runtime_choice: Optional[ServiceRuntimeChoice] = None
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
    # Standalone chat page customization (rendered by /s/{id})
    welcome_message: str = ""
    quick_questions: List[str] = []


class UpdateServiceRequest(BaseModel):
    runtime_choice: Optional[ServiceRuntimeChoice] = None
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
    welcome_message: Optional[str] = None
    quick_questions: Optional[List[str]] = None


class CreateKeyRequest(BaseModel):
    name: str = "default"
    # hosted = 运营方付 LLM 费用（默认，行为不变）；byok = 调用方请求时自带凭据
    billing: str = "hosted"


class ByokFields(BaseModel):
    """调用方自带主对话模型凭据（仅 billing=byok 的 service key 可用）。

    ``model`` 也用于 hosted key 之外的模型选择；``region`` 只对 bedrock 有意义。
    """
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    region: Optional[str] = None


class ConsumerChatRequest(ByokFields):
    """Custom SSE chat — mirrors admin /api/chat format."""
    conversation_id: str
    message: Any  # str or multimodal list


class ConsumerCompletionsRequest(ByokFields):
    """OpenAI-compatible /v1/chat/completions."""
    messages: list
    stream: bool = True
    conversation_id: Optional[str] = None


class CreateConsumerConversationRequest(BaseModel):
    title: str = ""
