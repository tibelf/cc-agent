import asyncio
import logging
import time
import json
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from prometheus_client import start_http_server, Counter, Gauge, Histogram, CollectorRegistry
import psutil

from models import Task, TaskState, WorkerStatus, Alert, AlertLevel, SystemMetrics
from database import db
from config.config import config
from utils import get_system_metrics, create_alert


logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    name: str
    condition: str
    threshold: float
    duration_seconds: int
    level: AlertLevel
    message_template: str
    cooldown_seconds: int = 3600  # 1 hour default


class MetricsCollector:
    """Collect and export Prometheus metrics"""
    
    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()
        
        # Task metrics
        self.task_runs_total = Counter(
            'auto_claude_task_runs_total',
            'Total number of task runs by status',
            ['status'],
            registry=self.registry
        )
        
        self.task_retry_total = Counter(
            'auto_claude_task_retry_total', 
            'Total number of task retries by reason',
            ['reason'],
            registry=self.registry
        )
        
        self.task_duration_seconds = Histogram(
            'auto_claude_task_duration_seconds',
            'Task execution duration in seconds',
            ['task_type', 'status'],
            registry=self.registry
        )
        
        # Worker metrics
        self.worker_heartbeat_age_seconds = Gauge(
            'auto_claude_worker_heartbeat_age_seconds',
            'Age of worker heartbeat in seconds',
            ['worker_id'],
            registry=self.registry
        )
        
        self.worker_cpu_usage_percent = Gauge(
            'auto_claude_worker_cpu_usage_percent',
            'Worker CPU usage percentage',
            ['worker_id'],
            registry=self.registry
        )
        
        self.worker_memory_usage_bytes = Gauge(
            'auto_claude_worker_memory_usage_bytes',
            'Worker memory usage in bytes',
            ['worker_id'],
            registry=self.registry
        )
        
        # System metrics
        self.system_disk_free_bytes = Gauge(
            'auto_claude_system_disk_free_bytes',
            'Free disk space in bytes',
            ['mount'],
            registry=self.registry
        )
        
        self.system_memory_usage_percent = Gauge(
            'auto_claude_system_memory_usage_percent',
            'System memory usage percentage',
            registry=self.registry
        )
        
        self.system_cpu_usage_percent = Gauge(
            'auto_claude_system_cpu_usage_percent',
            'System CPU usage percentage',
            registry=self.registry
        )
        
        # Task queue metrics
        self.queue_tasks_total = Gauge(
            'auto_claude_queue_tasks_total',
            'Number of tasks in each state',
            ['state'],
            registry=self.registry
        )
        
        # Alert metrics
        self.alerts_total = Counter(
            'auto_claude_alerts_total',
            'Total number of alerts by level',
            ['level'],
            registry=self.registry
        )
        
        # Rate limit metrics
        self.rate_limits_total = Counter(
            'auto_claude_rate_limits_total',
            'Total number of rate limits encountered',
            ['limit_type'],
            registry=self.registry
        )
    
    def record_task_completion(self, task: Task):
        """Record task completion metrics"""
        self.task_runs_total.labels(status=task.task_state.value).inc()
        
        if task.started_at and task.completed_at:
            duration = (task.completed_at - task.started_at).total_seconds()
            self.task_duration_seconds.labels(
                task_type=task.task_type.value,
                status=task.task_state.value
            ).observe(duration)
    
    def record_task_retry(self, task: Task, reason: str):
        """Record task retry metrics"""
        self.task_retry_total.labels(reason=reason).inc()
    
    def record_rate_limit(self, limit_type: str):
        """Record rate limit metrics"""
        self.rate_limits_total.labels(limit_type=limit_type).inc()
    
    def record_alert(self, alert: Alert):
        """Record alert metrics"""
        self.alerts_total.labels(level=alert.level.value).inc()
    
    def update_worker_metrics(self, workers: List[WorkerStatus]):
        """Update worker metrics"""
        current_time = datetime.utcnow()
        
        for worker in workers:
            # Heartbeat age
            if worker.last_heartbeat:
                age_seconds = (current_time - worker.last_heartbeat).total_seconds()
                self.worker_heartbeat_age_seconds.labels(
                    worker_id=worker.worker_id
                ).set(age_seconds)
            
            # CPU usage
            if worker.cpu_usage is not None:
                self.worker_cpu_usage_percent.labels(
                    worker_id=worker.worker_id
                ).set(worker.cpu_usage)
            
            # Memory usage
            if worker.memory_usage is not None:
                self.worker_memory_usage_bytes.labels(
                    worker_id=worker.worker_id
                ).set(worker.memory_usage)
    
    def update_system_metrics(self):
        """Update system metrics"""
        try:
            metrics = get_system_metrics()
            
            # Disk space (convert GB to bytes)
            self.system_disk_free_bytes.labels(mount="/").set(
                metrics.disk_free_gb * 1024 * 1024 * 1024
            )
            
            # Memory and CPU
            self.system_memory_usage_percent.set(metrics.memory_usage_percent)
            self.system_cpu_usage_percent.set(metrics.cpu_usage_percent)
            
            # Task queue states
            for state in TaskState:
                count = len(db.get_tasks_by_state([state.value]))
                self.queue_tasks_total.labels(state=state.value).set(count)
            
        except Exception as e:
            logger.error(f"Error updating system metrics: {e}")


class AlertManager:
    """Manage alerts with deduplication and escalation"""
    
    def __init__(self):
        self.alert_history: Dict[str, datetime] = {}
        self.suppressed_alerts: Set[str] = set()
        self.escalation_rules: List[AlertRule] = []
        self._load_alert_rules()
    
    def _load_alert_rules(self):
        """Load alert rules from configuration"""
        # Define default alert rules
        self.escalation_rules = [
            AlertRule(
                name="high_disk_usage",
                condition="disk_free_gb < 5",
                threshold=5.0,
                duration_seconds=300,
                level=AlertLevel.P1,
                message_template="Critical: Only {disk_free_gb:.1f}GB disk space remaining",
                cooldown_seconds=3600
            ),
            AlertRule(
                name="high_memory_usage", 
                condition="memory_usage_percent > 90",
                threshold=90.0,
                duration_seconds=600,
                level=AlertLevel.P2,
                message_template="High memory usage: {memory_usage_percent:.1f}%",
                cooldown_seconds=1800
            ),
            AlertRule(
                name="worker_heartbeat_stale",
                condition="worker_heartbeat_age > 300",
                threshold=300.0,
                duration_seconds=0,  # Immediate
                level=AlertLevel.P2,
                message_template="Worker {worker_id} heartbeat stale for {age} seconds",
                cooldown_seconds=1800
            ),
            AlertRule(
                name="many_failed_tasks",
                condition="failed_tasks > 10",
                threshold=10.0,
                duration_seconds=300,
                level=AlertLevel.P2,
                message_template="Many tasks failing: {failed_tasks} failed tasks",
                cooldown_seconds=3600
            ),
            AlertRule(
                name="rate_limit_frequency",
                condition="rate_limits_per_hour > 5",
                threshold=5.0,
                duration_seconds=0,
                level=AlertLevel.P1,
                message_template="Frequent rate limiting: {rate_limits_per_hour} in last hour",
                cooldown_seconds=7200
            )
        ]
    
    def should_send_alert(self, alert_key: str, cooldown_seconds: int) -> bool:
        """Check if alert should be sent based on cooldown"""
        if alert_key in self.suppressed_alerts:
            return False
        
        last_sent = self.alert_history.get(alert_key)
        if last_sent:
            time_since = (datetime.utcnow() - last_sent).total_seconds()
            if time_since < cooldown_seconds:
                return False
        
        return True
    
    def send_alert(self, alert: Alert) -> bool:
        """Send alert if not suppressed by cooldown"""
        alert_key = f"{alert.level.value}:{alert.title}"
        
        # Find matching rule for cooldown
        cooldown_seconds = 3600  # Default 1 hour
        for rule in self.escalation_rules:
            if rule.name.lower() in alert.title.lower():
                cooldown_seconds = rule.cooldown_seconds
                break
        
        if not self.should_send_alert(alert_key, cooldown_seconds):
            logger.debug(f"Alert suppressed by cooldown: {alert.title}")
            return False
        
        # Record alert sending time
        self.alert_history[alert_key] = datetime.utcnow()
        
        # Send alert (implement actual notification logic here)
        self._deliver_alert(alert)
        
        return True
    
    def _deliver_alert(self, alert: Alert):
        """Deliver alert through configured channels"""
        # Log the alert
        log_level = logging.ERROR if alert.level == AlertLevel.P1 else logging.WARNING
        logger.log(log_level, f"[{alert.level.value}] {alert.title}: {alert.message}")
        
        # Save to database
        db.save_alert(alert)
        
        # Here you could integrate with:
        # - Email notifications
        # - Slack/Discord webhooks
        # - PagerDuty
        # - SMS alerts
        # etc.
        
        # For now, just write to alert file
        self._write_alert_to_file(alert)
    
    def _write_alert_to_file(self, alert: Alert):
        """Write alert to file for external processing"""
        alerts_file = config.logs_dir / "alerts.jsonl"
        config.logs_dir.mkdir(exist_ok=True)
        
        try:
            alert_data = {
                "timestamp": alert.created_at.isoformat(),
                "level": alert.level.value,
                "title": alert.title,
                "message": alert.message,
                "task_id": alert.task_id,
                "worker_id": alert.worker_id,
                "metadata": alert.metadata
            }
            
            with open(alerts_file, 'a') as f:
                f.write(json.dumps(alert_data) + '\n')
                
        except Exception as e:
            logger.error(f"Failed to write alert to file: {e}")
    
    def suppress_alert(self, pattern: str):
        """Suppress alerts matching pattern"""
        self.suppressed_alerts.add(pattern)
    
    def unsuppress_alert(self, pattern: str):
        """Remove alert suppression"""
        self.suppressed_alerts.discard(pattern)
    
    def cleanup_old_history(self, max_age_hours: int = 24):
        """Clean up old alert history"""
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        self.alert_history = {
            k: v for k, v in self.alert_history.items()
            if v > cutoff_time
        }


class MonitoringService:
    """Main monitoring service that coordinates metrics and alerts"""
    
    def __init__(self):
        self.metrics_collector = MetricsCollector()
        self.alert_manager = AlertManager()
        self.running = False
        self.http_server = None
        
        # Rate limit tracking for alerting
        self.rate_limit_events: List[datetime] = []
    
    async def start(self):
        """Start the monitoring service"""
        self.running = True
        
        # Start Prometheus HTTP server
        try:
            self.http_server = start_http_server(
                config.metrics_port,
                registry=self.metrics_collector.registry
            )
            logger.info(f"Metrics server started on port {config.metrics_port}")
        except Exception as e:
            logger.error(f"Failed to start metrics server: {e}")
        
        # Start monitoring tasks
        await asyncio.gather(
            self._collect_metrics_loop(),
            self._check_alert_rules(),
            self._cleanup_loop()
        )
    
    async def stop(self):
        """Stop the monitoring service"""
        self.running = False
        
        if self.http_server:
            self.http_server.shutdown()
        
        logger.info("Monitoring service stopped")
    
    async def _collect_metrics_loop(self):
        """Continuously collect metrics"""
        while self.running:
            try:
                # Update system metrics
                self.metrics_collector.update_system_metrics()
                
                # Update worker metrics
                active_workers = db.get_active_workers()
                self.metrics_collector.update_worker_metrics(active_workers)
                
                await asyncio.sleep(30)  # Collect every 30 seconds
                
            except Exception as e:
                logger.error(f"Metrics collection error: {e}")
                await asyncio.sleep(30)
    
    async def _check_alert_rules(self):
        """Check alert rules and trigger alerts"""
        while self.running:
            try:
                await self._evaluate_alert_rules()
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Alert rule checking error: {e}")
                await asyncio.sleep(60)
    
    async def _evaluate_alert_rules(self):
        """Evaluate all alert rules"""
        try:
            # Get current system state
            metrics = get_system_metrics()
            active_workers = db.get_active_workers()
            
            # Check disk space
            if metrics.disk_free_gb < 5:
                alert = create_alert(
                    level=AlertLevel.P1,
                    title="Critical disk space",
                    message=f"Only {metrics.disk_free_gb:.1f}GB disk space remaining"
                )
                self.alert_manager.send_alert(alert)
            
            # Check memory usage
            if metrics.memory_usage_percent > 90:
                alert = create_alert(
                    level=AlertLevel.P2,
                    title="High memory usage",
                    message=f"Memory usage at {metrics.memory_usage_percent:.1f}%"
                )
                self.alert_manager.send_alert(alert)
            
            # Check worker heartbeats
            current_time = datetime.utcnow()
            for worker in active_workers:
                if worker.last_heartbeat:
                    age_seconds = (current_time - worker.last_heartbeat).total_seconds()
                    if age_seconds > 300:  # 5 minutes
                        alert = create_alert(
                            level=AlertLevel.P2,
                            title=f"Worker {worker.worker_id} heartbeat stale",
                            message=f"No heartbeat for {age_seconds:.0f} seconds",
                            worker_id=worker.worker_id
                        )
                        self.alert_manager.send_alert(alert)
            
            # Check failed tasks
            if metrics.failed_tasks > 10:
                alert = create_alert(
                    level=AlertLevel.P2,
                    title="Many failed tasks",
                    message=f"{metrics.failed_tasks} tasks in failed state"
                )
                self.alert_manager.send_alert(alert)
            
            # Check rate limit frequency
            recent_rate_limits = self._count_recent_rate_limits()
            if recent_rate_limits > 5:
                alert = create_alert(
                    level=AlertLevel.P1,
                    title="Frequent rate limiting",
                    message=f"{recent_rate_limits} rate limits in the last hour"
                )
                self.alert_manager.send_alert(alert)
            
        except Exception as e:
            logger.error(f"Error evaluating alert rules: {e}")
    
    def _count_recent_rate_limits(self) -> int:
        """Count rate limit events in the last hour"""
        cutoff_time = datetime.utcnow() - timedelta(hours=1)
        self.rate_limit_events = [
            event for event in self.rate_limit_events
            if event > cutoff_time
        ]
        return len(self.rate_limit_events)
    
    def record_rate_limit_event(self):
        """Record a rate limit event for alerting"""
        self.rate_limit_events.append(datetime.utcnow())
        self.metrics_collector.record_rate_limit("general")
    
    async def _cleanup_loop(self):
        """Periodic cleanup of monitoring data"""
        while self.running:
            try:
                # Cleanup alert history
                self.alert_manager.cleanup_old_history()
                
                # Cleanup rate limit events
                cutoff_time = datetime.utcnow() - timedelta(hours=24)
                self.rate_limit_events = [
                    event for event in self.rate_limit_events
                    if event > cutoff_time
                ]
                
                await asyncio.sleep(3600)  # Cleanup every hour
                
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                await asyncio.sleep(3600)
    
    def get_health_status(self) -> Dict[str, any]:
        """Get current health status"""
        try:
            metrics = get_system_metrics()
            active_workers = db.get_active_workers()
            unresolved_alerts = db.get_unresolved_alerts()
            
            return {
                "status": "healthy" if len(unresolved_alerts) == 0 else "degraded",
                "timestamp": datetime.utcnow().isoformat(),
                "metrics": {
                    "disk_free_gb": metrics.disk_free_gb,
                    "memory_usage_percent": metrics.memory_usage_percent,
                    "cpu_usage_percent": metrics.cpu_usage_percent,
                    "active_workers": len(active_workers),
                    "pending_tasks": metrics.pending_tasks,
                    "processing_tasks": metrics.processing_tasks,
                    "failed_tasks": metrics.failed_tasks,
                    "completed_tasks": metrics.completed_tasks
                },
                "alerts": {
                    "unresolved_count": len(unresolved_alerts),
                    "recent_rate_limits": self._count_recent_rate_limits()
                }
            }
        except Exception as e:
            return {
                "status": "error",
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }