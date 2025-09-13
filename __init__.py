"""
Auto-Claude: Automated Task Execution System for Claude Code

A comprehensive system for unattended execution of Claude Code tasks with:
- Graceful recovery from interruptions
- Rate limit handling with automatic pause/resume
- Security scanning and compliance checking
- Comprehensive monitoring and alerting
- Multi-worker support with load balancing
"""

__version__ = "1.0.0"
__author__ = "Auto-Claude System"

from .models import Task, TaskState, TaskType, TaskPriority
from .task_manager import TaskManager
from .worker import ClaudeWorker
from .config.config import config
from .database import db
from .security import security_manager
from .monitoring import MonitoringService

__all__ = [
    'Task',
    'TaskState', 
    'TaskType',
    'TaskPriority',
    'TaskManager',
    'ClaudeWorker',
    'config',
    'db',
    'security_manager',
    'MonitoringService'
]