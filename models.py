from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
import json


class TaskState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    WAITING_UNBAN = "waiting_unban"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


class ProcessState(str, Enum):
    SPAWNING = "spawning"
    RUNNING = "running"
    HUNG = "hung"
    TERMINATING = "terminating"
    KILLED = "killed"
    RESTARTING = "restarting"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskType(str, Enum):
    LIGHTWEIGHT = "lightweight"  # Simple tasks, can restart from beginning
    MEDIUM_CONTEXT = "medium_context"  # Need partial history for recovery
    HEAVY_CONTEXT = "heavy_context"  # Large files/data, need chunked processing


class AlertLevel(str, Enum):
    P1 = "P1"  # Business interruption
    P2 = "P2"  # Recoverable failure
    P3 = "P3"  # Minor issues


class Task(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    task_type: TaskType = TaskType.LIGHTWEIGHT
    priority: TaskPriority = TaskPriority.NORMAL
    
    # State management
    task_state: TaskState = TaskState.PENDING
    process_state: Optional[ProcessState] = None
    
    # Execution details
    command: str
    working_dir: Optional[str] = None
    environment: Dict[str, str] = Field(default_factory=dict)
    
    # Automation settings
    auto_execute: bool = False
    confirmation_strategy: str = "ask"  # ask, auto_yes, auto_no
    interaction_timeout: int = 300  # seconds to wait for confirmation
    
    # Recovery information
    retry_count: int = 0
    max_retries: int = 5
    resume_hint_file: Optional[str] = None
    checkpoint_data: Dict[str, Any] = Field(default_factory=dict)
    
    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    next_allowed_at: Optional[datetime] = None
    
    # Metadata
    tags: List[str] = Field(default_factory=list)
    assigned_worker: Optional[str] = None
    
    # Idempotency
    idempotency_keys: List[str] = Field(default_factory=list)
    
    # Error tracking
    last_error: Optional[str] = None
    error_history: List[Dict[str, Any]] = Field(default_factory=list)

    def to_json_file(self, file_path: str):
        """Save task to JSON file"""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.model_dump(mode='json'), f, indent=2, default=str, ensure_ascii=False)
    
    @classmethod
    def from_json_file(cls, file_path: str) -> 'Task':
        """Load task from JSON file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(**data)

    def add_error(self, error_msg: str, error_type: str = "general"):
        """Add error to history"""
        self.last_error = error_msg
        self.error_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "type": error_type,
            "message": error_msg
        })

    def can_retry(self) -> bool:
        """Check if task can be retried"""
        return self.retry_count < self.max_retries and self.task_state not in [
            TaskState.COMPLETED, TaskState.NEEDS_HUMAN_REVIEW
        ]
    
    def should_auto_confirm(self) -> bool:
        """Check if task should automatically confirm prompts"""
        return self.auto_execute or self.confirmation_strategy == "auto_yes"
    
    def get_confirmation_response(self, prompt: str = "") -> Optional[str]:
        """Get appropriate response for confirmation prompt"""
        if self.confirmation_strategy == "auto_yes":
            return "yes"
        elif self.confirmation_strategy == "auto_no":
            return "no"
        return None  # requires user input


class WorkerStatus(BaseModel):
    worker_id: str
    process_id: Optional[int] = None
    state: ProcessState = ProcessState.SPAWNING
    current_task_id: Optional[str] = None
    last_heartbeat: datetime = Field(default_factory=datetime.utcnow)
    cpu_usage: Optional[float] = None
    memory_usage: Optional[int] = None
    uptime_seconds: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0


class Alert(BaseModel):
    id: str
    level: AlertLevel
    title: str
    message: str
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SystemMetrics(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    disk_free_gb: float
    memory_usage_percent: float
    cpu_usage_percent: float
    active_workers: int
    pending_tasks: int
    processing_tasks: int
    failed_tasks: int
    completed_tasks: int