from typing import Any, Dict, Optional
from pydantic import StringConstraints
from pydantic import BaseModel

class WebRTCImageResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: float
    session_id: str