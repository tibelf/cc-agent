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
from uuid import uuid4, UUID

from models import Task, TaskState, ProcessState, WorkerStatus, TaskType
from task_manager import TaskManager
from database import db
from config.config import config
from utils import (
    create_alert, AlertLevel, parse_claude_error, sanitize_output,
    get_system_metrics, atomic_write,
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
            # Determine if we should resume with context BEFORE changing state
            resume_context = ""
            if task.task_state == TaskState.RETRYING:
                resume_context = self.task_manager.generate_resume_context(task)

            # Update task state
            self.task_manager.update_task_state(task, TaskState.PROCESSING)
            self.status.state = ProcessState.RUNNING

            # Setup task environment
            task_dir = config.tasks_dir / task.id
            task_dir.mkdir(exist_ok=True)
            
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
        
        # Prepare command - check if we need to resume with session
        if task.checkpoint_data.get('session_id') and resume_context:
            # Use session resume
            session_id = task.checkpoint_data['session_id']
            resume_query = self._build_resume_query(task, resume_context)
            full_command = f'claude -r "{session_id}" "{resume_query}"'
            logger.info(f"Task {task.id}: Resuming with session_id: {session_id}")
        else:
            # New task
            full_command = task.command
            logger.info(f"Task {task.id}: Starting new execution")
        
        logger.info(f"Executing task {task.id} with command: {full_command[:100]}...")
        
        if resume_context and not task.checkpoint_data.get('session_id'):
            # Legacy resume context handling (fallback)
            resume_file = task_dir / "resume_context.txt"
            atomic_write(str(resume_file), resume_context)
            full_command = f"cat {resume_file} && {task.command}"
            logger.info(f"Task {task.id} resuming with legacy context")
        
        # Check if command has permission parameters (for logging)
        if "--permission-mode" in full_command:
            logger.info(f"Task {task.id} running with pre-configured permissions")
        elif "--dangerously-skip-permissions" in full_command:
            logger.warning(f"Task {task.id} running with SKIPPED permissions - use with caution")
        
        # Prepare environment
        env = os.environ.copy()
        env.update(task.environment)
        # Disable Python output buffering to ensure real-time output
        env['PYTHONUNBUFFERED'] = '1'
        
        # Working directory
        working_dir = task.working_dir or str(task_dir)
        
        try:
            # Start process using asyncio for better output capture
            self.process = await asyncio.create_subprocess_shell(
                full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=working_dir,
                limit=1024*1024,  # 1MB buffer for large JSON outputs
                executable="/usr/bin/zsh"  # Use zsh for better quote handling
            )
            
            self.status.state = ProcessState.RUNNING
            logger.info(f"Task {task.id}: Process started with asyncio subprocess")
            
            # Monitor process output
            return await self._monitor_process(task)
            
        except Exception as e:
            logger.error(f"Failed to start process: {e}")
            return False
    
    def _build_resume_query(self, task: Task, resume_context: str) -> str:
        """Build resume query for session continuation"""
        if task.checkpoint_data.get('needs_interaction'):
            interaction_prompt = task.checkpoint_data.get('interaction_prompt', '')
            auto_response = task.checkpoint_data.get('auto_response', '').strip()

            if not auto_response:
                auto_response = "我具备完全自主操作权限，无需人工干预。我将继续自主完成所有任务操作。"

            segments = []
            if interaction_prompt:
                segments.append(interaction_prompt.strip())
            segments.append(auto_response)
            if resume_context:
                segments.append(resume_context.strip())

            return "\n\n".join(filter(None, segments)).strip()

        return resume_context
    
    @staticmethod
    def _is_uuid_format(value: Optional[str]) -> bool:
        """Check whether the provided string is a valid UUID."""
        if not value:
            return False
        try:
            UUID(str(value))
            return True
        except (ValueError, TypeError, AttributeError):
            return False

    def _update_session_id(self, task: Task, candidate: str, source: str) -> bool:
        """Update the stored session_id when a better candidate is found."""
        if not candidate:
            return False

        candidate = candidate.strip()
        if not candidate:
            return False

        current = task.checkpoint_data.get('session_id')
        if current == candidate:
            return False

        candidate_is_uuid = self._is_uuid_format(candidate)
        current_is_uuid = self._is_uuid_format(current) if current else False

        # Always prefer UUID format over non-UUID format
        # Do not downgrade an existing UUID to a non-UUID candidate
        if current_is_uuid and not candidate_is_uuid:
            logger.debug(
                "Task %s: Ignoring non-UUID session_id %s from %s, keeping UUID %s",
                task.id,
                candidate,
                source,
                current,
            )
            return False

        # Upgrade from non-UUID to UUID format
        if current and not current_is_uuid and candidate_is_uuid:
            logger.info(
                "Task %s: Upgrading session_id from %s to UUID %s from %s",
                task.id,
                current,
                candidate,
                source,
            )
        # First session_id capture
        elif not current:
            logger.info(
                "Task %s: Captured initial session_id from %s: %s (%s format)",
                task.id,
                source,
                candidate,
                "UUID" if candidate_is_uuid else "short"
            )
        # UUID to UUID replacement (should be rare)
        elif current_is_uuid and candidate_is_uuid:
            logger.info(
                "Task %s: Replacing UUID session_id %s with %s from %s",
                task.id,
                current,
                candidate,
                source,
            )
        # Non-UUID to non-UUID replacement (when no UUID available)
        else:
            logger.info(
                "Task %s: Updating session_id from %s to %s from %s",
                task.id,
                current,
                candidate,
                source,
            )

        task.checkpoint_data['session_id'] = candidate
        return True

    def _extract_session_id(self, output_line: str, task: Task) -> bool:
        """Extract session_id from Claude output"""
        try:
            # Claude Code includes session_id in JSON output
            if '"session_id"' in output_line:
                import json
                data = json.loads(output_line)
                if 'session_id' in data:
                    return self._update_session_id(task, data['session_id'], "line JSON")
        except json.JSONDecodeError:
            pass
        return False
    
    def _process_output_chunk(self, chunk: str, task: Task, output_buffer: list, log_file) -> None:
        """Process a chunk of output for JSON parsing, session_id extraction, and analysis"""
        from utils import sanitize_output
        
        # Write raw chunk to log file
        log_file.write(chunk)
        log_file.flush()
        
        # Split chunk into lines for line-by-line analysis
        lines = chunk.splitlines(keepends=True)
        
        for line in lines:
            if not line.strip():
                continue
                
            # Extract session_id if present
            self._extract_session_id(line, task)
            
            # Check if this line contains Claude JSON result
            result_content = self._extract_claude_result(line)
            if result_content:
                # Use AI to detect interaction need on actual result content
                needs_interaction, auto_response = self._ai_detect_interaction_need_sync(result_content, task)
                if needs_interaction:
                    logger.info(f"Task {task.id}: Detected interaction need in result: {result_content}")
                    # Save interaction state for resume
                    self._save_interaction_state(task, result_content, auto_response)
                    # Update task state to retry with interaction handling
                    self.task_manager.update_task_state(
                        task,
                        TaskState.RETRYING,
                        f"Interaction needed: {result_content}",
                        save_snapshot=True
                    )
                    self._terminate_process()
                    return
            
            # Sanitize and add to buffers
            sanitized_line = sanitize_output(line)
            output_buffer.append(sanitized_line)
            
            # Keep resume patch buffer (last 500 lines)
            if len(output_buffer) > 500:
                output_buffer.pop(0)
        
        # Also try to extract session_id from the entire chunk (in case JSON spans multiple lines)
        self._extract_session_id_from_chunk(chunk, task)
    
    def _extract_session_id_from_chunk(self, chunk: str, task: Task) -> bool:
        """Extract session_id from a chunk of output (handles multi-line JSON)"""
        import json
        import re
        
        try:
            # Try to find JSON objects in the chunk
            json_pattern = r'\{[^{}]*"session_id"[^{}]*\}'
            matches = re.findall(json_pattern, chunk, re.DOTALL)
            
            for match in matches:
                try:
                    data = json.loads(match)
                    if 'session_id' in data and data['session_id']:
                        if self._update_session_id(task, data['session_id'], "chunk JSON"):
                            return True
                except json.JSONDecodeError:
                    continue
            
            # Also try to parse the entire chunk as JSON (for single large JSON objects)
            if '"session_id"' in chunk:
                # Try to extract JSON array or object
                lines = chunk.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('[') or line.startswith('{'):
                        try:
                            # Try parsing as JSON array first
                            if line.startswith('['):
                                json_array = json.loads(line)
                                for item in json_array:
                                    if isinstance(item, dict) and 'session_id' in item:
                                        if self._update_session_id(task, item['session_id'], "chunk array"):
                                            return True
                            else:
                                # Try parsing as single JSON object
                                data = json.loads(line)
                                if 'session_id' in data:
                                    if self._update_session_id(task, data['session_id'], "chunk object"):
                                        return True
                        except json.JSONDecodeError:
                            continue
                            
        except Exception as e:
            # Silent failure for chunk parsing - this is a best-effort enhancement
            pass
            
        return False
    
    async def _fallback_output_capture(self, task: Task) -> str:
        """Fallback method to capture output using subprocess.run when asyncio fails"""
        import subprocess
        
        try:
            task_dir = config.tasks_dir / task.id
            env = os.environ.copy()
            env.update(task.environment)
            env['PYTHONUNBUFFERED'] = '1'
            
            working_dir = task.working_dir or str(task_dir)
            
            logger.info(f"Task {task.id}: Attempting fallback output capture with subprocess.run")
            
            # Execute the same command with subprocess.run for output capture
            result = await asyncio.to_thread(
                subprocess.run,
                task.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=working_dir,
                text=True,
                timeout=30  # Short timeout for fallback
            )
            
            if result.returncode == 0 and result.stdout:
                logger.info(f"Task {task.id}: Fallback capture successful, got {len(result.stdout)} chars")
                return result.stdout
            else:
                logger.warning(f"Task {task.id}: Fallback capture failed or empty, exit code: {result.returncode}")
                return ""
                
        except subprocess.TimeoutExpired:
            logger.warning(f"Task {task.id}: Fallback capture timed out")
            return ""
        except Exception as e:
            logger.warning(f"Task {task.id}: Fallback capture error: {e}")
            return ""
    
    def _extract_claude_result(self, line: str) -> str:
        """Extract result content from Claude JSON output"""
        try:
            if '"type":"result"' in line and '"result"' in line:
                import json
                data = json.loads(line)
                if data.get('type') == 'result':
                    return data.get('result', '')
        except json.JSONDecodeError:
            pass
        return None
    
    def _analyze_final_result(self, task: Task, total_output: str) -> tuple[bool, bool]:
        """Analyze complete output for final result and determine if user interaction is needed
        
        Returns:
            (interaction_needed: bool, task_completed: bool)
        """
        try:
            logger.info(f"Task {task.id}: Analyzing final result for completion and interaction needs")
            
            # First check for completion marker
            if "✅ TASK_COMPLETED" in total_output:
                logger.info(f"Task {task.id}: Found completion marker - task completed successfully")
                return False, True  # No interaction needed, task is complete
            
            import json
            
            # Find lines that contain potential JSON with result
            lines = total_output.split('\n')
            for line in lines:
                if '"type":"result"' in line and '"result"' in line:
                    try:
                        # Try parsing as JSON array first (common format)
                        data = json.loads(line)
                        
                        # Handle both array and single object cases
                        items = data if isinstance(data, list) else [data]
                        
                        for item in items:
                            if isinstance(item, dict) and item.get('type') == 'result':
                                result_content = item.get('result', '')
                                if result_content:
                                    logger.info(f"Task {task.id}: Extracted final result content ({len(result_content)} chars)")
                                    logger.info(f"Task {task.id}: Result preview: {result_content[:200]}...")
                                    
                                    # Check for completion marker in result content
                                    if "✅ TASK_COMPLETED" in result_content:
                                        logger.info(f"Task {task.id}: Found completion marker in result content - task completed")
                                        return False, True  # No interaction needed, task is complete
                                    
                                    # Use AI to detect interaction need
                                    needs_interaction, auto_response = self._ai_detect_interaction_need_sync(result_content, task)
                                    if needs_interaction:
                                        logger.info(f"Task {task.id}: Final result analysis - interaction needed")
                                        self._save_interaction_state(task, result_content, auto_response)
                                        self.task_manager.update_task_state(
                                            task,
                                            TaskState.RETRYING,
                                            f"Final analysis: Interaction needed for result content",
                                            save_snapshot=True
                                        )
                                        return True, False
                                    else:
                                        logger.info(f"Task {task.id}: Final result analysis - no interaction needed but no completion marker found")
                                        # If no completion marker and no interaction needed, task is incomplete
                                        logger.warning(f"Task {task.id}: Task appears incomplete - no completion marker found")
                                        return False, False
                                        
                    except json.JSONDecodeError as e:
                        logger.debug(f"Task {task.id}: JSON parse error for line: {e}")
                        continue
            
            logger.info(f"Task {task.id}: No result JSON found and no completion marker - task may be incomplete")
            return False, False
            
        except Exception as e:
            logger.error(f"Task {task.id}: Error in final result analysis: {e}")
            return False, False

    def _ai_detect_interaction_need_sync(self, result_content: str, task: Task) -> tuple[bool, str]:
        """Use Claude CLI to detect if interaction is needed and generate autonomous response"""
        try:
            prompt = f"""请判断以下文本是否是在请求我们做出确认或选择，并给出可直接执行的答复。

TEXT: {result_content}

要求：
1. 如果文本需要我们确认或选择，输出 JUDGMENT: YES，并在 RESPONSE 中给出可以直接回复给对方的内容。
   - 若原文包含编号选项（如“1.”、“2.”），直接返回对应的数字或字母。
   - 否则使用原语言给出简洁明确的回答，例如 “Yes, please proceed.”。
   - 不允许输出解释、理由或其他附加说明。
2. 如果不是确认请求，输出 JUDGMENT: NO，并让 RESPONSE 为空。

输出格式必须严格如下：
JUDGMENT: YES/NO
RESPONSE: <直接回复内容或留空>

不要输出任何其他文本。"""

            result = subprocess.run(
                ['claude', '-p', prompt],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            if result.returncode == 0:
                response_text = result.stdout.strip()
                
                # 解析判断结果和回复内容
                judgment = "NO"
                auto_response = ""
                
                lines = response_text.split('\n')
                for line in lines:
                    if line.startswith('JUDGMENT:'):
                        judgment = line.replace('JUDGMENT:', '').strip().upper()
                    elif line.startswith('RESPONSE:'):
                        auto_response = line.replace('RESPONSE:', '').strip()

                auto_response = auto_response.strip()
                
                needs_interaction = judgment == "YES"
                logger.info(f"Task {task.id}: AI judgment for '{result_content[:50]}...': {judgment}")
                if needs_interaction:
                    generic_markers = [
                        "自主", "自行", "autonom", "best choice", "choose the best option",
                        "make the best decision", "you can decide"
                    ]
                    normalized_response = auto_response.lower()
                    if not auto_response or any(
                        marker in auto_response or marker in normalized_response
                        for marker in generic_markers
                    ):
                        logger.warning(
                            "Task %s: AI response not actionable, retrying detection", task.id
                        )
                        return False, ""
                    logger.info(
                        "Task %s: Generated autonomous response: %s",
                        task.id,
                        auto_response,
                    )
                
                return needs_interaction, auto_response
            else:
                logger.warning(f"Task {task.id}: AI judgment failed, assuming no interaction needed")
                return False, ""
            
        except Exception as e:
            logger.error(f"Task {task.id}: AI interaction detection error: {e}")
            return False, ""

    def _detect_interaction_need(self, line: str, task: Task) -> bool:
        """Detect if interaction is needed based on output"""
        # Skip lines that contain automation instructions (these are not actual prompts)
        line_lower = line.lower()
        if "automated task execution" in line_lower or "do not ask for confirmation" in line_lower:
            return False
            
        # Look for actual interaction prompts
        confirmation_keywords = ["confirm", "continue", "proceed", "yes", "no", "(y/n)", "[y/n]"]
        return any(keyword in line_lower for keyword in confirmation_keywords)
    
    def _save_interaction_state(self, task: Task, prompt_line: str, auto_response: str = ""):
        """Save interaction state to checkpoint for resume"""
        task.checkpoint_data.update({
            'needs_interaction': True,
            'interaction_prompt': prompt_line.strip(),
            'auto_response': auto_response,
            'interaction_timestamp': datetime.utcnow().isoformat()
        })
        
        logger.info(f"Task {task.id}: Detected interaction need, saving state for resume")
    
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
                # Write task execution header
                header = f"""=== TASK EXECUTION LOG ===
Task ID: {task.id}
Task Name: {task.name}
Command: {task.command}
Started: {datetime.utcnow().isoformat()}
Working Directory: {task_dir}

=== COMMAND OUTPUT ===
"""
                log_file.write(header)
                log_file.flush()
                logger.info(f"Task {task.id} execution started, logging to {output_file}")
                
                # Heuristic: detect prompt-only simple tasks (should complete fast)
                cmd_text = (task.command or "")
                is_prompt_only = (
                    cmd_text.strip().startswith("claude -p")
                    and not any(flag in cmd_text for flag in ["--watch", "--server", "-f ", "--file", "--stdin"])
                )

                while self.process and self.process.returncode is None and self.running:
                    
                    # Check for timeout - but be more lenient for simple tasks
                    current_time = time.time()
                    time_since_start = current_time - start_time
                    time_since_output = current_time - last_output_time
                    
                    # Choose no-output timeout based on task nature
                    effective_timeout = config.claude_cli_timeout
                    if is_prompt_only:
                        # Simple one-shot prompts should finish quickly; fail fast on silence
                        effective_timeout = min(effective_timeout, 900)  # 900s max for no-output
                    else:
                        # Give heavier sessions more leeway in the first 2 minutes
                        if time_since_start < 120:  # First 2 minutes
                            effective_timeout = max(effective_timeout, 900)  # Up to 15 minutes
                    
                    if time_since_output > effective_timeout:
                        logger.warning(f"Task {task.id} appears hung (no output for {time_since_output:.0f}s, timeout: {effective_timeout}s)")
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
                    
                    # Read output in chunks for better JSON capture
                    try:
                        # Try to read available data (non-blocking with timeout)
                        chunk = await asyncio.wait_for(
                            self.process.stdout.read(4096),  # Read up to 4KB chunks
                            timeout=1.0
                        )
                        
                        if chunk:
                            # Convert bytes to string if needed
                            if isinstance(chunk, bytes):
                                chunk = chunk.decode('utf-8', errors='replace')
                            
                            last_output_time = current_time
                            
                            # Process chunk for session_id and other analysis
                            self._process_output_chunk(chunk, task, output_buffer, log_file)
                            
                            total_output += chunk
                            
                            # Check for errors in output chunk
                            error_info = parse_claude_error(chunk)
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
            
            # Process finished, read any remaining output
            if self.process:
                exit_code = await self.process.wait()
                
                # Read any remaining buffered output after process completion
                try:
                    remaining_output = await self.process.stdout.read()
                    if remaining_output:
                        # Convert bytes to string if needed
                        if isinstance(remaining_output, bytes):
                            remaining_output = remaining_output.decode('utf-8', errors='replace')
                        
                        logger.info(f"Task {task.id} had remaining output after completion: {len(remaining_output)} chars")
                        total_output += remaining_output
                        
                        # Process remaining output for session_id extraction
                        with open(output_file, 'a', encoding='utf-8') as log_file:
                            self._process_output_chunk(remaining_output, task, output_buffer, log_file)
                            
                except Exception as e:
                    logger.warning(f"Error reading remaining output for task {task.id}: {e}")
                
                # Fallback: If we didn't capture any output but process succeeded, try subprocess fallback
                if exit_code == 0 and len(total_output.strip()) == 0:
                    logger.info(f"Task {task.id}: No output captured during execution, attempting fallback capture")
                    fallback_output = await self._fallback_output_capture(task)
                    if fallback_output:
                        total_output += fallback_output
                        with open(output_file, 'a', encoding='utf-8') as log_file:
                            log_file.write(f"\n=== FALLBACK OUTPUT CAPTURE ===\n")
                            self._process_output_chunk(fallback_output, task, output_buffer, log_file)
                
                # Write task completion footer to log
                with open(output_file, 'a', encoding='utf-8') as log_file:
                    footer = f"""

=== TASK EXECUTION COMPLETED ===
Exit Code: {exit_code}
Duration: {format_duration(time.time() - start_time)}
Total Output Lines: {len(total_output.splitlines())}
Completed: {datetime.utcnow().isoformat()}
"""
                    log_file.write(footer)
                    log_file.flush()
                
                if exit_code == 0:
                    # Analyze final result for interaction needs and completion before marking as completed
                    interaction_needed, task_completed = self._analyze_final_result(task, total_output)
                    if interaction_needed:
                        # Task needs user interaction, change state to retrying
                        return False
                    
                    if task_completed:
                        logger.info(f"Task {task.id} completed with exit code 0 and completion marker")
                        return True
                    else:
                        logger.error(f"Task {task.id} finished with exit code 0 but no completion marker - marking as failed")
                        task.add_error("Process completed but no completion marker found - task may be incomplete")
                        return False
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

            # Update task checkpoint without discarding existing metadata
            checkpoint_data = dict(self.current_task.checkpoint_data or {})
            checkpoint_data.update({
                'last_saved': datetime.utcnow().isoformat(),
                'output_lines': len(patch_content.split('\n')),
                'patch_size': len(patch_content)
            })

            self.current_task.resume_hint_file = "resume_patch.txt"
            self.current_task.checkpoint_data = checkpoint_data
            
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
            
            # For asyncio processes, we can't use synchronous wait with timeout
            # The process termination will be handled by the monitoring loop
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
