#!/usr/bin/env python3
"""
TaskCtl - Command-line interface for managing Auto-Claude tasks
"""

import click
import json
import sys
import asyncio
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from models import Task, TaskState, TaskType, TaskPriority
from task_manager import TaskManager
from database import db
from config.config import config
from utils import setup_logging, format_duration
from security import security_manager
from monitoring import MonitoringService
from command_generator import command_generator
from crontab_manager import crontab_manager, ScheduledTask


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.option('--config-file', help='Path to configuration file')
@click.pass_context
def cli(ctx, verbose, config_file):
    """Auto-Claude Task Management CLI"""
    ctx.ensure_object(dict)
    
    # Setup logging
    log_level = "DEBUG" if verbose else "INFO"
    setup_logging(log_level)
    
    # Load config if specified
    if config_file:
        from config.config import Config
        global config
        config = Config.load(config_file)


@cli.group()
def task():
    """Task management commands"""
    pass


@task.command()
@click.argument('name')
@click.option('--description', '-d', required=True, help='Task description - what you want Claude to do')
@click.option('--type', 'task_type', 
              type=click.Choice(['lightweight', 'medium_context', 'heavy_context']),
              default='heavy_context', help='Task type')
@click.option('--priority', '-p',
              type=click.Choice(['low', 'normal', 'high', 'urgent']),
              default='normal', help='Task priority')
@click.option('--working-dir', help='Working directory for task')
@click.option('--env', multiple=True, help='Environment variables (KEY=VALUE)')
@click.option('--tag', multiple=True, help='Tags for task')
@click.option('--skip-security-scan', is_flag=True, help='Skip security scan')
def create(name, description, task_type, priority, working_dir, env, tag, skip_security_scan):
    """Create a new task"""
    try:
        click.echo("Creating task manager...")
        task_manager = TaskManager()
        click.echo("Task manager created successfully")
        
        # Parse environment variables
        environment = {}
        for env_var in env:
            if '=' in env_var:
                key, value = env_var.split('=', 1)
                environment[key] = value
            else:
                click.echo(f"Warning: Ignoring invalid environment variable: {env_var}")
        
        # Generate Claude command from description
        click.echo(f"Generating Claude command for task: {name}")
        generated_command = command_generator.generate_command(
            name=name,
            description=description,
            task_type=TaskType(task_type),
            working_dir=working_dir,
            auto_execute=True
        )
        
        # Validate generated command
        if not command_generator.validate_command(generated_command):
            click.echo("❌ Generated command failed validation. Please check your task description.")
            return
        
        # Show generated command to user
        click.echo(f"Generated command: {generated_command}")
        
        # Create task
        click.echo("Creating task...")
        # Fix tag list conversion issue
        tag_list = [] if not tag else list(tag)
        task = task_manager.create_task(
            name=name,
            command=generated_command,
            description=description,
            task_type=TaskType(task_type),
            priority=TaskPriority(priority),
            working_dir=working_dir,
            environment=environment,
            tags=tag_list
        )
        
        # Security scan
        if not skip_security_scan:
            click.echo("Running security scan...")
            scan_results = asyncio.run(security_manager.scan_task(task))
            
            if scan_results['blocked']:
                click.echo(f"❌ Task blocked by security scan: {scan_results['risk_level']}")
                click.echo("Violations:")
                for violation in scan_results['violations']:
                    click.echo(f"  - {violation['type']}: {violation.get('description', 'N/A')}")
                return
            elif scan_results['violations']:
                click.echo(f"⚠️  Security warnings (Risk: {scan_results['risk_level']}):")
                for violation in scan_results['violations']:
                    click.echo(f"  - {violation['type']}: {violation.get('description', 'N/A')}")
        
        # Get permission level info based on task type
        permission_info = {
            "lightweight": "只读权限 (Read, Grep, Glob)",
            "medium_context": "读写权限 (Read, Write, Edit, Git)",
            "heavy_context": "完全权限 (Read, Write, Edit, Bash, WebFetch)"
        }
        
        click.echo(f"✅ Task created: {task.id}")
        click.echo(f"   Name: {task.name}")
        click.echo(f"   Command: {task.command}")
        click.echo(f"   Priority: {task.priority.value}")
        click.echo(f"   Type: {task.task_type.value} ({permission_info.get(task.task_type.value, 'Unknown')})")
        return
        
    except Exception as e:
        click.echo(f"❌ Error creating task: {e}", err=True)
        import traceback
        click.echo("Full traceback:")
        click.echo(traceback.format_exc())
        sys.exit(1)


@task.command()
@click.option('--state', multiple=True, help='Filter by state')
@click.option('--priority', multiple=True, help='Filter by priority') 
@click.option('--tag', multiple=True, help='Filter by tag')
@click.option('--limit', default=50, help='Maximum number of tasks to show')
@click.option('--format', 'output_format', type=click.Choice(['table', 'json']), default='table')
def list(state, priority, tag, limit, output_format):
    """List tasks"""
    try:
        # Get all tasks if no state filter, otherwise filter by states
        if state:
            tasks = []
            for s in state:
                tasks.extend(db.get_tasks_by_state([s]))
        else:
            all_states = [s.value for s in TaskState]
            tasks = []
            for s in all_states:
                tasks.extend(db.get_tasks_by_state([s]))
        
        # Apply additional filters
        if priority:
            tasks = [t for t in tasks if t.priority.value in priority]
        
        if tag:
            tasks = [t for t in tasks if any(t_tag in t.tags for t_tag in tag)]
        
        # Sort by creation time (newest first) and limit
        tasks.sort(key=lambda x: x.created_at, reverse=True)
        tasks = tasks[:limit]
        
        if output_format == 'json':
            task_data = [t.model_dump(mode='json') for t in tasks]
            click.echo(json.dumps(task_data, indent=2, default=str))
        else:
            # Table format
            if not tasks:
                click.echo("No tasks found.")
                return
            
            click.echo(f"{'ID':<15} {'Name':<20} {'State':<15} {'Priority':<8} {'Created':<12}")
            click.echo("-" * 78)
            
            for task in tasks:
                created_str = task.created_at.strftime('%Y-%m-%d')
                click.echo(f"{task.id:<15} {task.name[:20]:<20} {task.task_state.value:<15} {task.priority.value:<8} {created_str:<12}")
        
    except Exception as e:
        click.echo(f"❌ Error listing tasks: {e}", err=True)
        sys.exit(1)


@task.command()
@click.argument('task_id')
@click.option('--show-logs', is_flag=True, help='Show task logs')
@click.option('--format', 'output_format', type=click.Choice(['table', 'json']), default='table', help='Output format')
def show(task_id, show_logs, output_format):
    """Show detailed task information"""
    try:
        task = db.get_task(task_id)
        if not task:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)

        # JSON format output
        if output_format == 'json':
            task_dict = task.model_dump(mode='json')
            click.echo(json.dumps(task_dict, indent=2, default=str, ensure_ascii=False))
            return

        # Table/text format output (default)
        # Basic info
        click.echo(f"Task ID: {task.id}")
        click.echo(f"Name: {task.name}")
        click.echo(f"Description: {task.description or 'None'}")
        click.echo(f"Command: {task.command}")
        click.echo(f"State: {task.task_state.value}")
        click.echo(f"Priority: {task.priority.value}")
        click.echo(f"Type: {task.task_type.value}")
        click.echo(f"Created: {task.created_at}")

        if task.started_at:
            click.echo(f"Started: {task.started_at}")
        if task.completed_at:
            click.echo(f"Completed: {task.completed_at}")
            duration = (task.completed_at - task.started_at).total_seconds() if task.started_at else None
            if duration:
                click.echo(f"Duration: {format_duration(int(duration))}")

        if task.next_allowed_at:
            click.echo(f"Next allowed: {task.next_allowed_at}")

        if task.retry_count > 0:
            click.echo(f"Retry count: {task.retry_count}/{task.max_retries}")

        if task.assigned_worker:
            click.echo(f"Assigned worker: {task.assigned_worker}")

        if task.tags:
            click.echo(f"Tags: {', '.join(task.tags)}")

        if task.last_error:
            click.echo(f"Last error: {task.last_error}")

        # Show logs if requested
        if show_logs:
            task_dir = config.tasks_dir / task.id
            output_file = task_dir / "output.log"

            if output_file.exists():
                click.echo("\n--- Task Output ---")
                with open(output_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Show last 50 lines
                    lines = content.split('\n')[-50:]
                    for line in lines:
                        click.echo(line)
            else:
                click.echo("No logs available.")

    except Exception as e:
        click.echo(f"❌ Error showing task: {e}", err=True)
        sys.exit(1)


@task.command()
@click.argument('task_id')
@click.confirmation_option(prompt='Are you sure you want to cancel this task?')
def cancel(task_id):
    """Cancel a task"""
    try:
        task = db.get_task(task_id)
        if not task:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)
        
        if task.task_state in [TaskState.COMPLETED, TaskState.FAILED]:
            click.echo(f"❌ Cannot cancel task in {task.task_state.value} state")
            sys.exit(1)
        
        task_manager = TaskManager()
        task_manager.update_task_state(task, TaskState.FAILED, "Cancelled by user")
        
        click.echo(f"✅ Task {task_id} cancelled")
        
    except Exception as e:
        click.echo(f"❌ Error cancelling task: {e}", err=True)
        sys.exit(1)


@task.command()
@click.argument('task_id')
def reset(task_id):
    """Reset a retrying/stuck task back to pending state"""
    try:
        task = db.get_task(task_id)
        if not task:
            click.echo(f"❌ Task {task_id} not found")
            sys.exit(1)
        
        if task.task_state not in [TaskState.RETRYING, TaskState.PROCESSING, TaskState.WAITING_UNBAN]:
            click.echo(f"❌ Can only reset retrying/processing/waiting_unban tasks, not {task.task_state.value}")
            sys.exit(1)
        
        task_manager = TaskManager()
        # Reset retry count and move back to pending
        task.retry_count = 0
        task.next_allowed_at = None
        task_manager.update_task_state(task, TaskState.PENDING, "Reset by user")
        
        click.echo(f"✅ Task {task_id} reset to pending state")
        
    except Exception as e:
        click.echo(f"❌ Error resetting task: {e}", err=True)
        sys.exit(1)


@task.command()
@click.option('--dry-run', is_flag=True, help='Show what would be reset without actually doing it')
def reset_all_retrying(dry_run):
    """Reset all retrying tasks back to pending state"""
    try:
        retrying_tasks = db.get_tasks_by_state([TaskState.RETRYING.value])
        
        if not retrying_tasks:
            click.echo("No retrying tasks found.")
            return
        
        if dry_run:
            click.echo("Would reset the following tasks:")
            for task in retrying_tasks:
                click.echo(f"  - {task.id} ({task.name}) - retry count: {task.retry_count}")
            return
        
        task_manager = TaskManager()
        reset_count = 0
        
        for task in retrying_tasks:
            try:
                task.retry_count = 0
                task.next_allowed_at = None
                task_manager.update_task_state(task, TaskState.PENDING, "Reset by batch command")
                reset_count += 1
                click.echo(f"✅ Reset {task.id} ({task.name})")
            except Exception as e:
                click.echo(f"❌ Failed to reset {task.id}: {e}")
        
        click.echo(f"✅ Reset {reset_count} tasks to pending state")
        
    except Exception as e:
        click.echo(f"❌ Error resetting tasks: {e}", err=True)
        sys.exit(1)


@task.command()
@click.argument('task_id')
def retry(task_id):
    """Retry a failed task"""
    try:
        task = db.get_task(task_id)
        if not task:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)
        
        if not task.can_retry():
            click.echo(f"❌ Task cannot be retried (state: {task.task_state.value}, retries: {task.retry_count}/{task.max_retries})")
            sys.exit(1)
        
        task_manager = TaskManager()
        task_manager.update_task_state(task, TaskState.PENDING, "Retried by user")
        
        click.echo(f"✅ Task {task_id} queued for retry")
        
    except Exception as e:
        click.echo(f"❌ Error retrying task: {e}", err=True)
        sys.exit(1)


@cli.group()
def worker():
    """Worker management commands"""
    pass


@worker.command()
def list():
    """List active workers"""
    try:
        workers = db.get_active_workers()
        
        if not workers:
            click.echo("No active workers found.")
            return
        
        click.echo(f"{'Worker ID':<16} {'PID':<8} {'State':<12} {'Current Task':<14} {'Last Heartbeat':<12}")
        click.echo("-" * 80)
        
        for worker in workers:
            pid = str(worker.process_id) if worker.process_id else "N/A"
            task_id = worker.current_task_id[:12] if worker.current_task_id else "None"
            heartbeat = worker.last_heartbeat.strftime('%H:%M:%S') if worker.last_heartbeat else "N/A"
            
            click.echo(f"{worker.worker_id[:16]:<16} {pid:<8} {worker.state.value:<12} {task_id:<14} {heartbeat:<12}")
        
    except Exception as e:
        click.echo(f"❌ Error listing workers: {e}", err=True)
        sys.exit(1)


@cli.group()
def security():
    """Security management commands"""
    pass


@security.command()
@click.argument('task_id')
def scan(task_id):
    """Run security scan on a task"""
    try:
        task = db.get_task(task_id)
        if not task:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)
        
        click.echo("Running security scan...")
        scan_results = asyncio.run(security_manager.scan_task(task))
        
        click.echo(f"Task: {task_id}")
        click.echo(f"Risk Level: {scan_results['risk_level']}")
        click.echo(f"Blocked: {'Yes' if scan_results['blocked'] else 'No'}")
        
        if scan_results['violations']:
            click.echo("\nViolations:")
            for violation in scan_results['violations']:
                click.echo(f"  - {violation['type']} ({violation.get('severity', 'unknown')})")
                if 'description' in violation:
                    click.echo(f"    {violation['description']}")
        
        if scan_results['recommendations']:
            click.echo("\nRecommendations:")
            for rec in scan_results['recommendations']:
                click.echo(f"  - {rec}")
        
    except Exception as e:
        click.echo(f"❌ Error running security scan: {e}", err=True)
        sys.exit(1)


@security.command()
@click.argument('task_id')
@click.argument('reason')
def unblock(task_id, reason):
    """Unblock a task after security review"""
    try:
        success = security_manager.unblock_task(task_id, reason)
        
        if success:
            click.echo(f"✅ Task {task_id} unblocked")
        else:
            click.echo(f"❌ Task {task_id} not found in blocked tasks")
        
    except Exception as e:
        click.echo(f"❌ Error unblocking task: {e}", err=True)
        sys.exit(1)


@security.command()
def report():
    """Show security report"""
    try:
        report = security_manager.get_security_report()
        
        click.echo("Security Report")
        click.echo("=" * 50)
        click.echo(f"Status: {report['security_status']}")
        click.echo(f"Blocked tasks: {report['blocked_tasks_count']}")
        
        if report['blocked_task_ids']:
            click.echo("Blocked task IDs:")
            for task_id in report['blocked_task_ids']:
                click.echo(f"  - {task_id}")
        
        if report['recent_violations']:
            click.echo("\nRecent violations (last 24h):")
            for violation_type, count in report['recent_violations'].items():
                click.echo(f"  - {violation_type}: {count}")
        
    except Exception as e:
        click.echo(f"❌ Error generating security report: {e}", err=True)
        sys.exit(1)


@cli.group()
def system():
    """System management commands"""
    pass


@system.command()
def status():
    """Show system status"""
    try:
        import psutil
        import os
        
        # Check if auto_claude.py is running
        auto_claude_running = False
        auto_claude_pid = None
        auto_claude_uptime = 0
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
            try:
                if proc.info['cmdline'] and any('auto_claude.py' in arg for arg in proc.info['cmdline']):
                    auto_claude_running = True
                    auto_claude_pid = proc.info['pid']
                    auto_claude_uptime = int(time.time() - proc.info['create_time'])
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        # Get monitoring service status if available
        try:
            monitoring = MonitoringService()
            health_status = monitoring.get_health_status()
        except:
            health_status = None
        
        # Determine overall system status
        if auto_claude_running:
            overall_status = 'healthy'
        else:
            overall_status = 'critical'
        
        click.echo("System Status")
        click.echo("=" * 50)
        click.echo(f"Overall Status: {overall_status}")
        click.echo(f"Timestamp: {datetime.utcnow().isoformat()}Z")
        
        # Auto-Claude Process Status
        click.echo(f"\nAuto-Claude Worker Service:")
        if auto_claude_running:
            click.echo(f"  Status: Running (PID: {auto_claude_pid})")
            click.echo(f"  Uptime: {auto_claude_uptime} seconds")
        else:
            click.echo(f"  Status: Not running")
            click.echo(f"  To start: python auto_claude.py &")
        
        # Additional metrics from monitoring service
        if health_status and 'metrics' in health_status:
            metrics = health_status['metrics']
            click.echo(f"\nResources:")
            click.echo(f"  Disk free: {metrics['disk_free_gb']:.1f} GB")
            click.echo(f"  Memory usage: {metrics['memory_usage_percent']:.1f}%")
            click.echo(f"  CPU usage: {metrics['cpu_usage_percent']:.1f}%")
            
            click.echo(f"\nTasks:")
            click.echo(f"  Active workers: {metrics['active_workers']}")
            click.echo(f"  Pending: {metrics['pending_tasks']}")
            click.echo(f"  Processing: {metrics['processing_tasks']}")
            click.echo(f"  Failed: {metrics['failed_tasks']}")
            click.echo(f"  Completed: {metrics['completed_tasks']}")
        else:
            # Fallback: get basic task counts from database
            try:
                tasks = db.get_tasks_by_state(['pending'])
                pending_count = len(tasks) if tasks else 0
                tasks = db.get_tasks_by_state(['processing'])
                processing_count = len(tasks) if tasks else 0
                
                click.echo(f"\nTasks:")
                click.echo(f"  Pending: {pending_count}")
                click.echo(f"  Processing: {processing_count}")
            except:
                click.echo(f"\nTasks: Unable to fetch task counts")
        
        # Alerts
        if health_status and 'alerts' in health_status:
            alerts = health_status['alerts']
            click.echo(f"\nAlerts:")
            click.echo(f"  Unresolved: {alerts['unresolved_count']}")
            click.echo(f"  Recent rate limits: {alerts['recent_rate_limits']}")
        
    except Exception as e:
        click.echo(f"❌ Error getting system status: {e}", err=True)
        sys.exit(1)


@system.command()
@click.option('--days', default=7, help='Number of days to keep')
def cleanup(days):
    """Clean up old data"""
    try:
        click.echo(f"Cleaning up data older than {days} days...")
        
        # Database cleanup
        db.cleanup_old_data(days)
        
        # Log file cleanup
        cleaned_mb = 0
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        for log_file in config.logs_dir.rglob("*.log"):
            if log_file.stat().st_mtime < cutoff_date.timestamp():
                size_mb = log_file.stat().st_size / (1024 * 1024)
                log_file.unlink()
                cleaned_mb += size_mb
        
        click.echo(f"✅ Cleanup completed. Freed {cleaned_mb:.1f}MB of log files.")
        
    except Exception as e:
        click.echo(f"❌ Error during cleanup: {e}", err=True)
        sys.exit(1)


@cli.command()
def init():
    """Initialize Auto-Claude system"""
    try:
        click.echo("Initializing Auto-Claude system...")
        
        # Create directories
        for directory in [config.tasks_dir, config.queue_dir / "pending", 
                         config.queue_dir / "processing", config.snapshots_dir, 
                         config.logs_dir]:
            directory.mkdir(parents=True, exist_ok=True)
            click.echo(f"Created directory: {directory}")
        
        # Initialize database
        db.init_db()
        click.echo("Initialized database")
        
        # Create systemd service file
        create_systemd_service()
        
        click.echo("✅ Auto-Claude system initialized successfully!")
        click.echo("\nNext steps:")
        click.echo("1. Review configuration in config/config.py")
        click.echo("2. Start the service: sudo systemctl start auto-claude")
        click.echo("3. Enable auto-start: sudo systemctl enable auto-claude")
        click.echo("4. Create your first task: taskctl task create 'My Task' 'echo Hello World'")
        
    except Exception as e:
        click.echo(f"❌ Error during initialization: {e}", err=True)
        sys.exit(1)


def create_systemd_service():
    """Create systemd service file"""
    try:
        service_content = f"""[Unit]
Description=Auto-Claude Task Automation System
After=network.target

[Service]
Type=simple
User=auto-claude
WorkingDirectory={config.base_dir}
Environment=PYTHONPATH={config.base_dir}
ExecStart={sys.executable} -m auto_claude
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
        
        service_file = Path("/etc/systemd/system/auto-claude.service")
        service_file.write_text(service_content)
        click.echo(f"Created systemd service: {service_file}")
        
    except PermissionError:
        click.echo("⚠️  Could not create systemd service (insufficient permissions)")
        click.echo("   Run as root or manually create /etc/systemd/system/auto-claude.service")
    except Exception as e:
        click.echo(f"⚠️  Could not create systemd service: {e}")


@cli.group()
def schedule():
    """Scheduled task management commands"""
    pass


@schedule.command()
@click.argument('name')
@click.option('--description', '-d', required=True, help='Task description - what you want Claude to do')
@click.option('--cron', '-c', required=True, help='Cron expression (e.g., "0 9 * * 1-5" for weekdays at 9am)')
@click.option('--type', 'task_type', 
              type=click.Choice(['lightweight', 'medium_context', 'heavy_context']),
              default='heavy_context', help='Task type')
@click.option('--working-dir', help='Working directory for task')
def add(name, description, cron, task_type, working_dir):
    """Add a scheduled task to crontab"""
    try:
        click.echo(f"Creating scheduled task: {name}")
        
        # Validate cron expression
        if not crontab_manager.validate_cron_expression(cron):
            click.echo(f"❌ Invalid cron expression: {cron}")
            click.echo("Format: minute hour day month weekday")
            click.echo("Example: '0 9 * * 1-5' (weekdays at 9am)")
            return
        
        # Add scheduled task
        task_id = crontab_manager.add_scheduled_task(
            name=name,
            description=description,
            cron_expr=cron,
            task_type=task_type,
            working_dir=working_dir
        )
        
        click.echo(f"✅ Scheduled task added successfully!")
        click.echo(f"   Task ID: {task_id}")
        click.echo(f"   Name: {name}")
        click.echo(f"   Schedule: {cron}")
        click.echo(f"   Type: {task_type}")
        click.echo(f"   Description: {description}")
        
    except ValueError as e:
        click.echo(f"❌ Validation error: {e}", err=True)
        sys.exit(1)
    except RuntimeError as e:
        click.echo(f"❌ System error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error creating scheduled task: {e}", err=True)
        sys.exit(1)


@schedule.command()
@click.option('--format', 'output_format', type=click.Choice(['table', 'json']), default='table')
def list(output_format):
    """List all scheduled tasks"""
    try:
        tasks = crontab_manager.list_scheduled_tasks()
        
        if not tasks:
            click.echo("No scheduled tasks found.")
            return
        
        if output_format == 'json':
            task_data = []
            for task in tasks:
                task_data.append({
                    'task_id': task.task_id,
                    'name': task.name,
                    'description': task.description,
                    'cron_expression': task.cron_expr,
                    'task_type': task.task_type,
                    'working_dir': task.working_dir,
                    'enabled': task.enabled,
                    'created_at': task.created_at.isoformat()
                })
            click.echo(json.dumps(task_data, indent=2))
        else:
            # Table format
            click.echo(f"{'Name':<20} {'Schedule':<15} {'Type':<15} {'Status':<8} {'Created':<12}")
            click.echo("-" * 78)
            
            for task in tasks:
                status = "Enabled" if task.enabled else "Disabled"
                created_str = task.created_at.strftime('%Y-%m-%d')
                click.echo(f"{task.name[:20]:<20} {task.cron_expr:<15} {task.task_type:<15} {status:<8} {created_str:<12}")
        
    except RuntimeError as e:
        click.echo(f"❌ System error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error listing scheduled tasks: {e}", err=True)
        sys.exit(1)


@schedule.command()
@click.argument('task_id')
def remove(task_id):
    """Remove a scheduled task by task ID"""
    try:
        if crontab_manager.remove_scheduled_task(task_id):
            click.echo(f"✅ Scheduled task {task_id} removed successfully")
        else:
            click.echo(f"❌ Scheduled task {task_id} not found")
            sys.exit(1)
            
    except RuntimeError as e:
        click.echo(f"❌ System error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error removing scheduled task: {e}", err=True)
        sys.exit(1)


@schedule.command()
@click.argument('name')
def remove_by_name(name):
    """Remove a scheduled task by name"""
    try:
        if crontab_manager.remove_scheduled_task_by_name(name):
            click.echo(f"✅ Scheduled task '{name}' removed successfully")
        else:
            click.echo(f"❌ Scheduled task '{name}' not found")
            sys.exit(1)
            
    except RuntimeError as e:
        click.echo(f"❌ System error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error removing scheduled task: {e}", err=True)
        sys.exit(1)


@schedule.command()
@click.argument('task_id')
def enable(task_id):
    """Enable a scheduled task"""
    try:
        if crontab_manager.enable_scheduled_task(task_id):
            click.echo(f"✅ Scheduled task {task_id} enabled")
        else:
            click.echo(f"❌ Scheduled task {task_id} not found")
            sys.exit(1)
            
    except RuntimeError as e:
        click.echo(f"❌ System error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error enabling scheduled task: {e}", err=True)
        sys.exit(1)


@schedule.command()
@click.argument('task_id')
def disable(task_id):
    """Disable a scheduled task"""
    try:
        if crontab_manager.disable_scheduled_task(task_id):
            click.echo(f"✅ Scheduled task {task_id} disabled")
        else:
            click.echo(f"❌ Scheduled task {task_id} not found")
            sys.exit(1)
            
    except RuntimeError as e:
        click.echo(f"❌ System error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error disabling scheduled task: {e}", err=True)
        sys.exit(1)


if __name__ == '__main__':
    cli()