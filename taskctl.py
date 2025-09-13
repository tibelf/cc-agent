#!/usr/bin/env python3
"""
TaskCtl - Command-line interface for managing Auto-Claude tasks
"""

import click
import json
import sys
import asyncio
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
@click.argument('command')
@click.option('--description', '-d', help='Task description')
@click.option('--type', 'task_type', 
              type=click.Choice(['lightweight', 'medium_context', 'heavy_context']),
              default='lightweight', help='Task type')
@click.option('--priority', '-p',
              type=click.Choice(['low', 'normal', 'high', 'urgent']),
              default='normal', help='Task priority')
@click.option('--working-dir', help='Working directory for task')
@click.option('--env', multiple=True, help='Environment variables (KEY=VALUE)')
@click.option('--tag', multiple=True, help='Tags for task')
@click.option('--skip-security-scan', is_flag=True, help='Skip security scan')
def create(name, command, description, task_type, priority, working_dir, env, tag, skip_security_scan):
    """Create a new task"""
    try:
        task_manager = TaskManager()
        
        # Parse environment variables
        environment = {}
        for env_var in env:
            if '=' in env_var:
                key, value = env_var.split('=', 1)
                environment[key] = value
            else:
                click.echo(f"Warning: Ignoring invalid environment variable: {env_var}")
        
        # Create task
        task = task_manager.create_task(
            name=name,
            command=command,
            description=description,
            task_type=TaskType(task_type),
            working_dir=working_dir,
            environment=environment,
            tags=list(tag)
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
        
        click.echo(f"✅ Task created: {task.id}")
        click.echo(f"   Name: {task.name}")
        click.echo(f"   Command: {task.command}")
        click.echo(f"   Priority: {task.priority.value}")
        click.echo(f"   Type: {task.task_type.value}")
        
    except Exception as e:
        click.echo(f"❌ Error creating task: {e}", err=True)
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
            
            click.echo(f"{'ID':<12} {'Name':<20} {'State':<15} {'Priority':<8} {'Created':<12}")
            click.echo("-" * 75)
            
            for task in tasks:
                created_str = task.created_at.strftime('%Y-%m-%d')
                click.echo(f"{task.id[:12]:<12} {task.name[:20]:<20} {task.task_state.value:<15} {task.priority.value:<8} {created_str:<12}")
        
    except Exception as e:
        click.echo(f"❌ Error listing tasks: {e}", err=True)
        sys.exit(1)


@task.command()
@click.argument('task_id')
@click.option('--show-logs', is_flag=True, help='Show task logs')
def show(task_id, show_logs):
    """Show detailed task information"""
    try:
        task = db.get_task(task_id)
        if not task:
            click.echo(f"❌ Task not found: {task_id}")
            sys.exit(1)
        
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
        monitoring = MonitoringService()
        status = monitoring.get_health_status()
        
        click.echo("System Status")
        click.echo("=" * 50)
        click.echo(f"Overall Status: {status['status']}")
        click.echo(f"Timestamp: {status['timestamp']}")
        
        if 'metrics' in status:
            metrics = status['metrics']
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
        
        if 'alerts' in status:
            alerts = status['alerts']
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


if __name__ == '__main__':
    cli()