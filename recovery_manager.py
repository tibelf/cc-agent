import os
import asyncio
import logging
import psutil
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass

from models import Task, TaskState, ProcessState, WorkerStatus, AlertLevel
from database import db
from config.config import config
from utils import create_alert, get_system_metrics, is_process_alive


logger = logging.getLogger(__name__)


@dataclass
class RecoveryAction:
    action_type: str
    priority: int
    description: str
    parameters: Dict[str, Any]


class SystemHealthMonitor:
    """Monitor system health and trigger recovery actions"""
    
    def __init__(self):
        self.alerts_sent = {}  # Track sent alerts to avoid spam
        self.last_cleanup = datetime.utcnow()
    
    async def check_system_health(self) -> List[RecoveryAction]:
        """Check system health and return recovery actions"""
        actions = []
        
        try:
            metrics = get_system_metrics()
            
            # Check disk space
            if metrics.disk_free_gb < config.min_disk_space_gb:
                actions.append(RecoveryAction(
                    action_type="cleanup_disk_space",
                    priority=1,  # High priority
                    description=f"Low disk space: {metrics.disk_free_gb:.1f}GB remaining",
                    parameters={"threshold_gb": config.min_disk_space_gb}
                ))
            
            # Check memory usage
            if metrics.memory_usage_percent > 90:
                actions.append(RecoveryAction(
                    action_type="manage_memory_pressure",
                    priority=2,
                    description=f"High memory usage: {metrics.memory_usage_percent:.1f}%",
                    parameters={"usage_percent": metrics.memory_usage_percent}
                ))
            
            # Check for stuck processes
            stuck_workers = await self._check_stuck_workers()
            for worker_id, process_id in stuck_workers:
                actions.append(RecoveryAction(
                    action_type="restart_stuck_worker",
                    priority=1,
                    description=f"Worker {worker_id} appears stuck",
                    parameters={"worker_id": worker_id, "process_id": process_id}
                ))
            
            # Check for orphaned tasks
            orphaned_tasks = await self._check_orphaned_tasks()
            for task in orphaned_tasks:
                actions.append(RecoveryAction(
                    action_type="recover_orphaned_task",
                    priority=2,
                    description=f"Task {task.id} appears orphaned",
                    parameters={"task_id": task.id}
                ))
            
            # Check network connectivity
            if not await self._check_network_connectivity():
                actions.append(RecoveryAction(
                    action_type="handle_network_failure",
                    priority=1,
                    description="Network connectivity issues detected",
                    parameters={}
                ))
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            actions.append(RecoveryAction(
                action_type="investigate_system_error",
                priority=1,
                description=f"System health check failed: {str(e)}",
                parameters={"error": str(e)}
            ))
        
        return actions
    
    async def _check_stuck_workers(self) -> List[tuple]:
        """Check for stuck worker processes"""
        stuck_workers = []
        
        try:
            active_workers = db.get_active_workers(max_age_seconds=300)  # 5 minutes
            
            for worker in active_workers:
                if not worker.process_id:
                    continue
                
                # Check if process is still alive
                if not is_process_alive(worker.process_id):
                    logger.warning(f"Worker {worker.worker_id} process {worker.process_id} is dead")
                    stuck_workers.append((worker.worker_id, worker.process_id))
                    continue
                
                # Check if worker has been silent for too long
                if worker.last_heartbeat:
                    silence_duration = datetime.utcnow() - worker.last_heartbeat
                    if silence_duration > timedelta(minutes=10):
                        logger.warning(f"Worker {worker.worker_id} silent for {silence_duration}")
                        stuck_workers.append((worker.worker_id, worker.process_id))
                
                # Check CPU usage patterns (stuck process might have 100% CPU)
                try:
                    process = psutil.Process(worker.process_id)
                    cpu_percent = process.cpu_percent(interval=1)
                    if cpu_percent > 95:  # High CPU usage might indicate stuck process
                        memory_info = process.memory_info()
                        if memory_info.rss > 1024 * 1024 * 1024:  # > 1GB memory
                            logger.warning(f"Worker {worker.worker_id} high resource usage")
                            stuck_workers.append((worker.worker_id, worker.process_id))
                except psutil.NoSuchProcess:
                    stuck_workers.append((worker.worker_id, worker.process_id))
                
        except Exception as e:
            logger.error(f"Error checking stuck workers: {e}")
        
        return stuck_workers
    
    async def _check_orphaned_tasks(self) -> List[Task]:
        """Check for tasks that have been processing too long"""
        orphaned_tasks = []
        
        try:
            processing_tasks = db.get_tasks_by_state([TaskState.PROCESSING.value])
            
            for task in processing_tasks:
                # Task processing for more than 6 hours without updates
                if (task.started_at and 
                    datetime.utcnow() - task.started_at > timedelta(hours=6)):
                    
                    # Check if assigned worker is still active
                    if task.assigned_worker:
                        active_workers = db.get_active_workers()
                        worker_ids = [w.worker_id for w in active_workers]
                        
                        if task.assigned_worker not in worker_ids:
                            orphaned_tasks.append(task)
                    else:
                        orphaned_tasks.append(task)
        
        except Exception as e:
            logger.error(f"Error checking orphaned tasks: {e}")
        
        return orphaned_tasks
    
    async def _check_network_connectivity(self) -> bool:
        """Check basic network connectivity"""
        try:
            # Simple connectivity test
            proc = await asyncio.create_subprocess_exec(
                'ping', '-c', '1', '8.8.8.8',
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except:
            return False


class RecoveryExecutor:
    """Execute recovery actions"""
    
    def __init__(self):
        self.active_recoveries = set()
    
    async def execute_action(self, action: RecoveryAction) -> bool:
        """Execute a recovery action"""
        action_key = f"{action.action_type}_{hash(str(action.parameters))}"
        
        if action_key in self.active_recoveries:
            logger.debug(f"Recovery action {action.action_type} already in progress")
            return False
        
        self.active_recoveries.add(action_key)
        
        try:
            success = await self._execute_action_impl(action)
            
            if success:
                logger.info(f"Recovery action completed: {action.description}")
            else:
                logger.error(f"Recovery action failed: {action.description}")
            
            return success
            
        except Exception as e:
            logger.error(f"Recovery action error: {action.description} - {e}")
            return False
        finally:
            self.active_recoveries.discard(action_key)
    
    async def _execute_action_impl(self, action: RecoveryAction) -> bool:
        """Implement specific recovery actions"""
        
        if action.action_type == "cleanup_disk_space":
            return await self._cleanup_disk_space(action.parameters)
        
        elif action.action_type == "manage_memory_pressure":
            return await self._manage_memory_pressure(action.parameters)
        
        elif action.action_type == "restart_stuck_worker":
            return await self._restart_stuck_worker(action.parameters)
        
        elif action.action_type == "recover_orphaned_task":
            return await self._recover_orphaned_task(action.parameters)
        
        elif action.action_type == "handle_network_failure":
            return await self._handle_network_failure(action.parameters)
        
        elif action.action_type == "investigate_system_error":
            return await self._investigate_system_error(action.parameters)
        
        else:
            logger.warning(f"Unknown recovery action: {action.action_type}")
            return False
    
    async def _cleanup_disk_space(self, params: Dict[str, Any]) -> bool:
        """Clean up disk space"""
        try:
            cleaned_mb = 0
            
            # Clean old logs
            logs_cleaned = await self._cleanup_old_logs()
            cleaned_mb += logs_cleaned
            
            # Clean old snapshots
            snapshots_cleaned = await self._cleanup_old_snapshots()
            cleaned_mb += snapshots_cleaned
            
            # Clean completed task directories
            tasks_cleaned = await self._cleanup_completed_tasks()
            cleaned_mb += tasks_cleaned
            
            # Clean temp files
            temp_cleaned = await self._cleanup_temp_files()
            cleaned_mb += temp_cleaned
            
            logger.info(f"Disk cleanup completed: {cleaned_mb:.1f}MB freed")
            
            # Check if we freed enough space
            metrics = get_system_metrics()
            if metrics.disk_free_gb >= params["threshold_gb"]:
                return True
            
            # If still low on space, create alert
            create_alert(
                level=AlertLevel.P1,
                title="Critical disk space",
                message=f"Disk space still low after cleanup: {metrics.disk_free_gb:.1f}GB remaining"
            )
            
            return False
            
        except Exception as e:
            logger.error(f"Disk cleanup error: {e}")
            return False
    
    async def _cleanup_old_logs(self) -> float:
        """Clean up old log files"""
        cleaned_mb = 0
        cutoff_date = datetime.utcnow() - timedelta(days=config.max_log_files)
        
        try:
            for log_file in config.logs_dir.rglob("*.log"):
                if log_file.stat().st_mtime < cutoff_date.timestamp():
                    size_mb = log_file.stat().st_size / (1024 * 1024)
                    log_file.unlink()
                    cleaned_mb += size_mb
        except Exception as e:
            logger.error(f"Log cleanup error: {e}")
        
        return cleaned_mb
    
    async def _cleanup_old_snapshots(self) -> float:
        """Clean up old snapshots"""
        cleaned_mb = 0
        cutoff_date = datetime.utcnow() - timedelta(days=config.max_log_files)
        
        try:
            for snapshot_file in config.snapshots_dir.rglob("*"):
                if snapshot_file.is_file() and snapshot_file.stat().st_mtime < cutoff_date.timestamp():
                    size_mb = snapshot_file.stat().st_size / (1024 * 1024)
                    snapshot_file.unlink()
                    cleaned_mb += size_mb
        except Exception as e:
            logger.error(f"Snapshot cleanup error: {e}")
        
        return cleaned_mb
    
    async def _cleanup_completed_tasks(self) -> float:
        """Clean up old completed task directories"""
        cleaned_mb = 0
        cutoff_date = datetime.utcnow() - timedelta(days=config.max_log_files)
        
        try:
            completed_tasks = db.get_tasks_by_state([TaskState.COMPLETED.value, TaskState.FAILED.value])
            
            for task in completed_tasks:
                if (task.completed_at and 
                    task.completed_at < cutoff_date.replace(tzinfo=None)):
                    
                    task_dir = config.tasks_dir / task.id
                    if task_dir.exists():
                        # Calculate directory size
                        size = sum(f.stat().st_size for f in task_dir.rglob('*') if f.is_file())
                        size_mb = size / (1024 * 1024)
                        
                        # Remove directory
                        shutil.rmtree(task_dir)
                        cleaned_mb += size_mb
        except Exception as e:
            logger.error(f"Task directory cleanup error: {e}")
        
        return cleaned_mb
    
    async def _cleanup_temp_files(self) -> float:
        """Clean up temporary files"""
        cleaned_mb = 0
        
        try:
            # Clean .tmp files
            for tmp_file in config.base_dir.rglob("*.tmp"):
                size_mb = tmp_file.stat().st_size / (1024 * 1024)
                tmp_file.unlink()
                cleaned_mb += size_mb
            
            # Clean empty directories
            for directory in config.base_dir.rglob("*"):
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
        except Exception as e:
            logger.error(f"Temp file cleanup error: {e}")
        
        return cleaned_mb
    
    async def _manage_memory_pressure(self, params: Dict[str, Any]) -> bool:
        """Handle high memory usage"""
        try:
            # Get memory-intensive workers
            active_workers = db.get_active_workers()
            memory_hogs = []
            
            for worker in active_workers:
                if worker.process_id and worker.memory_usage:
                    if worker.memory_usage > 500 * 1024 * 1024:  # > 500MB
                        memory_hogs.append(worker)
            
            # Sort by memory usage and restart the worst offenders
            memory_hogs.sort(key=lambda w: w.memory_usage or 0, reverse=True)
            
            restarted_count = 0
            for worker in memory_hogs[:2]:  # Restart top 2 memory hogs
                if await self._restart_worker(worker.worker_id, worker.process_id):
                    restarted_count += 1
            
            if restarted_count > 0:
                logger.info(f"Restarted {restarted_count} memory-intensive workers")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Memory pressure management error: {e}")
            return False
    
    async def _restart_stuck_worker(self, params: Dict[str, Any]) -> bool:
        """Restart a stuck worker"""
        worker_id = params["worker_id"]
        process_id = params.get("process_id")
        
        return await self._restart_worker(worker_id, process_id)
    
    async def _restart_worker(self, worker_id: str, process_id: int = None) -> bool:
        """Restart a specific worker"""
        try:
            # Kill the process if it exists
            if process_id and is_process_alive(process_id):
                try:
                    process = psutil.Process(process_id)
                    process.terminate()
                    
                    # Wait for graceful termination
                    await asyncio.sleep(5)
                    
                    if process.is_running():
                        process.kill()
                        
                    logger.info(f"Killed worker process {process_id}")
                except psutil.NoSuchProcess:
                    pass
            
            # The worker should restart automatically via supervisor
            # Create alert for tracking
            create_alert(
                level=AlertLevel.P2,
                title=f"Worker {worker_id} restarted",
                message=f"Restarted stuck worker {worker_id} (PID: {process_id})",
                worker_id=worker_id
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Worker restart error: {e}")
            return False
    
    async def _recover_orphaned_task(self, params: Dict[str, Any]) -> bool:
        """Recover an orphaned task"""
        task_id = params["task_id"]
        
        try:
            task = db.get_task(task_id)
            if not task:
                return False
            
            # Reset task to pending state for retry
            task.task_state = TaskState.PENDING
            task.assigned_worker = None
            task.process_state = None
            
            # Save recovery snapshot
            from task_manager import TaskManager
            task_manager = TaskManager()
            task_manager.update_task_state(
                task,
                TaskState.PENDING,
                "Recovered from orphaned state",
                save_snapshot=True
            )
            
            logger.info(f"Recovered orphaned task {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Task recovery error: {e}")
            return False
    
    async def _handle_network_failure(self, params: Dict[str, Any]) -> bool:
        """Handle network connectivity issues"""
        try:
            # Pause all processing tasks
            processing_tasks = db.get_tasks_by_state([TaskState.PROCESSING.value])
            
            for task in processing_tasks:
                task.task_state = TaskState.PAUSED
                task.add_error("Network connectivity issues detected")
                db.save_task(task)
            
            # Create alert
            create_alert(
                level=AlertLevel.P1,
                title="Network connectivity failure",
                message=f"Paused {len(processing_tasks)} tasks due to network issues"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Network failure handling error: {e}")
            return False
    
    async def _investigate_system_error(self, params: Dict[str, Any]) -> bool:
        """Investigate and log system errors"""
        error = params.get("error", "Unknown error")
        
        try:
            # Collect system information
            metrics = get_system_metrics()
            
            # Create diagnostic alert
            create_alert(
                level=AlertLevel.P1,
                title="System diagnostic required",
                message=f"System error detected: {error}",
                metadata={
                    "disk_free_gb": metrics.disk_free_gb,
                    "memory_usage_percent": metrics.memory_usage_percent,
                    "cpu_usage_percent": metrics.cpu_usage_percent,
                    "active_workers": metrics.active_workers
                }
            )
            
            return True
            
        except Exception as e:
            logger.error(f"System investigation error: {e}")
            return False


class AutoRecoveryManager:
    """Main recovery manager that coordinates health monitoring and recovery"""
    
    def __init__(self):
        self.health_monitor = SystemHealthMonitor()
        self.recovery_executor = RecoveryExecutor()
        self.running = False
    
    async def start(self):
        """Start the recovery manager"""
        self.running = True
        logger.info("Auto-recovery manager started")
        
        while self.running:
            try:
                # Check system health
                recovery_actions = await self.health_monitor.check_system_health()
                
                # Sort actions by priority (lower number = higher priority)
                recovery_actions.sort(key=lambda a: a.priority)
                
                # Execute recovery actions
                for action in recovery_actions:
                    try:
                        await self.recovery_executor.execute_action(action)
                    except Exception as e:
                        logger.error(f"Recovery action execution failed: {e}")
                
                # Wait before next check
                await asyncio.sleep(config.health_check_interval)
                
            except Exception as e:
                logger.error(f"Recovery manager error: {e}")
                await asyncio.sleep(config.health_check_interval)
    
    async def stop(self):
        """Stop the recovery manager"""
        self.running = False
        logger.info("Auto-recovery manager stopped")