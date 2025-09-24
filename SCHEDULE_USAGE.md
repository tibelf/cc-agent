# 定时任务功能使用指南

## 概述

基于 crontab 的定时任务功能已集成到 Auto-Claude 系统中。用户可以通过简单的命令创建、管理和监控定时任务。

## 功能特点

- ✅ 基于系统 crontab，稳定可靠
- ✅ 支持标准 cron 表达式
- ✅ 完整的任务生命周期管理
- ✅ 支持启用/禁用任务
- ✅ 自动命令生成和验证
- ✅ 与现有任务系统完全集成

## 命令使用

### 1. 创建定时任务

```bash
# 基础用法
python taskctl.py schedule add "任务名称" \
  --description "任务描述" \
  --cron "0 9 * * *"

# 完整参数
python taskctl.py schedule add "代码审查" \
  --description "每天上午9点执行代码审查" \
  --cron "0 9 * * *" \
  --type "heavy_context" \
  --working-dir "/path/to/project"
```

**参数说明：**
- `name`: 任务名称（必需）
- `--description, -d`: 详细描述（必需）
- `--cron, -c`: cron 表达式（必需）
- `--type`: 任务类型 (lightweight/medium_context/heavy_context，默认 heavy_context)
- `--working-dir`: 工作目录（可选）

### 2. 查看定时任务

```bash
# 表格格式
python taskctl.py schedule list

# JSON 格式
python taskctl.py schedule list --format json
```

### 3. 管理定时任务

```bash
# 禁用任务
python taskctl.py schedule disable <任务ID>

# 启用任务
python taskctl.py schedule enable <任务ID>

# 删除任务（通过ID）
python taskctl.py schedule remove <任务ID>

# 删除任务（通过名称）
python taskctl.py schedule remove-by-name "任务名称"
```

## Cron 表达式格式

```
分钟 小时 日 月 周
*    *   *  *  *
```

**常用示例：**
- `0 9 * * *` - 每天上午9点
- `0 9 * * 1-5` - 工作日上午9点
- `0 17 * * 5` - 每周五下午5点
- `*/30 * * * *` - 每30分钟
- `0 0 1 * *` - 每月1号午夜
- `0 9,17 * * 1-5` - 工作日上午9点和下午5点

## 实际使用示例

### 示例1: 每日代码审查
```bash
python taskctl.py schedule add "每日代码审查" \
  --description "审查昨天提交的代码，检查代码质量和潜在问题" \
  --cron "0 9 * * 1-5" \
  --type "medium_context"
```

### 示例2: 每周性能报告
```bash
python taskctl.py schedule add "周度性能分析" \
  --description "分析系统性能指标，生成周度性能报告" \
  --cron "0 17 * * 5" \
  --type "heavy_context"
```

### 示例3: 每小时监控任务
```bash
python taskctl.py schedule add "系统监控" \
  --description "检查系统状态和资源使用情况" \
  --cron "0 * * * *" \
  --type "lightweight"
```

## 工作原理

1. **任务创建**: 定时任务信息保存到系统 crontab 中
2. **调度执行**: cron 守护进程按时触发任务
3. **命令执行**: 执行 `taskctl.py task create` 命令创建普通任务
4. **任务处理**: 新创建的任务进入现有的任务处理流程
5. **监控恢复**: 复用现有的监控、错误恢复、安全检查机制

## 注意事项

### 权限要求
- 确保用户有操作 crontab 的权限
- 确保 Python 环境和脚本路径正确

### 任务重复
- 系统会检查任务名称重复
- 避免创建相同名称的定时任务

### 系统兼容性
- 支持 Linux 和 macOS 系统
- Windows 用户需要使用 WSL 或其他 cron 实现

### 任务标识
- 每个定时任务都有唯一的 task_id
- crontab 中使用注释标识项目管理的任务

## 故障排除

### 常见问题

**1. crontab 命令不存在**
```bash
# 检查 cron 服务
sudo systemctl status cron
# 或
which crontab
```

**2. 权限不足**
```bash
# 检查用户权限
ls -la /var/spool/cron/crontabs/
```

**3. 任务不执行**
```bash
# 检查 cron 日志
tail -f /var/log/cron.log
# 或
grep CRON /var/log/syslog
```

**4. 路径问题**
- 确保 Python 路径正确
- 确保脚本路径使用绝对路径

### 调试方法

```bash
# 1. 查看当前 crontab
crontab -l

# 2. 手动测试命令
python taskctl.py task create "测试" --description "测试任务"

# 3. 检查任务状态
python taskctl.py task list

# 4. 检查系统日志
tail -f logs/auto_claude.log
```

## 最佳实践

1. **合理设置执行时间**: 避免在系统繁忙时段执行重型任务
2. **使用描述性名称**: 便于识别和管理任务
3. **选择合适类型**: 根据任务复杂度选择 lightweight/medium_context/heavy_context
4. **定期清理**: 删除不再需要的定时任务
5. **监控执行**: 定期检查任务执行状态和日志

## 技术实现

- **crontab_manager.py**: 核心管理模块
- **taskctl.py**: CLI 命令接口
- **标识格式**: `# AUTO_CLAUDE_TASK:<task_id> - <name> (created: <timestamp>)`
- **命令模板**: 自动生成完整的 taskctl 命令

定时任务功能完全集成到现有架构中，无需额外的守护进程或数据库存储，简单可靠。