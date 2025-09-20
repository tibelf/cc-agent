# Auto-Claude: Claude Code 自动化任务执行系统

Auto-Claude 是一个全面的自动化系统，用于在远程VPS服务器上无人值守地运行Claude Code任务。它提供了优雅的中断恢复、自动速率限制处理、安全合规和全面监控功能。

## 🚀 核心特性

### 主要功能
- **无人值守执行**: 7x24小时自动运行任务，无需人工干预
- **优雅恢复**: 从检查点自动恢复中断的任务
- **速率限制管理**: 遇到限制时自动暂停，可用时自动恢复
- **多工作器支持**: 并行运行多个Claude会话
- **全面监控**: Prometheus指标和告警系统
- **智能命令生成**: 用户只需描述任务，系统自动生成Claude CLI命令

### 异常处理 (覆盖40+种场景)
- **服务限制**: 5小时会话限制、速率限制、配额超限
- **网络问题**: 连接失败、DNS问题、代理问题
- **系统资源**: 磁盘满、内存压力、文件权限错误
- **进程管理**: 挂起进程、工作器崩溃、孤立任务
- **安全防护**: 敏感数据检测、恶意命令阻止

### 安全与合规
- **敏感数据检测**: 自动识别并屏蔽凭据、API密钥、个人信息
- **命令分析**: 扫描命令中的安全风险
- **合规检查**: 策略违规检测
- **审计日志**: 完整的安全事件审计跟踪

### 监控与告警
- **Prometheus指标**: 系统、任务和工作器指标
- **多级告警**: P1/P2/P3告警与去重
- **健康监控**: 自动系统健康检查
- **恢复操作**: 常见问题的自动恢复

## 📋 系统要求

- Python 3.8+
- Claude Code CLI 已安装并配置
- SQLite (内置)
- 可选: Redis (用于多节点部署)

## 🛠 安装部署

### 1. 克隆与环境设置
```bash
git clone <repository-url>
cd auto-claude
pip install -r requirements.txt
```

### 2. 初始化系统
```bash
python taskctl.py init
```
**作用**: 创建必要的目录结构、初始化数据库、配置日志系统

### 3. 配置调整 (可选)
编辑 `config/config.py` 文件以调整系统参数

### 4. 启动系统
```bash
# 直接运行 (适用于开发和测试)
python auto_claude.py

# 或作为systemd服务运行 (适用于生产环境)
sudo systemctl start auto-claude
sudo systemctl enable auto-claude
```

## 📖 使用指南

### 任务管理

#### 创建任务 (核心功能)
```bash
# 基础用法 - 只需描述任务内容，系统会自动生成Claude命令 (默认使用heavy_context)
python taskctl.py task create "重构用户认证模块" \
  --description "提高代码安全性，重构认证模块使其更加安全和易维护"

# 指定任务类型和优先级
python taskctl.py task create "修复登录bug" \
  --description "解决用户在移动端无法登录的问题，检查认证流程" \
  --type medium_context \
  --priority high

# 轻量级任务 - 适用于简单的代码分析 (只读权限)
python taskctl.py task create "代码审查" \
  --description "审查utils.py文件的代码质量" \
  --type lightweight

# 重量级任务 - 适用于大型重构或复杂分析 (完全权限，默认选项)
python taskctl.py task create "性能优化" \
  --description "优化整个系统的数据库查询性能" \
  --priority urgent
```

**参数说明**:
- `name`: 任务名称 (必需) - 简短描述任务目标
- `--description`: 详细描述 (必需) - 告诉Claude具体要做什么
- `--type`: 任务类型 (可选，默认heavy_context)
  - `lightweight`: 轻量级任务，只读权限 (Read, Grep, Glob)
  - `medium_context`: 中等任务，读写权限 (Read, Write, Edit, Git)
  - `heavy_context`: 重量级任务，完全权限 (Read, Write, Edit, Bash, WebFetch)
- `--priority`: 优先级 (可选，默认normal)
  - `low`, `normal`, `high`, `urgent`

**自动生成的命令示例**:
用户输入: "重构认证模块"
系统生成: `claude -p "请帮我重构认证模块，提高代码安全性" --permission-mode acceptEdits --allowedTools "Read" "Write" "Edit"`

#### 查看任务列表
```bash
# 查看所有任务
python taskctl.py task list

# 查看特定状态的任务
python taskctl.py task list --state pending --state processing

# 查看高优先级任务
python taskctl.py task list --priority high

# 以JSON格式输出 (便于脚本处理)
python taskctl.py task list --format json
```

#### 监控任务进度
```bash
# 查看任务详情
python taskctl.py task show task_abc123

# 查看任务日志 (实时监控任务执行)
python taskctl.py task show task_abc123 --show-logs

# 查看任务输出文件
tail -f tasks/task_abc123/output.log
```

#### 任务控制
```bash
# 取消正在执行的任务
python taskctl.py task cancel task_abc123

# 重试失败的任务
python taskctl.py task retry task_abc123

# 暂停任务 (如果支持)
python taskctl.py task pause task_abc123

# 恢复暂停的任务
python taskctl.py task resume task_abc123
```

### 系统管理

#### 系统状态监控
```bash
# 查看系统总体状态
python taskctl.py system status
```
**显示内容**: 系统健康状态、资源使用情况、活跃任务数量、告警信息

#### 工作器管理
```bash
# 查看所有工作器状态
python taskctl.py worker list

# 重启特定工作器
python taskctl.py worker restart worker_00

# 查看工作器详细信息
python taskctl.py worker show worker_00
```
**作用**: 监控Claude Code执行进程，确保任务正常处理

#### 安全管理
```bash
# 扫描任务的安全风险
python taskctl.py security scan task_abc123

# 解除被安全系统阻止的任务
python taskctl.py security unblock task_abc123 "经安全团队审批"

# 查看安全报告
python taskctl.py security report

# 查看敏感数据检测日志
python taskctl.py security audit --days 7
```
**注意事项**: 
- 高风险命令会被自动阻止
- 包含敏感信息的任务需要人工审核
- 安全日志会记录所有检测事件

### 任务类型详解

#### Lightweight (轻量级)
- **适用场景**: 代码分析、简单查询、文档生成
- **特点**: 执行时间短，可以重新开始
- **权限**: 只读权限 (Read, Grep, Glob)
- **示例**: 
```bash
python taskctl.py task create "代码分析" \
  --description "分析main.py的代码结构" \
  --type lightweight
```

#### Medium Context (中等上下文)
- **适用场景**: 代码重构、bug修复、功能实现
- **特点**: 需要保持部分执行历史
- **权限**: 读写权限 (Read, Write, Edit, Git操作)
- **示例**: 
```bash
python taskctl.py task create "重构API" \
  --description "重构用户API接口，提高性能" \
  --type medium_context
```

#### Heavy Context (重上下文)
- **适用场景**: 大规模重构、系统级优化、复杂分析
- **特点**: 需要完整的执行历史和上下文
- **权限**: 完整权限 (包括网络访问、系统操作)
- **示例**: 
```bash
python taskctl.py task create "系统优化" \
  --description "优化整个系统架构，提升性能" \
  --type heavy_context \
  --priority urgent
```

## 🔧 系统配置

### 主要配置项 (`config/config.py`)

```python
# Claude CLI设置
claude_cli_timeout: int = 120        # 命令超时时间(秒)
claude_session_limit: int = 18000    # Claude会话限制(5小时)
max_output_size: int = 50 * 1024 * 1024  # 最大输出大小(50MB)

# 重试和退避策略
max_retries: int = 5                 # 最大重试次数
base_delay: float = 1.0              # 基础延迟时间
max_delay: float = 300.0             # 最大延迟时间
exponential_base: float = 2.0        # 指数退避基数

# 速率限制
default_unban_wait: int = 3600       # 默认等待时间(1小时)
rate_limit_backoff_multiplier: float = 1.5  # 退避倍数

# 系统资源
min_disk_space_gb: int = 5           # 最小磁盘空间要求
max_log_size_mb: int = 50            # 日志文件大小限制
max_log_files: int = 7               # 日志文件保留数量

# 监控设置
heartbeat_interval: int = 30         # 心跳间隔(秒)
health_check_interval: int = 60      # 健康检查间隔(秒)
metrics_port: int = 8000             # 监控端口

# 安全配置
sensitive_patterns: list = [         # 敏感数据检测模式
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # 邮箱
    r'\b1[3-9]\d{9}\b',              # 手机号
    r'sk-[a-zA-Z0-9]{48}',           # API密钥
    r'[A-Za-z0-9+/]{40}=?=?',        # base64令牌
]

# violation_keywords 已移除 - 不再进行违规关键词检测
```

### 配置调优建议

#### 开发环境
```python
# 更快的反馈，更多的调试信息
claude_cli_timeout = 60
max_retries = 3
heartbeat_interval = 15
```

#### 生产环境
```python
# 更稳定的设置，更多的容错
claude_cli_timeout = 300
max_retries = 10
default_unban_wait = 7200  # 2小时
```

#### 高负载环境
```python
# 更保守的资源使用
min_disk_space_gb = 20
max_output_size = 10 * 1024 * 1024  # 10MB
rate_limit_backoff_multiplier = 2.0
```

## 🚨 错误处理与故障恢复

### 自动恢复场景

| 错误类别 | 具体场景 | 恢复措施 | 预计恢复时间 |
|----------|----------|----------|--------------|
| **速率限制** | 5小时限制、配额超限、请求过多 | 暂停 → 等待 → 自动恢复 | 1-24小时 |
| **网络问题** | 连接断开、DNS失败、代理问题 | 指数退避重试 | 1-30分钟 |
| **系统资源** | 磁盘满、内存压力、文件冲突 | 清理 + 告警 | 5-60分钟 |
| **进程问题** | 工作器挂起、进程崩溃 | 终止 + 重启 | 1-5分钟 |
| **任务异常** | 孤立任务、处理卡住 | 重置为待处理 | 即时 |

### 任务状态机

任务遵循以下状态流转:
```
待处理(pending) → 处理中(processing) → 暂停(paused) → 等待解封(waiting_unban) → 重试中(retrying) → 完成(completed) | 失败(failed)
                                    ↓
                             需人工审核(needs_human_review)
```

### 恢复机制详解

#### 1. 轻量级任务恢复
- **策略**: 从头重新开始执行
- **数据保留**: 任务描述和上下文
- **适用**: 代码分析、简单查询
- **恢复时间**: < 1分钟

#### 2. 中等上下文任务恢复
- **策略**: 使用最后500行输出作为恢复补丁
- **数据保留**: 部分执行历史 + 当前状态
- **适用**: 代码重构、功能开发
- **恢复时间**: 1-5分钟

#### 3. 重上下文任务恢复
- **策略**: 分块处理，进度跟踪
- **数据保留**: 完整执行历史 + 检查点
- **适用**: 大规模重构、系统优化
- **恢复时间**: 5-30分钟

### 常见错误处理

#### Claude CLI相关错误
```bash
# 错误: command not found: claude
# 解决: 确保Claude Code CLI已正确安装
which claude
claude --version

# 错误: Permission denied
# 解决: 检查文件权限和工作目录
chmod +x taskctl.py
chown -R $USER:$GROUP ./
```

#### 系统资源错误
```bash
# 错误: 磁盘空间不足
# 解决: 清理旧日志和任务文件
python taskctl.py system cleanup --days 7

# 错误: 内存不足
# 解决: 调整任务并发数
# 编辑config.py: max_concurrent_tasks = 1
```

#### 网络连接错误
```bash
# 错误: Connection timeout
# 解决: 检查网络连接和代理设置
curl -I https://claude.ai
export https_proxy=your_proxy_url

# 错误: Rate limit exceeded
# 解决: 等待或检查API限制
python taskctl.py task list --state waiting_unban
```

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

### 常见问题诊断

#### 任务卡在处理中
```bash
# 1. 检查系统状态
python taskctl.py system status

# 2. 查看工作器状态
python taskctl.py worker list

# 3. 检查特定任务
python taskctl.py task show task_id --show-logs

# 4. 强制重启卡住的工作器
python taskctl.py worker restart worker_00
```

**可能原因**:
- Claude CLI进程挂起
- 网络连接超时
- 权限问题导致命令无法执行
- 系统资源不足

#### 速率限制问题
```bash
# 1. 查看等待解封的任务
python taskctl.py task list --state waiting_unban

# 2. 检查速率限制日志
tail -f logs/auto_claude.log | grep "rate limit"

# 3. 查看当前等待时间
python taskctl.py system status

# 4. 手动调整等待时间 (紧急情况)
# 编辑 config.py: default_unban_wait = 1800  # 30分钟
```

**注意事项**:
- 请勿频繁手动重试，这可能加重速率限制
- 系统会自动处理大部分速率限制情况
- 如持续出现，考虑降低任务并发数

#### 系统资源使用过高
```bash
# 1. 检查磁盘使用情况
df -h
du -sh logs/ tasks/ db/

# 2. 清理旧数据
python taskctl.py system cleanup --days 3

# 3. 查看内存使用
python taskctl.py system status
free -h

# 4. 检查监控指标
curl http://localhost:8000/metrics | grep auto_claude
```

**优化建议**:
- 定期清理超过7天的任务日志
- 限制单个任务的最大输出大小
- 在高负载时减少并发任务数

#### 安全系统阻止任务
```bash
# 1. 查看安全报告
python taskctl.py security report

# 2. 扫描特定任务
python taskctl.py security scan task_id

# 3. 查看阻止原因
python taskctl.py task show task_id

# 4. 人工审核后解除阻止
python taskctl.py security unblock task_id "经过安全审核，确认安全"
```

**安全检查项**:
- 命令是否包含敏感操作 (rm -rf, sudo等)
- 是否涉及敏感数据 (密码、密钥等)
- 是否违反安全策略

#### 性能问题诊断
```bash
# 1. 查看系统负载
python taskctl.py system status

# 2. 检查数据库性能
sqlite3 db/ledger.db ".timeout 5000" ".tables"

# 3. 查看日志文件大小
ls -lh logs/

# 4. 监控工作器性能
python taskctl.py worker list --detailed

# 5. 检查网络延迟
ping -c 5 claude.ai
```

### 日志文件说明

| 日志文件 | 作用 | 重要性 | 清理频率 |
|----------|------|--------|----------|
| `logs/auto_claude.log` | 主系统日志 | ⭐⭐⭐ | 每周 |
| `logs/alerts.jsonl` | 告警事件 | ⭐⭐ | 每月 |
| `logs/security_audit.log` | 安全事件 | ⭐⭐⭐ | 长期保留 |
| `tasks/*/output.log` | 任务执行日志 | ⭐⭐ | 任务完成后7天 |
| `queue/*/` | 任务队列文件 | ⭐ | 自动清理 |

### 应急响应流程

#### 1. 系统完全无响应
```bash
# Step 1: 检查进程
ps aux | grep auto_claude

# Step 2: 强制重启
killall python
python auto_claude.py

# Step 3: 检查数据完整性
python taskctl.py system status
```

#### 2. 大量任务失败
```bash
# Step 1: 停止接收新任务
touch .maintenance

# Step 2: 分析失败原因
python taskctl.py task list --state failed --limit 20

# Step 3: 批量重试
for task in $(python taskctl.py task list --state failed --format json | jq -r '.[].id'); do
    python taskctl.py task retry $task
done
```

#### 3. 安全告警
```bash
# Step 1: 立即停止系统
killall python

# Step 2: 检查安全日志
tail -100 logs/security_audit.log

# Step 3: 隔离可疑任务
python taskctl.py security report --severity high

# Step 4: 人工审核后重启
# 审核完成后再启动系统
```

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

## 🚀 快速开始指南

### 5分钟上手
```bash
# 1. 安装系统
git clone <repository-url> && cd auto-claude
pip install -r requirements.txt

# 2. 初始化
python taskctl.py init

# 3. 启动系统
python auto_claude.py &

# 4. 创建第一个任务
python taskctl.py task create "代码审查" \
  --description "审查main.py文件的代码质量和潜在问题"

# 5. 查看任务状态
python taskctl.py task list
```

### 典型使用场景

#### 场景1: 日常代码维护
```bash
# 代码重构
python taskctl.py task create "重构API接口" \
  --description "重构user_api.py，提高代码可读性和性能" \
  --type medium_context

# Bug修复  
python taskctl.py task create "修复登录bug" \
  --description "解决用户登录时出现的500错误" \
  --priority high

# 代码审查
python taskctl.py task create "代码审查" \
  --description "审查新提交的payment模块代码" \
  --type lightweight
```

#### 场景2: 大规模重构项目
```bash
# 系统架构优化
python taskctl.py task create "架构重构" \
  --description "重构整个微服务架构，提升系统扩展性" \
  --type heavy_context \
  --priority urgent

# 数据库优化
python taskctl.py task create "数据库优化" \
  --description "优化所有SQL查询，提升数据库性能" \
  --type heavy_context
```

#### 场景3: 自动化运维
```bash
# 性能分析
python taskctl.py task create "性能分析" \
  --description "分析系统瓶颈，生成性能优化建议" \
  --type medium_context

# 安全审计
python taskctl.py task create "安全审计" \
  --description "检查代码中的安全漏洞和风险点" \
  --type medium_context \
  --priority high
```

## 💡 最佳实践

### 任务描述编写技巧
1. **具体明确**: "重构user_api.py的认证模块" 比 "重构代码" 更好
2. **包含目标**: "提高性能" "增强安全性" "提升可读性"
3. **提供上下文**: "针对移动端用户" "考虑高并发场景"

### 系统运维建议
1. **定期监控**: 每天检查 `python taskctl.py system status`
2. **日志清理**: 每周运行 `python taskctl.py system cleanup --days 7`
3. **备份数据**: 定期备份 `db/ledger.db` 文件
4. **更新系统**: 关注新版本更新和安全补丁

### 安全注意事项
1. **敏感操作**: 避免在任务中包含删除、格式化等危险操作
2. **权限控制**: 合理设置文件和目录权限
3. **网络安全**: 在生产环境中使用防火墙和VPN
4. **审计日志**: 定期检查 `logs/security_audit.log`

## 🆘 技术支持

- **问题报告**: [GitHub Issues](https://github.com/your-repo/auto-claude/issues)
- **功能讨论**: [GitHub Discussions](https://github.com/your-repo/auto-claude/discussions)
- **安全问题**: 发送邮件至 security@example.com
- **使用交流**: 加入官方QQ群/微信群

## 🗺 发展路线图

### v1.1 (下一版本)
- [ ] Web管理界面
- [ ] Docker容器化部署
- [ ] 定时任务调度 (类似cron)
- [ ] 任务模板和工作流
- [ ] 更智能的错误恢复

### v1.2 (未来计划)
- [ ] 多节点集群部署
- [ ] 插件系统扩展
- [ ] 高级数据分析
- [ ] CI/CD系统集成
- [ ] 移动端监控应用

### v2.0 (长期愿景)
- [ ] AI驱动的任务优化
- [ ] 自然语言任务创建
- [ ] 智能资源调度
- [ ] 预测性故障检测

---

## 📄 开源协议

本项目采用 MIT 协议开源 - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

感谢所有为 Auto-Claude 项目做出贡献的开发者和用户！

**Auto-Claude** - 让 Claude Code 自动化变得简单可靠 🚀

---

> 💡 **提示**: 如果您在使用过程中遇到任何问题，请先查看本文档的故障排除部分，大部分常见问题都能在这里找到解决方案。