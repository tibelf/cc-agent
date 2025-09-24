import subprocess
import re
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from pathlib import Path
import sys
import os

logger = logging.getLogger(__name__)


class ScheduledTask:
    """定时任务数据结构"""
    def __init__(self, task_id: str, name: str, description: str, 
                 cron_expr: str, task_type: str, working_dir: str = None, 
                 enabled: bool = True, created_at: datetime = None):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.cron_expr = cron_expr
        self.task_type = task_type
        self.working_dir = working_dir
        self.enabled = enabled
        self.created_at = created_at or datetime.utcnow()


class CrontabManager:
    """crontab 定时任务管理器"""
    
    PROJECT_PREFIX = "AUTO_CLAUDE_TASK"
    
    def __init__(self):
        self.python_path = sys.executable
        self.script_path = Path(__file__).parent / "taskctl.py"
        
    def validate_cron_expression(self, cron_expr: str) -> bool:
        """验证 cron 表达式格式"""
        # 简单的 cron 表达式验证 (分 时 日 月 周)
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
            
        patterns = [
            r'^(\*|([0-5]?\d)(,([0-5]?\d))*|([0-5]?\d)-([0-5]?\d))$',  # 分钟 0-59
            r'^(\*|([01]?\d|2[0-3])(,([01]?\d|2[0-3]))*|([01]?\d|2[0-3])-([01]?\d|2[0-3]))$',  # 小时 0-23
            r'^(\*|([12]?\d|3[01])(,([12]?\d|3[01]))*|([12]?\d|3[01])-([12]?\d|3[01]))$',  # 日 1-31
            r'^(\*|([1-9]|1[0-2])(,([1-9]|1[0-2]))*|([1-9]|1[0-2])-([1-9]|1[0-2]))$',  # 月 1-12
            r'^(\*|[0-7](,[0-7])*|[0-7]-[0-7])$'  # 周 0-7
        ]
        
        for i, part in enumerate(parts):
            if not re.match(patterns[i], part):
                return False
                
        return True
    
    def _generate_task_id(self, name: str) -> str:
        """生成任务ID"""
        import uuid
        # 使用名称和时间戳生成唯一ID
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        short_uuid = str(uuid.uuid4())[:8]
        clean_name = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]', '_', name)[:20]
        return f"{clean_name}_{timestamp}_{short_uuid}"
    
    def _get_current_crontab(self) -> str:
        """获取当前用户的 crontab 内容"""
        try:
            result = subprocess.run(['crontab', '-l'], 
                                  capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return result.stdout
            elif "no crontab for" in result.stderr:
                return ""  # 用户还没有 crontab
            else:
                raise RuntimeError(f"Failed to read crontab: {result.stderr}")
        except FileNotFoundError:
            raise RuntimeError("crontab command not found. This system might not support cron.")
    
    def _set_crontab(self, content: str) -> None:
        """设置 crontab 内容"""
        try:
            process = subprocess.run(['crontab', '-'], 
                                   input=content, text=True, 
                                   capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to set crontab: {e.stderr}")
    
    def _build_crontab_command(self, task: ScheduledTask) -> str:
        """构建 crontab 执行命令"""
        cmd_parts = [
            f'"{self.python_path}"',
            f'"{self.script_path}"',
            'task', 'create',
            f'"{task.name}"',
            '--description', f'"{task.description}"',
            '--type', task.task_type,
            '--skip-security-scan'
        ]
        
        if task.working_dir:
            cmd_parts.extend(['--working-dir', f'"{task.working_dir}"'])
            
        return ' '.join(cmd_parts)
    
    def add_scheduled_task(self, name: str, description: str, cron_expr: str, 
                          task_type: str = "heavy_context", working_dir: str = None) -> str:
        """添加定时任务"""
        # 验证 cron 表达式
        if not self.validate_cron_expression(cron_expr):
            raise ValueError(f"Invalid cron expression: {cron_expr}")
        
        # 生成任务ID
        task_id = self._generate_task_id(name)
        
        # 创建任务对象
        task = ScheduledTask(
            task_id=task_id,
            name=name,
            description=description,
            cron_expr=cron_expr,
            task_type=task_type,
            working_dir=working_dir
        )
        
        # 获取现有 crontab
        current_crontab = self._get_current_crontab()
        
        # 检查是否已有同名任务
        if self._find_task_in_crontab(current_crontab, name):
            raise ValueError(f"Scheduled task with name '{name}' already exists")
        
        # 构建新的 crontab 条目
        comment_line = f"# {self.PROJECT_PREFIX}:{task_id} - {name} (created: {task.created_at.strftime('%Y-%m-%d %H:%M:%S')})"
        command_line = f"{cron_expr} {self._build_crontab_command(task)}"
        
        # 添加到 crontab
        new_crontab = current_crontab.rstrip('\n') + '\n' if current_crontab else ''
        new_crontab += f"{comment_line}\n{command_line}\n"
        
        # 更新 crontab
        self._set_crontab(new_crontab)
        
        logger.info(f"Added scheduled task: {task_id} ({name})")
        return task_id
    
    def _find_task_in_crontab(self, crontab_content: str, name: str) -> Optional[str]:
        """在 crontab 中查找指定名称的任务"""
        lines = crontab_content.split('\n')
        for line in lines:
            if line.startswith(f"# {self.PROJECT_PREFIX}:") and f" - {name} " in line:
                # 提取任务ID
                match = re.search(rf"# {self.PROJECT_PREFIX}:([^\s]+)", line)
                if match:
                    return match.group(1)
        return None
    
    def list_scheduled_tasks(self) -> List[ScheduledTask]:
        """列出所有定时任务"""
        current_crontab = self._get_current_crontab()
        tasks = []
        lines = current_crontab.split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # 查找项目标识的注释行
            if line.startswith(f"# {self.PROJECT_PREFIX}:"):
                # 解析注释行
                comment_match = re.match(
                    rf"# {self.PROJECT_PREFIX}:([^\s]+) - (.+?) \(created: (.+?)\)",
                    line
                )
                
                if comment_match and i + 1 < len(lines):
                    task_id = comment_match.group(1)
                    name = comment_match.group(2)
                    created_str = comment_match.group(3)
                    
                    # 解析下一行的 cron 命令
                    next_line = lines[i + 1].strip()
                    if next_line:
                        # 检查任务是否被注释（禁用）
                        enabled = not next_line.startswith('#')
                        
                        # 去掉注释符号获取实际命令
                        command_line = next_line[1:].lstrip() if next_line.startswith('#') else next_line
                        
                        # 解析 cron 表达式和命令
                        parts = command_line.split(' ', 5)
                        if len(parts) >= 6:
                            cron_expr = ' '.join(parts[:5])
                            command = parts[5]
                            
                            # 从命令中提取任务信息
                            description = self._extract_description_from_command(command)
                            task_type = self._extract_task_type_from_command(command)
                            working_dir = self._extract_working_dir_from_command(command)
                            
                            # 解析创建时间
                            try:
                                created_at = datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                created_at = datetime.utcnow()
                            
                            task = ScheduledTask(
                                task_id=task_id,
                                name=name,
                                description=description,
                                cron_expr=cron_expr,
                                task_type=task_type,
                                working_dir=working_dir,
                                enabled=enabled,
                                created_at=created_at
                            )
                            tasks.append(task)
                    
                    i += 2  # 跳过注释行和命令行
                else:
                    i += 1
            else:
                i += 1
        
        return tasks
    
    def _extract_description_from_command(self, command: str) -> str:
        """从命令中提取描述"""
        match = re.search(r'--description\s+"([^"]+)"', command)
        return match.group(1) if match else ""
    
    def _extract_task_type_from_command(self, command: str) -> str:
        """从命令中提取任务类型"""
        match = re.search(r'--type\s+(\S+)', command)
        return match.group(1) if match else "heavy_context"
    
    def _extract_working_dir_from_command(self, command: str) -> Optional[str]:
        """从命令中提取工作目录"""
        match = re.search(r'--working-dir\s+"([^"]+)"', command)
        return match.group(1) if match else None
    
    def remove_scheduled_task(self, task_id: str) -> bool:
        """删除定时任务"""
        current_crontab = self._get_current_crontab()
        lines = current_crontab.split('\n')
        new_lines = []
        
        i = 0
        found = False
        while i < len(lines):
            line = lines[i]
            
            # 查找要删除的任务
            if line.startswith(f"# {self.PROJECT_PREFIX}:{task_id}"):
                found = True
                # 跳过注释行和下一行的命令行
                i += 2
                continue
            
            new_lines.append(line)
            i += 1
        
        if found:
            # 更新 crontab
            new_crontab = '\n'.join(new_lines).rstrip('\n')
            if new_crontab:
                new_crontab += '\n'
            self._set_crontab(new_crontab)
            logger.info(f"Removed scheduled task: {task_id}")
            return True
        
        return False
    
    def remove_scheduled_task_by_name(self, name: str) -> bool:
        """根据名称删除定时任务"""
        current_crontab = self._get_current_crontab()
        task_id = self._find_task_in_crontab(current_crontab, name)
        if task_id:
            return self.remove_scheduled_task(task_id)
        return False
    
    def enable_scheduled_task(self, task_id: str) -> bool:
        """启用定时任务"""
        return self._toggle_scheduled_task(task_id, enable=True)
    
    def disable_scheduled_task(self, task_id: str) -> bool:
        """禁用定时任务"""
        return self._toggle_scheduled_task(task_id, enable=False)
    
    def _toggle_scheduled_task(self, task_id: str, enable: bool) -> bool:
        """启用或禁用定时任务"""
        current_crontab = self._get_current_crontab()
        lines = current_crontab.split('\n')
        new_lines = []
        
        i = 0
        found = False
        while i < len(lines):
            line = lines[i]
            
            # 查找目标任务
            if line.startswith(f"# {self.PROJECT_PREFIX}:{task_id}"):
                found = True
                new_lines.append(line)  # 保留注释行
                
                # 处理下一行的命令行
                if i + 1 < len(lines):
                    command_line = lines[i + 1]
                    if enable:
                        # 启用：移除行首的 # 注释
                        if command_line.startswith('#'):
                            command_line = command_line[1:].lstrip()
                    else:
                        # 禁用：在行首添加 # 注释
                        if not command_line.startswith('#'):
                            command_line = f"#{command_line}"
                    
                    new_lines.append(command_line)
                    i += 2
                else:
                    i += 1
                continue
            
            new_lines.append(line)
            i += 1
        
        if found:
            # 更新 crontab
            new_crontab = '\n'.join(new_lines).rstrip('\n')
            if new_crontab:
                new_crontab += '\n'
            self._set_crontab(new_crontab)
            
            action = "enabled" if enable else "disabled"
            logger.info(f"Scheduled task {task_id} {action}")
            return True
        
        return False


# 全局实例
crontab_manager = CrontabManager()