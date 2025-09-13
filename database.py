import sqlite3
import asyncio
import threading
from typing import List, Optional, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager
from config.config import config
from models import Task, WorkerStatus, Alert


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(config.db_path)
        self._local = threading.local()
        self.init_db()
    
    def get_connection(self):
        """Get thread-local database connection"""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(
                self.db_path, 
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn
    
    def init_db(self):
        """Initialize database schema"""
        conn = self.get_connection()
        
        # Tasks table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Idempotency ledger
        conn.execute('''
            CREATE TABLE IF NOT EXISTS idempotency_ledger (
                key TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                result TEXT
            )
        ''')
        
        # Worker status
        conn.execute('''
            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Alerts
        conn.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
        ''')
        
        # Recovery snapshots
        conn.execute('''
            CREATE TABLE IF NOT EXISTS recovery_snapshots (
                task_id TEXT,
                snapshot_id TEXT,
                data BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, snapshot_id)
            )
        ''')
        
        conn.commit()
    
    def save_task(self, task: Task):
        """Save or update task"""
        conn = self.get_connection()
        conn.execute('''
            INSERT OR REPLACE INTO tasks (id, data, updated_at) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (task.id, task.model_dump_json()))
        conn.commit()
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        conn = self.get_connection()
        row = conn.execute(
            'SELECT data FROM tasks WHERE id = ?', 
            (task_id,)
        ).fetchone()
        
        if row:
            return Task.model_validate_json(row['data'])
        return None
    
    def get_tasks_by_state(self, states: List[str]) -> List[Task]:
        """Get tasks by state"""
        if not states:
            return []
            
        conn = self.get_connection()
        placeholders = ','.join('?' * len(states))
        rows = conn.execute(f'''
            SELECT data FROM tasks 
            WHERE json_extract(data, '$.task_state') IN ({placeholders})
            ORDER BY json_extract(data, '$.created_at')
        ''', states).fetchall()
        
        return [Task.model_validate_json(row['data']) for row in rows]
    
    def get_pending_tasks(self, limit: int = 10) -> List[Task]:
        """Get pending tasks ordered by priority and creation time"""
        conn = self.get_connection()
        rows = conn.execute('''
            SELECT data FROM tasks 
            WHERE json_extract(data, '$.task_state') = 'pending'
            AND (json_extract(data, '$.next_allowed_at') IS NULL 
                 OR datetime(json_extract(data, '$.next_allowed_at')) <= datetime('now'))
            ORDER BY 
                CASE json_extract(data, '$.priority')
                    WHEN 'urgent' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'normal' THEN 3
                    WHEN 'low' THEN 4
                END,
                json_extract(data, '$.created_at')
            LIMIT ?
        ''', (limit,)).fetchall()
        
        return [Task.model_validate_json(row['data']) for row in rows]
    
    def check_idempotency(self, key: str) -> Optional[Dict[str, Any]]:
        """Check if operation was already executed"""
        conn = self.get_connection()
        row = conn.execute('''
            SELECT task_id, executed_at, result 
            FROM idempotency_ledger 
            WHERE key = ?
        ''', (key,)).fetchone()
        
        if row:
            return {
                'task_id': row['task_id'],
                'executed_at': row['executed_at'],
                'result': row['result']
            }
        return None
    
    def mark_idempotent_operation(self, key: str, task_id: str, result: str = None):
        """Mark operation as executed"""
        conn = self.get_connection()
        conn.execute('''
            INSERT OR REPLACE INTO idempotency_ledger (key, task_id, result)
            VALUES (?, ?, ?)
        ''', (key, task_id, result))
        conn.commit()
    
    def save_worker_status(self, worker: WorkerStatus):
        """Save worker status"""
        conn = self.get_connection()
        conn.execute('''
            INSERT OR REPLACE INTO workers (worker_id, data, last_heartbeat)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (worker.worker_id, worker.model_dump_json()))
        conn.commit()
    
    def get_active_workers(self, max_age_seconds: int = 120) -> List[WorkerStatus]:
        """Get workers that have sent heartbeat recently"""
        conn = self.get_connection()
        rows = conn.execute('''
            SELECT data FROM workers
            WHERE last_heartbeat > datetime('now', '-{} seconds')
        '''.format(max_age_seconds)).fetchall()
        
        return [WorkerStatus.model_validate_json(row['data']) for row in rows]
    
    def save_alert(self, alert: Alert):
        """Save alert"""
        conn = self.get_connection()
        conn.execute('''
            INSERT OR REPLACE INTO alerts (id, data, resolved_at)
            VALUES (?, ?, ?)
        ''', (alert.id, alert.model_dump_json(), 
              alert.resolved_at.isoformat() if alert.resolved_at else None))
        conn.commit()
    
    def get_unresolved_alerts(self) -> List[Alert]:
        """Get unresolved alerts"""
        conn = self.get_connection()
        rows = conn.execute('''
            SELECT data FROM alerts 
            WHERE resolved_at IS NULL
            ORDER BY created_at DESC
        ''').fetchall()
        
        return [Alert.model_validate_json(row['data']) for row in rows]
    
    def save_recovery_snapshot(self, task_id: str, snapshot_id: str, data: bytes):
        """Save recovery snapshot"""
        conn = self.get_connection()
        conn.execute('''
            INSERT OR REPLACE INTO recovery_snapshots (task_id, snapshot_id, data)
            VALUES (?, ?, ?)
        ''', (task_id, snapshot_id, data))
        conn.commit()
    
    def get_recovery_snapshot(self, task_id: str, snapshot_id: str) -> Optional[bytes]:
        """Get recovery snapshot"""
        conn = self.get_connection()
        row = conn.execute('''
            SELECT data FROM recovery_snapshots
            WHERE task_id = ? AND snapshot_id = ?
        ''', (task_id, snapshot_id)).fetchone()
        
        return row['data'] if row else None
    
    def cleanup_old_data(self, days: int = 7):
        """Cleanup old data"""
        conn = self.get_connection()
        
        # Cleanup completed/failed tasks older than N days
        conn.execute('''
            DELETE FROM tasks 
            WHERE json_extract(data, '$.task_state') IN ('completed', 'failed')
            AND created_at < datetime('now', '-{} days')
        '''.format(days))
        
        # Cleanup old recovery snapshots
        conn.execute('''
            DELETE FROM recovery_snapshots 
            WHERE created_at < datetime('now', '-{} days')
        '''.format(days))
        
        # Cleanup resolved alerts
        conn.execute('''
            DELETE FROM alerts 
            WHERE resolved_at IS NOT NULL 
            AND resolved_at < datetime('now', '-{} days')
        '''.format(days))
        
        conn.commit()


# Global database instance
db = Database()