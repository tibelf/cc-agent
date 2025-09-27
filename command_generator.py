#!/usr/bin/env python3
"""
Command Generator for Auto-Claude
Converts task descriptions into proper Claude CLI commands
"""

import re
from typing import Dict, List, Optional
from enum import Enum
from models import TaskType


class TaskCategory(Enum):
    """Task categories for command generation"""
    REFACTOR = "refactor"
    DEBUG = "debug"
    REVIEW = "review"
    ANALYZE = "analyze"
    OPTIMIZE = "optimize"
    IMPLEMENT = "implement"
    TEST = "test"
    DOCUMENTATION = "documentation"
    GENERAL = "general"


class CommandGenerator:
    """Generates Claude CLI commands from task descriptions"""
    
    def __init__(self):
        # Keywords ordered by specificity - more specific keywords first
        self.category_keywords = {
            TaskCategory.DEBUG: ["修复", "debug", "fix", "解决", "bug", "错误", "故障"],
            TaskCategory.REFACTOR: ["重构", "refactor", "改进", "优化代码", "restructure", "改造"],
            TaskCategory.TEST: ["单元测试", "集成测试", "测试用例", "unit test", "integration test", "e2e"],
            TaskCategory.REVIEW: ["代码审查", "review", "检查代码", "code review", "验证", "validate"],
            TaskCategory.ANALYZE: ["代码分析", "analyze", "理解代码", "understand", "解释", "explain"],
            TaskCategory.OPTIMIZE: ["性能优化", "optimize", "性能", "performance", "速度", "memory"],
            TaskCategory.IMPLEMENT: ["实现功能", "implement", "开发", "develop", "新增", "添加功能"],
            TaskCategory.DOCUMENTATION: ["编写文档", "documentation", "注释", "comment", "说明文档"],
            TaskCategory.GENERAL: []
        }
        
        self.command_templates = {
            TaskCategory.REFACTOR: "请帮我重构{target}，{objective}",
            TaskCategory.DEBUG: "请帮我修复{problem}",
            TaskCategory.REVIEW: "请帮我审查{target}，重点关注{focus}",
            TaskCategory.ANALYZE: "请帮我分析{target}，{objective}",
            TaskCategory.OPTIMIZE: "请帮我优化{target}的{aspect}",
            TaskCategory.IMPLEMENT: "请帮我实现{feature}，{requirements}",
            TaskCategory.TEST: "请帮我为{target}编写{test_type}测试",
            TaskCategory.DOCUMENTATION: "请帮我为{target}编写{doc_type}",
            TaskCategory.GENERAL: "{task}"
        }
        
        # 自动化指令模板
        self.auto_execution_suffix = """

IMPORTANT: This is an automated task execution. Do not ask for confirmation or user input. 
If you have the necessary tools and permissions, execute the requested actions directly.
If you cannot complete the action due to missing tools or authentication, 
provide specific instructions for manual completion instead of asking for confirmation.

COMPLETION RULE: 
When ALL requested actions are successfully completed automatically, end your response with: "✅ TASK_COMPLETED"
ONLY use this marker if the task is 100% completed without requiring any manual steps.
DO NOT use this marker if you provide manual instructions, encounter errors, or cannot complete the task."""

        # 默认权限配置
        self.default_allowed_tools = [
            "Bash(git:*)",  # Git operations
            "Read",         # File reading
            "Write",        # File writing
            "Edit",         # File editing
            "Grep",         # Search operations
            "Glob"          # File pattern matching
        ]
        
        # MCP工具列表
        self.mcp_tools = [
            "mcp__rube__RUBE_SEARCH_TOOLS",
            "mcp__rube__RUBE_CREATE_PLAN", 
            "mcp__rube__RUBE_MULTI_EXECUTE_TOOL",
            "mcp__rube__RUBE_REMOTE_BASH_TOOL",
            "mcp__rube__RUBE_REMOTE_WORKBENCH",
            "mcp__rube__RUBE_MANAGE_CONNECTIONS",
            "mcp__browsermcp__browser_navigate",
            "mcp__browsermcp__browser_go_back",
            "mcp__browsermcp__browser_go_forward", 
            "mcp__browsermcp__browser_snapshot",
            "mcp__browsermcp__browser_click",
            "mcp__browsermcp__browser_hover",
            "mcp__browsermcp__browser_type",
            "mcp__browsermcp__browser_select_option",
            "mcp__browsermcp__browser_press_key",
            "mcp__browsermcp__browser_wait",
            "mcp__browsermcp__browser_get_console_logs",
            "mcp__browsermcp__browser_screenshot",
            "mcp__context7__resolve-library-id",
            "mcp__context7__get-library-docs"
        ]
        
        self.task_type_permissions = {
            TaskType.LIGHTWEIGHT: {
                "allowed_tools": ["Read", "Grep", "Glob"],
                "permission_mode": "acceptEdits"
            },
            TaskType.MEDIUM_CONTEXT: {
                "allowed_tools": ["Read", "Write", "Edit", "Grep", "Glob", "Bash(git:*)"] + self.mcp_tools,
                "permission_mode": "acceptEdits"
            },
            TaskType.HEAVY_CONTEXT: {
                "allowed_tools": ["Read", "Write", "Edit", "Grep", "Glob", "Bash", "WebFetch"] + self.mcp_tools,
                "permission_mode": "acceptEdits"
            }
        }
    
    def categorize_task(self, name: str, description: str) -> TaskCategory:
        """Categorize task based on keywords in name and description"""
        text = f"{name} {description}".lower()
        
        # Count keyword matches for each category
        category_scores = {}
        for category, keywords in self.category_keywords.items():
            if category == TaskCategory.GENERAL:
                continue
            
            score = sum(1 for keyword in keywords if keyword.lower() in text)
            if score > 0:
                category_scores[category] = score
        
        # Return category with highest score, or GENERAL if no matches
        if category_scores:
            return max(category_scores.items(), key=lambda x: x[1])[0]
        return TaskCategory.GENERAL
    
    def extract_task_components(self, name: str, description: str, category: TaskCategory) -> Dict[str, str]:
        """Extract components from task description for template filling"""
        # For GENERAL category, use description only. For specific categories, combine name and description
        if category == TaskCategory.GENERAL:
            full_description = description if description else name
        else:
            full_description = f"{name}。{description}" if description else name
        
        components = {
            "task": full_description,
            "target": name if name else "代码",
            "objective": description or "提高代码质量和可维护性",
            "problem": description or f"{name}相关的问题",
            "focus": "代码质量、安全性和性能",
            "aspect": "性能和效率", 
            "feature": name,
            "requirements": description or "按照最佳实践",
            "test_type": "单元",
            "doc_type": "详细的文档说明"
        }
        
        # Try to extract more specific information based on description
        if description:
            # Extract target from common patterns
            target_patterns = [
                r"(?:重构|修复|优化|分析)(.+?)(?:，|。|$)",
                r"(.+?)(?:模块|组件|函数|类|文件)",
                r"(?:在|的)(.+?)(?:中|里)"
            ]
            
            for pattern in target_patterns:
                match = re.search(pattern, description)
                if match:
                    target = match.group(1).strip()
                    if target and len(target) > 1:
                        components["target"] = target
                        break
            
            # Extract objectives and problems
            if any(word in description for word in ["安全", "性能", "速度", "内存"]):
                components["objective"] = "提高安全性和性能"
                components["focus"] = "安全漏洞和性能瓶颈"
                components["aspect"] = "安全性和性能"
            
            if any(word in description for word in ["bug", "错误", "问题", "故障"]):
                components["problem"] = description
        
        return components
    
    def generate_prompt(self, name: str, description: str, category: TaskCategory) -> str:
        """Generate the prompt part of Claude command"""
        components = self.extract_task_components(name, description, category)
        template = self.command_templates[category]
        
        try:
            # Fill template with available components
            prompt = template.format(**components)
        except KeyError:
            # Fallback to general template if formatting fails
            prompt = self.command_templates[TaskCategory.GENERAL].format(task=components["task"])
        
        return prompt
    
    def generate_command(self, 
                        name: str, 
                        description: str = None,
                        task_type: TaskType = TaskType.LIGHTWEIGHT,
                        working_dir: str = None,
                        auto_execute: bool = False) -> str:
        """Generate complete Claude CLI command"""
        
        # Categorize the task
        category = self.categorize_task(name, description or "")
        
        # Generate the prompt
        prompt = self.generate_prompt(name, description or "", category)
        
        # Add auto-execution suffix if requested
        if auto_execute:
            prompt += self.auto_execution_suffix
        
        # Get permission configuration for task type
        permissions = self.task_type_permissions.get(task_type, self.task_type_permissions[TaskType.LIGHTWEIGHT])
        
        # Build command parts
        command_parts = ["claude", "-p", f'"{prompt}"', "--verbose", "--output-format", "json"]
        
        # Add permission mode
        command_parts.extend(["--permission-mode", permissions["permission_mode"]])
        
        # Add allowed tools
        if permissions["allowed_tools"]:
            tools_str = " ".join(f'"{tool}"' for tool in permissions["allowed_tools"])
            command_parts.extend(["--allowedTools", tools_str])
        
        # Add working directory if specified
        if working_dir:
            command_parts.extend(["--cwd", f'"{working_dir}"'])
        
        return " ".join(command_parts)
    
    def validate_command(self, command: str) -> bool:
        """Validate generated command format"""
        # Basic validation checks
        if not command.startswith("claude"):
            return False
        
        if "-p" not in command and "--print" not in command:
            return False
        
        # Check for dangerous commands that should be blocked
        dangerous_patterns = [
            r"rm\s+-rf",
            r"sudo\s+rm",
            r"format\s+c:",
            r"del\s+/s\s+/q",
            r"shutdown",
            r"reboot"
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return False
        
        return True


# Global instance
command_generator = CommandGenerator()