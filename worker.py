import os
import sys
import asyncio
import subprocess
import signal
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from uuid import uuid4

from models import Task, TaskState, ProcessState, WorkerStatus, TaskType
from task_manager import TaskManager
from database import db
from config.config import config
from utils import (
    create_alert, AlertLevel, parse_claude_error, sanitize_output,
    check_violation_keywords, get_system_metrics, atomic_write,
    is_process_alive, format_duration
)


logger = logging.getLogger(__name__)


class ClaudeWorker:
    def __init__(self, worker_id: str = None):
        self.worker_id = worker_id or f"worker_{uuid4().hex[:8]}"
        self.task_manager = TaskManager()
        self.current_task: Optional[Task] = None
        self.process: Optional[subprocess.Popen] = None
        self.running = False
        self.status = WorkerStatus(worker_id=self.worker_id)
        
        # Set environment variable for task manager
        os.environ['WORKER_ID'] = self.worker_id
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Worker {self.worker_id} received signal {signum}")
        self.running = False
        
        if self.current_task and self.process:
            self._save_resume_patch()
            self._terminate_process()
    
    async def start(self):
        """Start the worker"""
        self.running = True
        self.status.state = ProcessState.RUNNING
        
        logger.info(f"Worker {self.worker_id} started")
        
        # Start background tasks
        heartbeat_task = asyncio.create_task(self._send_heartbeat())
        process_task = asyncio.create_task(self._process_tasks())
        
        try:
            await asyncio.gather(heartbeat_task, process_task)
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the worker"""
        self.running = False
        self.status.state = ProcessState.TERMINATING
        
        if self.process:
            self._terminate_process()
        
        logger.info(f"Worker {self.worker_id} stopped")
    
    async def _send_heartbeat(self):
        """Send periodic heartbeat"""
        while self.running:
            try:
                self.status.last_heartbeat = datetime.utcnow()
                self.status.process_id = os.getpid()
                
                if self.current_task:
                    self.status.current_task_id = self.current_task.id
                
                # Update resource usage
                import psutil
                process = psutil.Process()
                self.status.cpu_usage = process.cpu_percent()
                self.status.memory_usage = process.memory_info().rss
                
                # Save to database
                db.save_worker_status(self.status)
                
                await asyncio.sleep(config.heartbeat_interval)
                
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                await asyncio.sleep(config.heartbeat_interval)
    
    async def _process_tasks(self):
        """Main task processing loop"""
        while self.running:
            try:
                # Get next task
                task = self.task_manager.get_next_pending_task()
                
                if not task:
                    await asyncio.sleep(5)  # Wait for tasks
                    continue
                
                self.current_task = task
                await self._execute_task(task)
                self.current_task = None
                
            except Exception as e:
                logger.error(f"Task processing error: {e}")
                if self.current_task:
                    self.task_manager.update_task_state(
                        self.current_task, 
                        TaskState.FAILED,
                        f"Worker error: {str(e)}"
                    )
                await asyncio.sleep(10)
    
    async def _execute_task(self, task: Task):
        """Execute a single task"""
        logger.info(f"Executing task {task.id}: {task.name}")
        
        try:
            # Update task state
            self.task_manager.update_task_state(task, TaskState.PROCESSING)
            self.status.state = ProcessState.RUNNING
            
            # Setup task environment
            task_dir = config.tasks_dir / task.id
            task_dir.mkdir(exist_ok=True)
            
            # Check for resume context
            resume_context = ""
            if task.task_state == TaskState.RETRYING:
                resume_context = self.task_manager.generate_resume_context(task)
            
            # Execute the task
            success = await self._run_claude_command(task, resume_context)
            
            if success:
                self.task_manager.update_task_state(task, TaskState.COMPLETED)
                self.status.tasks_completed += 1
                logger.info(f"Task {task.id} completed successfully")
            else:
                # Task failed, check if it can be retried
                if task.can_retry():
                    self.task_manager.update_task_state(
                        task, 
                        TaskState.RETRYING,
                        save_snapshot=True
                    )
                    logger.info(f"Task {task.id} will be retried (attempt {task.retry_count + 1})")
                else:
                    self.task_manager.update_task_state(task, TaskState.FAILED)
                    self.status.tasks_failed += 1
                    logger.error(f"Task {task.id} failed permanently")
            
        except Exception as e:
            logger.error(f"Task execution error: {e}")
            self.task_manager.update_task_state(
                task, 
                TaskState.FAILED,
                f"Execution error: {str(e)}"
            )
            self.status.tasks_failed += 1
    
    async def _run_claude_command(self, task: Task, resume_context: str = "") -> bool:
        """Run Claude CLI command with monitoring"""
        task_dir = config.tasks_dir / task.id
        
        # Prepare command
        full_command = task.command
        if resume_context:
            # Save resume context to file
            resume_file = task_dir / "resume_context.txt"
            atomic_write(str(resume_file), resume_context)
            full_command = f"cat {resume_file} && {task.command}"
        
        # Prepare environment
        env = os.environ.copy()
        env.update(task.environment)
        
        # Working directory
        working_dir = task.working_dir or str(task_dir)
        
        try:
            # Start process
            self.process = subprocess.Popen(
                full_command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=working_dir,
                text=True,
                bufsize=1  # Line buffered
            )
            
            self.status.state = ProcessState.RUNNING
            
            # Monitor process output
            return await self._monitor_process(task)
            
        except Exception as e:
            logger.error(f"Failed to start process: {e}")
            return False
    
    async def _monitor_process(self, task: Task) -> bool:
        """Monitor running process with real-time output analysis"""
        task_dir = config.tasks_dir / task.id
        output_file = task_dir / "output.log"
        resume_patch_file = task_dir / "resume_patch.txt"
        
        start_time = time.time()
        last_output_time = start_time
        output_buffer = []
        total_output = ""
        
        try:
            with open(output_file, 'w', encoding='utf-8') as log_file:
                
                while self.process and self.process.poll() is None and self.running:
                    
                    # Check for timeout
                    current_time = time.time()
                    if current_time - last_output_time > config.claude_cli_timeout:
                        logger.warning(f"Task {task.id} appears hung (no output for {config.claude_cli_timeout}s)")
                        self.status.state = ProcessState.HUNG
                        self._terminate_process()
                        return False
                    
                    # Check session limit
                    if current_time - start_time > config.claude_session_limit:
                        logger.info(f"Task {task.id} hit 5-hour session limit")
                        self._save_resume_patch(output_buffer)
                        self.task_manager.update_task_state(
                            task,
                            TaskState.WAITING_UNBAN,
                            "5-hour session limit reached"
                        )
                        self._terminate_process()
                        return False
                    
                    # Read output
                    try:
                        line = await asyncio.wait_for(
                            asyncio.to_thread(self.process.stdout.readline),
                            timeout=1.0
                        )
                        
                        if line:
                            last_output_time = current_time
                            
                            # Sanitize output
                            sanitized_line = sanitize_output(line)
                            
                            # Check for violations
                            violations = check_violation_keywords(line)
                            if violations:
                                logger.warning(f"Violation keywords detected: {violations}")
                                self.task_manager.update_task_state(
                                    task,
                                    TaskState.NEEDS_HUMAN_REVIEW,
                                    f"Violation keywords detected: {violations}"
                                )
                                self._terminate_process()
                                return False
                            
                            # Add to buffers
                            output_buffer.append(sanitized_line)
                            total_output += sanitized_line
                            
                            # Write to log file
                            log_file.write(sanitized_line)
                            log_file.flush()
                            
                            # Keep resume patch buffer (last 500 lines)
                            if len(output_buffer) > 500:
                                output_buffer.pop(0)
                            
                            # Check for errors in output
                            error_info = parse_claude_error(line)
                            if error_info['is_rate_limited']:
                                logger.info(f"Rate limit detected for task {task.id}")
                                self._save_resume_patch(output_buffer)
                                
                                # Calculate wait time
                                wait_time = error_info.get('retry_after', config.default_unban_wait)
                                task.next_allowed_at = datetime.utcnow() + timedelta(seconds=wait_time)
                                
                                self.task_manager.update_task_state(
                                    task,
                                    TaskState.WAITING_UNBAN,
                                    f"Rate limit: {error_info.get('error_message', 'Unknown rate limit')}"
                                )
                                self._terminate_process()
                                return False
                            
                            elif error_info['is_session_expired']:
                                logger.info(f"Session expired for task {task.id}")
                                self._save_resume_patch(output_buffer)
                                self.task_manager.update_task_state(
                                    task,
                                    TaskState.RETRYING,
                                    "Session expired"
                                )
                                self._terminate_process()
                                return False
                            
                            # Check output size
                            if len(total_output) > config.max_output_size:
                                logger.warning(f"Task {task.id} output size exceeded limit")
                                self._save_resume_patch(output_buffer)
                                self.task_manager.update_task_state(
                                    task,
                                    TaskState.PAUSED,
                                    "Output size limit exceeded"
                                )
                                self._terminate_process()
                                return False
                    
                    except asyncio.TimeoutError:
                        # No output available, continue monitoring
                        continue
                    
                    except Exception as e:
                        logger.error(f"Error reading process output: {e}")
                        break
            
            # Process finished, check exit code
            if self.process:
                exit_code = self.process.poll()
                if exit_code == 0:
                    logger.info(f"Task {task.id} completed with exit code 0")
                    return True
                else:
                    logger.error(f"Task {task.id} failed with exit code {exit_code}")
                    task.add_error(f"Process exited with code {exit_code}")
                    return False
            
            return False
            
        except Exception as e:
            logger.error(f"Process monitoring error: {e}")
            return False
        finally:
            self._save_resume_patch(output_buffer)
    
    def _save_resume_patch(self, output_buffer: list = None):
        """Save resume patch for task recovery"""
        if not self.current_task:
            return
        
        task_dir = config.tasks_dir / self.current_task.id
        resume_patch_file = task_dir / "resume_patch.txt"
        
        try:
            if output_buffer:
                patch_content = "".join(output_buffer)
            else:
                # Try to get from existing output log
                output_file = task_dir / "output.log"
                if output_file.exists():
                    with open(output_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        patch_content = "".join(lines[-500:])  # Last 500 lines
                else:
                    patch_content = "No output available for resume patch"
            
            atomic_write(str(resume_patch_file), patch_content)
            
            # Update task checkpoint
            self.current_task.resume_hint_file = "resume_patch.txt"
            self.current_task.checkpoint_data = {
                'last_saved': datetime.utcnow().isoformat(),
                'output_lines': len(patch_content.split('\n')),
                'patch_size': len(patch_content)
            }
            
            logger.info(f"Saved resume patch for task {self.current_task.id}")
            
        except Exception as e:
            logger.error(f"Failed to save resume patch: {e}")
    
    def _terminate_process(self):
        """Terminate the current process gracefully"""
        if not self.process:
            return
        
        try:
            self.status.state = ProcessState.TERMINATING
            
            # Try graceful shutdown first
            self.process.terminate()
            
            # Wait up to 10 seconds for graceful shutdown
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown fails
                logger.warning("Process didn't terminate gracefully, killing...")
                self.process.kill()
                self.process.wait()
                self.status.state = ProcessState.KILLED
            
            logger.info(f"Process terminated for task {self.current_task.id if self.current_task else 'unknown'}")
            
        except Exception as e:
            logger.error(f"Error terminating process: {e}")
        finally:
            self.process = None


async def main():
    """Main worker entry point"""
    import argparse
    from utils import setup_logging
    
    parser = argparse.ArgumentParser(description="Claude Code Auto Worker")
    parser.add_argument("--worker-id", help="Worker ID")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    parser.add_argument("--log-file", help="Log file path")
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level, args.log_file)
    
    # Create and start worker
    worker = ClaudeWorker(args.worker_id)
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    except Exception as e:
        logger.error(f"Worker error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())