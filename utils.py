import os
import re
import psutil
import logging
from typing import Dict, Any, List
from datetime import datetime
from uuid import uuid4

from models import Alert, AlertLevel, SystemMetrics
from database import db
from config.config import config


logger = logging.getLogger(__name__)


def create_alert(level: AlertLevel, title: str, message: str, 
                task_id: str = None, worker_id: str = None, 
                metadata: Dict[str, Any] = None) -> Alert:
    """Create and save an alert"""
    alert = Alert(
        id=f"alert_{uuid4().hex[:8]}",
        level=level,
        title=title,
        message=message,
        task_id=task_id,
        worker_id=worker_id,
        metadata=metadata or {}
    )
    
    db.save_alert(alert)
    logger.warning(f"Alert created [{level}]: {title} - {message}")
    
    return alert


def get_system_metrics() -> SystemMetrics:
    """Get current system metrics"""
    # Disk usage
    disk_usage = psutil.disk_usage(str(config.base_dir))
    disk_free_gb = disk_usage.free / (1024**3)
    
    # Memory usage
    memory = psutil.virtual_memory()
    memory_usage_percent = memory.percent
    
    # CPU usage
    cpu_usage_percent = psutil.cpu_percent(interval=1)
    
    # Task counts from database
    pending_tasks = len(db.get_tasks_by_state(['pending']))
    processing_tasks = len(db.get_tasks_by_state(['processing']))
    failed_tasks = len(db.get_tasks_by_state(['failed']))
    completed_tasks = len(db.get_tasks_by_state(['completed']))
    
    # Active workers
    active_workers = len(db.get_active_workers())
    
    return SystemMetrics(
        disk_free_gb=disk_free_gb,
        memory_usage_percent=memory_usage_percent,
        cpu_usage_percent=cpu_usage_percent,
        active_workers=active_workers,
        pending_tasks=pending_tasks,
        processing_tasks=processing_tasks,
        failed_tasks=failed_tasks,
        completed_tasks=completed_tasks
    )


def sanitize_output(text: str) -> str:
    """Remove sensitive information from text"""
    sanitized = text
    
    for pattern in config.sensitive_patterns:
        # Replace with masked version keeping last 4 characters
        def mask_match(match):
            value = match.group()
            if len(value) > 4:
                return '***' + value[-4:]
            return '***'
        
        sanitized = re.sub(pattern, mask_match, sanitized)
    
    return sanitized




def parse_claude_error(output: str) -> Dict[str, Any]:
    """Parse Claude CLI output for errors and rate limits"""
    result = {
        'is_rate_limited': False,
        'is_session_expired': False,
        'is_hung': False,
        'retry_after': None,
        'error_type': None,
        'error_message': None
    }
    
    output_lower = output.lower()
    
    # Rate limit patterns
    rate_limit_patterns = [
        r'rate limit.*?exceeded',
        r'quota.*?exceeded', 
        r'too many requests',
        r'5-hour limit.*?reached',
        r'usage limit.*?reached'
    ]
    
    for pattern in rate_limit_patterns:
        if re.search(pattern, output_lower):
            result['is_rate_limited'] = True
            result['error_type'] = 'rate_limit'
            
            # Try to extract retry after time
            retry_match = re.search(r'retry.*?after.*?(\d+).*?(second|minute|hour)', output_lower)
            if retry_match:
                value = int(retry_match.group(1))
                unit = retry_match.group(2)
                if unit.startswith('minute'):
                    value *= 60
                elif unit.startswith('hour'):
                    value *= 3600
                result['retry_after'] = value
            
            break
    
    # Session expired patterns  
    session_patterns = [
        r'session.*?expired',
        r'authentication.*?failed',
        r'login.*?required',
        r'unauthorized'
    ]
    
    for pattern in session_patterns:
        if re.search(pattern, output_lower):
            result['is_session_expired'] = True
            result['error_type'] = 'session_expired'
            break
    
    # Extract general error message
    error_patterns = [
        r'error:\s*(.+)',
        r'failed:\s*(.+)',
        r'exception:\s*(.+)'
    ]
    
    for pattern in error_patterns:
        match = re.search(pattern, output_lower)
        if match:
            result['error_message'] = match.group(1).strip()
            if not result['error_type']:
                result['error_type'] = 'general'
            break
    
    return result


def estimate_context_size(text: str) -> int:
    """Estimate token count for context size management"""
    # Rough approximation: 1 token ≈ 4 characters
    return len(text) // 4


def truncate_for_context(text: str, max_tokens: int = 8000) -> str:
    """Truncate text to fit within token limits"""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    
    # Keep the end of the text (most recent context)
    return "... [truncated] ...\n" + text[-max_chars:]


def setup_logging(log_level: str = "INFO", log_file: str = None):
    """Setup structured logging"""
    import structlog
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="ISO"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer() if not log_file else structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    # Configure standard logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        filename=log_file,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def get_resume_patch_size(task_id: str) -> int:
    """Get size of resume patch file"""
    patch_file = config.tasks_dir / task_id / "resume_patch.txt"
    if patch_file.exists():
        return patch_file.stat().st_size
    return 0


def atomic_write(file_path: str, content: str, encoding: str = 'utf-8'):
    """Write file atomically using temporary file"""
    temp_path = f"{file_path}.tmp"
    try:
        with open(temp_path, 'w', encoding=encoding) as f:
            f.write(content)
        os.rename(temp_path, file_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def format_duration(seconds: int) -> str:
    """Format duration in human readable format"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m {seconds%60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


def is_process_alive(pid: int) -> bool:
    """Check if process is still alive"""
    try:
        return psutil.pid_exists(pid)
    except:
        return False