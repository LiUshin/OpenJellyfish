from typing import Any, Optional, Union
from pydantic import BaseModel


class AuthRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    reg_key: str


class CreateConversationRequest(BaseModel):
    title: str = "新对话"


class ChatRequest(BaseModel):
    conversation_id: str
    message: Any  # str or multimodal list [{"type":"text",...},{"type":"image_url",...}]
    model: Optional[str] = None
    capabilities: Optional[list] = None
    plan_mode: Optional[bool] = None
    yolo: Optional[bool] = None


class ResumeRequest(BaseModel):
    conversation_id: str
    decisions: list
    model: Optional[str] = None
    capabilities: Optional[list] = None
    yolo: Optional[bool] = None


class StopChatRequest(BaseModel):
    conversation_id: str


class WriteFileRequest(BaseModel):
    path: str
    content: str


class EditFileRequest(BaseModel):
    path: str
    old_string: str
    new_string: str


class MoveFileRequest(BaseModel):
    source: str
    destination: str


class CopyFileRequest(BaseModel):
    source: str
    destination: str


class RunScriptRequest(BaseModel):
    script_path: str
    args: Optional[list] = None
    input_data: Optional[str] = None
    timeout: int = 30


class SystemPromptRequest(BaseModel):
    prompt: str


class SaveVersionRequest(BaseModel):
    content: str
    label: str = ""
    note: str = ""


class UpdateVersionMetaRequest(BaseModel):
    label: Optional[str] = None
    note: Optional[str] = None


class UserProfileRequest(BaseModel):
    portfolio: str = ""
    risk_preference: str = ""
    investment_habits: str = ""
    user_persona: str = ""
    custom_notes: str = ""


class SubagentRequest(BaseModel):
    name: str
    description: str
    system_prompt: str
    tools: list = []
    model: Optional[str] = None
    enabled: bool = True


class SubagentUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    tools: Optional[list] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None


class BatchRunRequest(BaseModel):
    filename: str
    query_col: str = "B"
    start_row: int = 2
    end_row: int = 100
    content_col: str = "F"
    tool_col: str = "G"
    model: str
    prompt_version_id: Optional[str] = None
    sheet_name: Optional[str] = None
