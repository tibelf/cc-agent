# Auto-Claude: Automated Task Execution System

Auto-Claude is a comprehensive automation system for running Claude Code tasks unattended on remote VPS servers. It provides graceful recovery from interruptions, automatic rate limit handling, security compliance, and comprehensive monitoring.

## 🚀 Features

### Core Capabilities
- **Unattended Execution**: Runs tasks 24/7 without human intervention
- **Graceful Recovery**: Automatically resumes interrupted tasks from checkpoints
- **Rate Limit Management**: Automatically pauses when hitting limits and resumes when available
- **Multi-worker Support**: Run multiple Claude sessions in parallel
- **Comprehensive Monitoring**: Prometheus metrics and alerting

### Exception Handling (40+ Scenarios Covered)
- **Service Limits**: 5-hour session limits, rate limits, quota exceeded
- **Network Issues**: Connection failures, DNS problems, proxy issues
- **System Resources**: Disk full, memory pressure, file permission errors
- **Process Management**: Hung processes, worker crashes, orphaned tasks
- **Security**: Sensitive data detection, malicious command blocking

### Security & Compliance
- **Sensitive Data Detection**: Automatically masks credentials, API keys, PII
- **Command Analysis**: Scans commands for security risks
- **Compliance Checking**: Policy violation detection
- **Audit Logging**: Complete audit trail for security events

### Monitoring & Alerting
- **Prometheus Metrics**: System, task, and worker metrics
- **Multi-level Alerts**: P1/P2/P3 alerting with deduplication
- **Health Monitoring**: Automatic system health checks
- **Recovery Actions**: Automated recovery from common issues

## 📋 Requirements

- Python 3.8+
- Claude Code CLI installed and configured
- SQLite (included)
- Optional: Redis for multi-node deployments

## 🛠 Installation

1. **Clone and setup**:
```bash
cd auto-claude
pip install -r requirements.txt
```

2. **Initialize system**:
```bash
python taskctl.py init
```

3. **Configure** (edit `config/config.py` if needed)

4. **Start system**:
```bash
# Direct run
python auto_claude.py

# Or as systemd service
sudo systemctl start auto-claude
sudo systemctl enable auto-claude
```

## 📖 Usage

### Task Management

**Create a task**:
```bash
./taskctl.py task create "My Task" "claude code --query 'Help me refactor this code'" \
  --description "Refactor legacy code" \
  --type medium_context \
  --priority high
```

**List tasks**:
```bash
./taskctl.py task list --state pending --state processing
```

**Monitor task**:
```bash
./taskctl.py task show task_abc123 --show-logs
```

**Cancel/retry tasks**:
```bash
./taskctl.py task cancel task_abc123
./taskctl.py task retry task_abc123
```

### System Management

**Check system status**:
```bash
./taskctl.py system status
```

**View workers**:
```bash
./taskctl.py worker list
```

**Security management**:
```bash
./taskctl.py security scan task_abc123
./taskctl.py security unblock task_abc123 "Approved by security team"
./taskctl.py security report
```

### Task Types

- **Lightweight**: Simple tasks that can restart from beginning
- **Medium Context**: Tasks needing partial history for recovery  
- **Heavy Context**: Large file/data tasks with chunked processing

## 🔧 Configuration

Key configuration options in `config/config.py`:

```python
# Rate limiting
claude_session_limit: int = 18000  # 5 hours
default_unban_wait: int = 3600     # 1 hour

# Resources
min_disk_space_gb: int = 5
max_log_size_mb: int = 50

# Workers
num_workers: int = 2

# Security
sensitive_patterns: list = [...]   # Regex patterns for sensitive data
violation_keywords: list = [...]   # Policy violation keywords
```

## 🚨 Error Handling & Recovery

### Automatic Recovery Scenarios

| Category | Scenarios | Recovery Action |
|----------|-----------|----------------|
| **Rate Limits** | 5-hour limit, quota exceeded, too many requests | Pause → Wait → Auto-resume |
| **Network** | Connection drops, DNS failures, proxy issues | Exponential backoff retry |
| **Resources** | Disk full, memory pressure, file conflicts | Cleanup + alert |
| **Processes** | Hung workers, crashed processes | Kill + restart |
| **Tasks** | Orphaned tasks, stuck processing | Reset to pending |

### State Machine

Tasks follow this state flow:
```
pending → processing → paused → waiting_unban → retrying → completed | failed
                    ↓
              needs_human_review
```

### Resume Mechanisms

1. **Lightweight tasks**: Restart from beginning with task context
2. **Medium context**: Resume using last 500 lines patch 
3. **Heavy context**: Chunked processing with progress tracking

## 📊 Monitoring

### Metrics (Prometheus)

- `auto_claude_task_runs_total{status}` - Task completion counts
- `auto_claude_worker_heartbeat_age_seconds{worker_id}` - Worker health
- `auto_claude_system_disk_free_bytes` - Disk space monitoring
- `auto_claude_queue_tasks_total{state}` - Queue depths

### Alerts

- **P1**: Business critical (disk full, service down)
- **P2**: Recoverable issues (high memory, stuck workers)  
- **P3**: Minor issues (task retries, rate limit warnings)

Access metrics at `http://localhost:8000/metrics`

## 🔒 Security Features

### Data Protection
- **Pattern Detection**: Emails, phone numbers, API keys, credit cards
- **Auto-masking**: `***1234` format for sensitive data
- **Audit Logging**: Complete security event trail

### Command Security
- **Risk Analysis**: Scans commands for dangerous operations
- **Blocking**: High-risk commands require human review
- **Compliance**: Policy violation detection

### Access Control
- **Sandboxing**: Tasks run in isolated environments
- **Resource Limits**: CPU, memory, and disk quotas
- **Network Restrictions**: Configurable network access

## 🧪 Testing

Run the test suite:
```bash
python -m pytest tests/ -v
```

Test specific scenarios:
```bash
# Test rate limit handling
python tests/test_rate_limits.py

# Test recovery mechanisms  
python tests/test_recovery.py

# Test security scanning
python tests/test_security.py
```

## 📈 Scaling

### Single Node (Current)
- File-based task queue
- SQLite database
- Local monitoring

### Multi-Node (Future)
- Redis task queue  
- Shared database
- Distributed monitoring
- Load balancing

## 🔧 Troubleshooting

### Common Issues

**Tasks stuck in processing**:
```bash
./taskctl.py system status  # Check worker health
./taskctl.py worker list    # Find stuck workers
```

**Rate limit issues**:
```bash
./taskctl.py task list --state waiting_unban
# Check logs: tail -f logs/auto_claude.log | grep "rate limit"
```

**High resource usage**:
```bash
./taskctl.py system cleanup --days 3  # Clean old data
# Check metrics at localhost:8000/metrics
```

**Security blocks**:
```bash
./taskctl.py security report
./taskctl.py security scan task_id
./taskctl.py security unblock task_id "reason"
```

### Log Files

- `logs/auto_claude.log` - Main system log
- `logs/alerts.jsonl` - Alert events
- `logs/security_audit.log` - Security events
- `tasks/*/output.log` - Individual task output

## 📚 Architecture

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Task Manager│    │   Workers   │    │  Recovery   │
│             │◄──►│             │◄──►│   Manager   │
│ - Queue     │    │ - Claude CLI│    │ - Health    │
│ - States    │    │ - Monitoring│    │ - Actions   │
└─────────────┘    └─────────────┘    └─────────────┘
       ▲                   ▲                   ▲
       │                   │                   │
       ▼                   ▼                   ▼
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Rate Limit  │    │ Monitoring  │    │  Security   │
│  Manager    │    │  Service    │    │   Manager   │
│             │    │             │    │             │
│ - Probing   │    │ - Metrics   │    │ - Scanning  │
│ - Waiting   │    │ - Alerts    │    │ - Compliance│
└─────────────┘    └─────────────┘    └─────────────┘
```

## 🤝 Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open Pull Request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

- **Issues**: GitHub Issues for bug reports
- **Discussions**: GitHub Discussions for questions
- **Security**: Email security issues to security@example.com

## 🗺 Roadmap

### v1.1 (Next Release)
- [ ] Web dashboard for task management
- [ ] Docker containerization
- [ ] Advanced scheduling (cron-like)
- [ ] Task templates and workflows

### v1.2 (Future)
- [ ] Multi-node deployment
- [ ] Plugin system
- [ ] Advanced analytics
- [ ] Integration with CI/CD systems

---

**Auto-Claude** - Making Claude Code automation effortless and reliable. 🚀