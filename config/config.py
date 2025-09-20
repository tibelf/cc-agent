import os
from pathlib import Path
from typing import Dict, Any
import yaml
from pydantic import BaseModel, Field

class Config(BaseModel):
    # Directories
    base_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent)
    config_dir: Path = Field(default_factory=lambda: Path(__file__).parent)
    tasks_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "tasks")
    queue_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "queue")
    snapshots_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "snapshots")
    logs_dir: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "logs")
    db_path: Path = Field(default_factory=lambda: Path(__file__).parent.parent / "db" / "ledger.db")
    
    # Claude CLI settings
    claude_cli_timeout: int = 6000  # seconds before considering hung (100 minutes)
    claude_session_limit: int = 18000  # 5 hours in seconds
    max_output_size: int = 50 * 1024 * 1024  # 50MB
    
    # Retry and backoff
    max_retries: int = 5
    base_delay: float = 1.0
    max_delay: float = 300.0
    exponential_base: float = 2.0
    
    # Rate limiting
    default_unban_wait: int = 3600  # 1 hour default wait
    rate_limit_backoff_multiplier: float = 1.5
    
    # Resources
    min_disk_space_gb: int = 5
    max_log_size_mb: int = 50
    max_log_files: int = 7
    
    # Monitoring
    heartbeat_interval: int = 30
    health_check_interval: int = 60
    metrics_port: int = 8000
    
    # Security
    sensitive_patterns: list = Field(default_factory=lambda: [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # emails
        r'\b1[3-9]\d{9}\b',  # phone numbers
        r'sk-[a-zA-Z0-9]{48}',  # API keys
        r'[A-Za-z0-9+/]{40}=?=?',  # base64 tokens
    ])
    

    @classmethod
    def load(cls, config_path: str = None) -> 'Config':
        """Load configuration from file or use defaults"""
        if config_path and os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
            return cls(**config_data)
        return cls()

# Global config instance
config = Config.load()