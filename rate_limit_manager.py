import asyncio
import logging
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

from models import Task, TaskState, AlertLevel
from database import db
from config.config import config
from utils import create_alert, parse_claude_error


logger = logging.getLogger(__name__)


class RateLimitType(Enum):
    SESSION_LIMIT = "session_limit"  # 5-hour limit
    REQUEST_RATE = "request_rate"    # Too many requests
    QUOTA_EXCEEDED = "quota_exceeded"  # Daily/monthly quota
    UNKNOWN = "unknown"


@dataclass
class RateLimitInfo:
    limit_type: RateLimitType
    retry_after_seconds: int
    detected_at: datetime
    raw_message: str
    confidence: float  # 0.0 to 1.0


class ClaudeProber:
    """Test Claude availability without consuming quota"""
    
    def __init__(self):
        self.last_probe_time = None
        self.consecutive_failures = 0
        self.max_probe_frequency = 300  # 5 minutes minimum between probes
    
    async def probe_availability(self) -> tuple[bool, Optional[RateLimitInfo]]:
        """
        Probe Claude service to check if rate limits have been lifted
        Returns: (is_available, rate_limit_info_if_still_limited)
        """
        current_time = datetime.utcnow()
        
        # Rate limit our own probing
        if (self.last_probe_time and 
            (current_time - self.last_probe_time).total_seconds() < self.max_probe_frequency):
            logger.debug("Skipping probe due to frequency limit")
            return False, None
        
        self.last_probe_time = current_time
        
        try:
            # Use a minimal Claude command to test availability
            probe_command = [
                "claude", "code", "--no-interactive", "--query", 
                "echo 'probe test' | head -1"
            ]
            
            proc = await asyncio.create_subprocess_exec(
                *probe_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                timeout=30
            )
            
            stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout_data.decode('utf-8', errors='ignore')
            
            # Check exit code
            if proc.returncode == 0:
                self.consecutive_failures = 0
                logger.info("Claude probe successful - service available")
                return True, None
            
            # Parse error output for rate limit information
            rate_limit_info = self._parse_probe_output(output)
            
            if rate_limit_info:
                self.consecutive_failures += 1
                logger.info(f"Claude probe detected rate limit: {rate_limit_info.limit_type}")
                return False, rate_limit_info
            else:
                # Unknown error
                self.consecutive_failures += 1
                logger.warning(f"Claude probe failed with unknown error (exit {proc.returncode}): {output[:200]}")
                return False, None
                
        except asyncio.TimeoutError:
            self.consecutive_failures += 1
            logger.warning("Claude probe timed out")
            return False, None
        except Exception as e:
            self.consecutive_failures += 1
            logger.error(f"Claude probe error: {e}")
            return False, None
    
    def _parse_probe_output(self, output: str) -> Optional[RateLimitInfo]:
        """Parse probe output for rate limit information"""
        error_info = parse_claude_error(output)
        
        if not (error_info['is_rate_limited'] or error_info['is_session_expired']):
            return None
        
        # Determine rate limit type
        limit_type = RateLimitType.UNKNOWN
        confidence = 0.5
        
        output_lower = output.lower()
        
        if '5-hour' in output_lower or 'session' in output_lower:
            limit_type = RateLimitType.SESSION_LIMIT
            confidence = 0.9
        elif 'quota' in output_lower:
            limit_type = RateLimitType.QUOTA_EXCEEDED
            confidence = 0.8
        elif 'rate limit' in output_lower or 'too many requests' in output_lower:
            limit_type = RateLimitType.REQUEST_RATE
            confidence = 0.8
        
        # Extract retry-after time
        retry_after = error_info.get('retry_after', config.default_unban_wait)
        
        return RateLimitInfo(
            limit_type=limit_type,
            retry_after_seconds=retry_after,
            detected_at=datetime.utcnow(),
            raw_message=output[:500],  # Keep first 500 chars
            confidence=confidence
        )
    
    def should_increase_probe_frequency(self) -> bool:
        """Determine if we should probe more frequently"""
        # If we've had many consecutive failures, back off
        return self.consecutive_failures < 3


class WaitingUnbanManager:
    """Manage tasks in waiting_unban state"""
    
    def __init__(self):
        self.prober = ClaudeProber()
        self.running = False
        self.rate_limit_history: Dict[str, RateLimitInfo] = {}
        self.global_unban_time: Optional[datetime] = None
        
    async def start(self):
        """Start the waiting_unban manager"""
        self.running = True
        logger.info("Waiting unban manager started")
        
        # Start monitoring tasks
        await asyncio.gather(
            self._monitor_waiting_tasks(),
            self._probe_service_availability(),
            self._manage_global_rate_limits()
        )
    
    async def stop(self):
        """Stop the waiting_unban manager"""
        self.running = False
        logger.info("Waiting unban manager stopped")
    
    async def _monitor_waiting_tasks(self):
        """Monitor tasks in waiting_unban state"""
        while self.running:
            try:
                waiting_tasks = db.get_tasks_by_state([TaskState.WAITING_UNBAN.value])
                
                if not waiting_tasks:
                    await asyncio.sleep(30)
                    continue
                
                logger.info(f"Monitoring {len(waiting_tasks)} tasks in waiting_unban state")
                
                for task in waiting_tasks:
                    # Check if individual task wait time has elapsed
                    if (task.next_allowed_at and 
                        datetime.utcnow() >= task.next_allowed_at):
                        
                        # Check global rate limit status
                        if await self._is_globally_unbanned():
                            await self._attempt_task_recovery(task)
                        else:
                            logger.debug(f"Task {task.id} ready but global rate limit still active")
                
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"Error monitoring waiting tasks: {e}")
                await asyncio.sleep(30)
    
    async def _probe_service_availability(self):
        """Periodically probe service availability"""
        while self.running:
            try:
                if self._should_probe():
                    is_available, rate_limit_info = await self.prober.probe_availability()
                    
                    if is_available:
                        logger.info("Claude service is available")
                        self.global_unban_time = None
                        await self._notify_service_recovery()
                    elif rate_limit_info:
                        await self._update_global_rate_limit(rate_limit_info)
                
                # Dynamic probe frequency based on current state
                wait_time = self._calculate_probe_wait_time()
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                logger.error(f"Error probing service: {e}")
                await asyncio.sleep(300)  # 5 minutes on error
    
    def _should_probe(self) -> bool:
        """Determine if we should probe the service"""
        # Only probe if we have waiting tasks or suspect rate limits
        waiting_tasks = db.get_tasks_by_state([TaskState.WAITING_UNBAN.value])
        return len(waiting_tasks) > 0 or self.global_unban_time is not None
    
    def _calculate_probe_wait_time(self) -> int:
        """Calculate how long to wait between probes"""
        base_wait = 300  # 5 minutes base
        
        # If we have recent rate limit info, use it to calculate wait time
        if self.global_unban_time:
            remaining_time = (self.global_unban_time - datetime.utcnow()).total_seconds()
            if remaining_time > 0:
                # Probe more frequently as we approach the expected unban time
                if remaining_time < 600:  # 10 minutes
                    return min(60, remaining_time / 5)  # Probe every minute near unban time
                elif remaining_time < 1800:  # 30 minutes
                    return min(300, remaining_time / 6)
        
        # Adjust based on probe success rate
        if self.prober.consecutive_failures > 3:
            base_wait *= 2  # Back off if we're failing frequently
        
        return base_wait
    
    async def _is_globally_unbanned(self) -> bool:
        """Check if global rate limits have been lifted"""
        if not self.global_unban_time:
            return True
        
        return datetime.utcnow() >= self.global_unban_time
    
    async def _update_global_rate_limit(self, rate_limit_info: RateLimitInfo):
        """Update global rate limit information"""
        # Calculate when the rate limit should be lifted
        unban_time = rate_limit_info.detected_at + timedelta(seconds=rate_limit_info.retry_after_seconds)
        
        # Use the later of current global unban time or new unban time
        if not self.global_unban_time or unban_time > self.global_unban_time:
            self.global_unban_time = unban_time
            
            logger.warning(
                f"Global rate limit updated: {rate_limit_info.limit_type} "
                f"until {unban_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # Create alert
            create_alert(
                level=AlertLevel.P2,
                title="Claude service rate limited",
                message=(
                    f"Rate limit detected: {rate_limit_info.limit_type.value}. "
                    f"Expected recovery: {unban_time.strftime('%H:%M:%S')}"
                ),
                metadata={
                    "limit_type": rate_limit_info.limit_type.value,
                    "retry_after_seconds": rate_limit_info.retry_after_seconds,
                    "confidence": rate_limit_info.confidence,
                    "raw_message": rate_limit_info.raw_message
                }
            )
    
    async def _attempt_task_recovery(self, task: Task):
        """Attempt to recover a task from waiting_unban state"""
        try:
            logger.info(f"Attempting to recover task {task.id} from waiting_unban")
            
            # Move task back to pending
            task.task_state = TaskState.PENDING
            task.next_allowed_at = None
            
            # Update in database and task manager
            from task_manager import TaskManager
            task_manager = TaskManager()
            task_manager.update_task_state(
                task,
                TaskState.PENDING,
                "Recovered from rate limit wait"
            )
            
            logger.info(f"Task {task.id} recovered and moved to pending queue")
            
            # Create recovery notification
            create_alert(
                level=AlertLevel.P3,
                title=f"Task {task.id} recovered",
                message=f"Task '{task.name}' recovered from rate limit and ready for processing",
                task_id=task.id
            )
            
        except Exception as e:
            logger.error(f"Error recovering task {task.id}: {e}")
            
            # If recovery fails, extend the wait time
            task.next_allowed_at = datetime.utcnow() + timedelta(minutes=30)
            db.save_task(task)
    
    async def _notify_service_recovery(self):
        """Notify about service recovery"""
        waiting_tasks = db.get_tasks_by_state([TaskState.WAITING_UNBAN.value])
        
        if waiting_tasks:
            create_alert(
                level=AlertLevel.P3,
                title="Claude service recovered",
                message=f"Service is available again. {len(waiting_tasks)} tasks ready for recovery",
                metadata={"waiting_task_count": len(waiting_tasks)}
            )
    
    async def _manage_global_rate_limits(self):
        """Manage global rate limit state"""
        while self.running:
            try:
                # Check if global rate limit should be expired
                if (self.global_unban_time and 
                    datetime.utcnow() > self.global_unban_time + timedelta(minutes=5)):
                    
                    logger.info("Global rate limit expired, clearing state")
                    self.global_unban_time = None
                
                # Clean up old rate limit history
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                self.rate_limit_history = {
                    k: v for k, v in self.rate_limit_history.items()
                    if v.detected_at > cutoff_time
                }
                
                await asyncio.sleep(600)  # Check every 10 minutes
                
            except Exception as e:
                logger.error(f"Error managing global rate limits: {e}")
                await asyncio.sleep(600)
    
    def record_rate_limit(self, task_id: str, output: str) -> Optional[RateLimitInfo]:
        """Record rate limit encountered during task execution"""
        rate_limit_info = self._parse_rate_limit_from_output(output)
        
        if rate_limit_info:
            self.rate_limit_history[task_id] = rate_limit_info
            
            # Update global rate limit if this is more restrictive
            asyncio.create_task(self._update_global_rate_limit(rate_limit_info))
        
        return rate_limit_info
    
    def _parse_rate_limit_from_output(self, output: str) -> Optional[RateLimitInfo]:
        """Parse rate limit information from task output"""
        error_info = parse_claude_error(output)
        
        if not (error_info['is_rate_limited'] or error_info['is_session_expired']):
            return None
        
        # Similar to probe parsing but with different confidence levels
        limit_type = RateLimitType.UNKNOWN
        confidence = 0.7  # Higher confidence since this is from actual usage
        
        output_lower = output.lower()
        
        if '5-hour' in output_lower or 'session limit' in output_lower:
            limit_type = RateLimitType.SESSION_LIMIT
            confidence = 0.95
        elif 'quota' in output_lower:
            limit_type = RateLimitType.QUOTA_EXCEEDED
            confidence = 0.9
        elif 'rate limit' in output_lower or 'too many requests' in output_lower:
            limit_type = RateLimitType.REQUEST_RATE
            confidence = 0.85
        
        retry_after = error_info.get('retry_after') or self._estimate_retry_after(limit_type)
        
        return RateLimitInfo(
            limit_type=limit_type,
            retry_after_seconds=retry_after,
            detected_at=datetime.utcnow(),
            raw_message=output[:500],
            confidence=confidence
        )
    
    def _estimate_retry_after(self, limit_type: RateLimitType) -> int:
        """Estimate retry-after time based on limit type"""
        if limit_type == RateLimitType.SESSION_LIMIT:
            return 18000  # 5 hours
        elif limit_type == RateLimitType.QUOTA_EXCEEDED:
            return 86400  # 24 hours (daily quota)
        elif limit_type == RateLimitType.REQUEST_RATE:
            return 3600   # 1 hour
        else:
            return config.default_unban_wait
    
    def get_estimated_recovery_time(self, task_id: str) -> Optional[datetime]:
        """Get estimated recovery time for a task"""
        if task_id in self.rate_limit_history:
            rate_limit_info = self.rate_limit_history[task_id]
            return rate_limit_info.detected_at + timedelta(seconds=rate_limit_info.retry_after_seconds)
        
        if self.global_unban_time:
            return self.global_unban_time
        
        return None