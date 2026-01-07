import os
import asyncio
import json
import shutil
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from uuid import uuid4

from models import Task, TaskState, ProcessState, TaskType, TaskPriority, AlertLevel
from database import db
from config.config import config
from utils import create_alert, get_system_metrics


logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self):
        self.running = False
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Ensure all required directories exist"""
        for dir_path in [config.tasks_dir, config.queue_dir / "pending", 
                        config.queue_dir / "processing", config.snapshots_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
    
    async def start(self):
        """Start task manager"""
        self.running = True
        logger.info("Task manager started")
        
        # Start background tasks
        await asyncio.gather(
            self._monitor_system_health(),
            self._cleanup_old_data(),
            self._process_waiting_tasks(),
        )
    
    async def stop(self):
        """Stop task manager"""
        self.running = False
        logger.info("Task manager stopped")
    
    def create_task(self, 
                   name: str,
                   command: str,
                   description: str = None,
                   task_type: TaskType = TaskType.LIGHTWEIGHT,
                   priority: TaskPriority = TaskPriority.NORMAL,
                   working_dir: str = None,
                   environment: Dict[str, str] = None,
                   tags: List[str] = None) -> Task:
        """Create a new task"""
        
        task = Task(
            id=f"task_{uuid4().hex[:8]}",
            name=name,
            description=description,
            command=command,
            task_type=task_type,
            priority=priority,
            working_dir=working_dir,
            environment=environment or {},
            tags=tags or [],
            auto_execute=True,
            confirmation_strategy="auto_yes"
        )
        
        # Create task directory
        task_dir = config.tasks_dir / task.id
        task_dir.mkdir(exist_ok=True)
        
        # Save task metadata
        task.to_json_file(str(task_dir / "task.json"))
        
        # Save to database
        db.save_task(task)
        
        # Add to pending queue
        self._add_to_queue(task, "pending")
        
        logger.info(f"Created task {task.id}: {task.name}")
        return task
    
    def _add_to_queue(self, task: Task, queue_type: str):
        """Add task to file-based queue"""
        queue_file = config.queue_dir / queue_type / f"{task.id}.json"
        task.to_json_file(str(queue_file))
    
    def _remove_from_queue(self, task_id: str, queue_type: str):
        """Remove task from file-based queue"""
        queue_file = config.queue_dir / queue_type / f"{task_id}.json"
        if queue_file.exists():
            queue_file.unlink()
    
    def get_next_pending_task(self) -> Optional[Task]:
        """Get next pending task using atomic file operations with priority sorting"""
        pending_dir = config.queue_dir / "pending"
        
        # Get all pending task files and sort by priority then creation time
        task_files = list(pending_dir.glob("*.json"))
        
        # Define priority order (higher priority values = earlier processing)
        priority_order = {
            TaskPriority.URGENT: 0,
            TaskPriority.HIGH: 1,
            TaskPriority.NORMAL: 2,
            TaskPriority.LOW: 3
        }
        
        # Sort by priority first, then by creation time
        def sort_key(task_file):
            try:
                # Load task to get priority
                with open(task_file, 'r', encoding='utf-8') as f:
                    task_data = json.load(f)
                priority = TaskPriority(task_data.get('priority', TaskPriority.NORMAL))
                return (priority_order.get(priority, 2), os.path.getctime(task_file))
            except Exception:
                # Fallback to normal priority if task cannot be read
                return (2, os.path.getctime(task_file))
        
        task_files = sorted(task_files, key=sort_key)
        
        for task_file in task_files:
            try:
                # Try to atomically move file to processing queue
                processing_file = config.queue_dir / "processing" / task_file.name
                task_file.rename(processing_file)
                
                # Load and return task
                task = Task.from_json_file(str(processing_file))
                
                # Check if task is ready to run
                if task.next_allowed_at and task.next_allowed_at > datetime.utcnow():
                    # Move back to pending if not ready
                    processing_file.rename(task_file)
                    continue
                
                return task
                
            except (OSError, FileNotFoundError):
                # Another process got it first, continue to next
                continue
        
        return None
    
    def update_task_state(self, task: Task, new_state: TaskState, 
                         error_msg: str = None, save_snapshot: bool = False):
        """Update task state with proper transitions"""
        old_state = task.task_state
        task.task_state = new_state
        
        # Handle state-specific logic
        if new_state == TaskState.PROCESSING:
            task.started_at = datetime.utcnow()
            task.assigned_worker = os.getenv('WORKER_ID', 'unknown')
            
        elif new_state == TaskState.WAITING_UNBAN:
            # Calculate next allowed time based on retry count
            wait_time = min(
                config.default_unban_wait * (config.rate_limit_backoff_multiplier ** task.retry_count),
                config.max_delay
            )
            task.next_allowed_at = datetime.utcnow() + timedelta(seconds=wait_time)
            
        elif new_state in [TaskState.COMPLETED, TaskState.FAILED]:
            task.completed_at = datetime.utcnow()
            
        elif new_state == TaskState.RETRYING:
            # Increment retry counter and either fail or schedule retry with backoff
            task.retry_count += 1
            if task.retry_count >= task.max_retries:
                new_state = TaskState.FAILED
                task.task_state = new_state
            else:
                # Schedule retry with exponential backoff and keep RETRYING state
                delay = min(
                    config.base_delay * (config.exponential_base ** (task.retry_count - 1)),
                    config.max_delay,
                )
                task.next_allowed_at = datetime.utcnow() + timedelta(seconds=delay)
                
        elif new_state == TaskState.AWAITING_CONFIRMATION:
            # For confirmation awaiting tasks, try to auto-retry with confirmation enabled
            if not task.auto_execute and task.retry_count < task.max_retries:
                logger.info(f"Task {task.id} awaiting confirmation, attempting auto-retry with confirmation enabled")
                self._setup_auto_confirmation_retry(task)
            else:
                logger.info(f"Task {task.id} requires manual intervention - max retries reached or already auto-execute enabled")
        
        # Add error if provided
        if error_msg:
            task.add_error(error_msg, error_type=new_state.value)
        
        # Save snapshot if requested
        if save_snapshot:
            self._save_task_snapshot(task)
        
        # Update task.json file to keep it in sync first
        task_dir = config.tasks_dir / task.id
        if task_dir.exists():
            task.to_json_file(str(task_dir / "task.json"))
        
        # Update database
        db.save_task(task)
        
        # Update queue files
        if new_state == TaskState.PENDING:
            # Move back to pending queue
            self._remove_from_queue(task.id, "processing")
            # Clear worker assignment when re-queued
            task.assigned_worker = None
            self._add_to_queue(task, "pending")
        elif new_state == TaskState.RETRYING:
            # Re-enqueue retrying tasks into pending queue with backoff
            self._remove_from_queue(task.id, "processing")
            task.assigned_worker = None
            self._add_to_queue(task, "pending")
        elif old_state == TaskState.PENDING and new_state == TaskState.PROCESSING:
            # Already moved in get_next_pending_task
            pass
        elif new_state in [TaskState.COMPLETED, TaskState.FAILED]:
            self._remove_from_queue(task.id, "processing")
        
        logger.info(f"Task {task.id} state changed: {old_state} -> {new_state}")
        
        # Create alert for significant state changes
        if new_state == TaskState.FAILED:
            create_alert(
                level=AlertLevel.P2,
                title=f"Task {task.id} failed",
                message=f"Task '{task.name}' failed after {task.retry_count} retries: {error_msg}",
                task_id=task.id
            )
        elif new_state == TaskState.WAITING_UNBAN:
            create_alert(
                level=AlertLevel.P3,
                title=f"Task {task.id} hit rate limit",
                message=f"Task '{task.name}' hit rate limit, waiting until {task.next_allowed_at}",
                task_id=task.id
            )
    
    def _save_task_snapshot(self, task: Task):
        """Save task recovery snapshot"""
        snapshot_data = {
            'task_state': task.task_state.value,
            'process_state': task.process_state.value if task.process_state else None,
            'retry_count': task.retry_count,
            'checkpoint_data': task.checkpoint_data,
            'last_error': task.last_error,
            'created_at': datetime.utcnow().isoformat()
        }
        
        # Save to snapshots directory
        snapshot_file = config.snapshots_dir / f"{task.id}_snapshot.json"
        with open(snapshot_file, 'w') as f:
            json.dump(snapshot_data, f, indent=2)
        
        # Save to database
        db.save_recovery_snapshot(
            task.id, 
            "latest", 
            json.dumps(snapshot_data).encode('utf-8')
        )
    
    def load_task_snapshot(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Load task recovery snapshot"""
        # Try database first
        snapshot_data = db.get_recovery_snapshot(task_id, "latest")
        if snapshot_data:
            return json.loads(snapshot_data.decode('utf-8'))
        
        # Fallback to file system
        snapshot_file = config.snapshots_dir / f"{task_id}_snapshot.json"
        if snapshot_file.exists():
            with open(snapshot_file, 'r') as f:
                return json.load(f)
        
        return None
    
    def generate_resume_context(self, task: Task) -> str:
        """Generate context for resuming interrupted tasks"""
        task_dir = config.tasks_dir / task.id
        
        context_parts = [
            "=== TASK RESUME CONTEXT ===",
            f"Task: {task.name}",
            f"Retry Count: {task.retry_count}",
        ]
        
        # Handle interaction-specific resume context
        if task.checkpoint_data.get('needs_interaction'):
            interaction_prompt = task.checkpoint_data.get('interaction_prompt', '')
            auto_response = task.checkpoint_data.get('auto_response')
            context_parts.extend([
                f"Previous interaction detected: {interaction_prompt}",
                f"Auto-responding with: {'yes' if task.should_auto_confirm() else 'continue'}",
            ])
            if auto_response:
                context_parts.append(f"Auto-response content: {auto_response}")
            context_parts.extend([
                "Please continue with the task after this response.",
            ])
        
        # Add session management info
        if task.checkpoint_data.get('session_id'):
            context_parts.append(f"Session ID: {task.checkpoint_data['session_id']}")
        
        # Legacy checkpoint data (for backward compatibility)
        if task.checkpoint_data and not task.checkpoint_data.get('needs_interaction'):
            context_parts.extend([
                "",
                "=== CHECKPOINT DATA ===",
                json.dumps({k: v for k, v in task.checkpoint_data.items() 
                           if k not in ['session_id', 'needs_interaction', 'interaction_prompt']}, indent=2),
                ""
            ])
        
        # Add resume patch if available (legacy)
        resume_patch_file = task_dir / "resume_patch.txt"
        if resume_patch_file.exists():
            context_parts.extend([
                "=== PREVIOUS OUTPUT (Last 500 lines) ===",
                resume_patch_file.read_text(encoding='utf-8')[-50000:],  # Last ~50KB
                "=== END PREVIOUS OUTPUT ===",
                ""
            ])
        
        # Final instructions and reminder about completion marker
        context_parts.extend([
            "Continue from where we left off.",
            "",
            "=== COMPLETION REMINDER ===",
            "Do not repeat actions that already succeeded.",
            "When the task is fully complete, end your final response with the exact line: ✅ TASK_COMPLETED",
            "Place the marker on its own line as the last content and do not add text after it."
        ])

        return "\n".join(context_parts)
    
    async def _monitor_system_health(self):
        """Monitor system health and resources"""
        while self.running:
            try:
                metrics = get_system_metrics()
                
                # Check disk space
                if metrics.disk_free_gb < config.min_disk_space_gb:
                    create_alert(
                        level=AlertLevel.P1,
                        title="Low disk space",
                        message=f"Disk space below {config.min_disk_space_gb}GB: {metrics.disk_free_gb:.1f}GB remaining"
                    )
                
                # Check for stuck tasks
                stuck_tasks = db.get_tasks_by_state([TaskState.PROCESSING.value])
                for task in stuck_tasks:
                    if (task.started_at and 
                        datetime.utcnow() - task.started_at > timedelta(hours=6)):
                        
                        create_alert(
                            level=AlertLevel.P2,
                            title=f"Task {task.id} appears stuck",
                            message=f"Task '{task.name}' has been processing for over 6 hours",
                            task_id=task.id
                        )
                
                await asyncio.sleep(config.health_check_interval)
                
            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                await asyncio.sleep(config.health_check_interval)
    
    async def _process_waiting_tasks(self):
        """Process tasks in waiting_unban state"""
        while self.running:
            try:
                waiting_tasks = db.get_tasks_by_state([TaskState.WAITING_UNBAN.value])
                
                for task in waiting_tasks:
                    if (task.next_allowed_at and 
                        datetime.utcnow() >= task.next_allowed_at):
                        
                        # Move back to pending queue
                        self.update_task_state(task, TaskState.PENDING)
                        logger.info(f"Task {task.id} moved from waiting_unban to pending")
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"Waiting tasks processing error: {e}")
                await asyncio.sleep(30)
    
    async def _cleanup_old_data(self):
        """Cleanup old data periodically"""
        while self.running:
            try:
                # Cleanup database
                db.cleanup_old_data(days=config.max_log_files)
                
                # Cleanup old task directories
                cutoff_date = datetime.utcnow() - timedelta(days=config.max_log_files)
                
                for task_dir in config.tasks_dir.iterdir():
                    if task_dir.is_dir() and task_dir.stat().st_mtime < cutoff_date.timestamp():
                        # Check if task is completed/failed
                        task_file = task_dir / "task.json"
                        if task_file.exists():
                            task = Task.from_json_file(str(task_file))
                            if task.task_state in [TaskState.COMPLETED, TaskState.FAILED]:
                                shutil.rmtree(task_dir)
                                logger.info(f"Cleaned up old task directory: {task_dir}")
                
                # Sleep for 1 hour between cleanups
                await asyncio.sleep(3600)
                
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                await asyncio.sleep(3600)
    
    def _setup_auto_confirmation_retry(self, task: Task):
        """Setup task for auto-confirmation retry"""
        from command_generator import command_generator
        
        # Enable auto-execution for this retry
        task.auto_execute = True
        task.confirmation_strategy = "auto_yes"
        
        # Regenerate command with auto-execution suffix
        if hasattr(task, 'name') and hasattr(task, 'description'):
            new_command = command_generator.generate_command(
                name=task.name,
                description=task.description,
                task_type=task.task_type,
                working_dir=task.working_dir,
                auto_execute=True
            )
            task.command = new_command
        
        # Reset state to pending for retry
        task.task_state = TaskState.PENDING
        task.retry_count += 1
        
        # Add to retry queue with a short delay
        task.next_allowed_at = datetime.utcnow() + timedelta(seconds=30)
        
        logger.info(f"Task {task.id} setup for auto-confirmation retry (attempt {task.retry_count})")
