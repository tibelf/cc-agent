#!/usr/bin/env python3
"""
测试新的任务完成验证逻辑
"""

from command_generator import command_generator
from models import TaskType
import re

def test_command_generation():
    """测试命令生成是否包含完成标识规则"""
    print("🧪 测试命令生成...")
    
    # 测试不同类型的任务
    test_cases = [
        ("修复bug", "修复登录页面的问题", TaskType.LIGHTWEIGHT),
        ("实现功能", "添加用户管理功能", TaskType.MEDIUM_CONTEXT), 
        ("分析代码", "分析性能瓶颈", TaskType.HEAVY_CONTEXT),
    ]
    
    for name, description, task_type in test_cases:
        command = command_generator.generate_command(
            name=name,
            description=description,
            task_type=task_type,
            auto_execute=True
        )
        
        print(f"\n✅ 任务: {name}")
        print(f"   类型: {task_type.value}")
        
        # 检查是否包含完成标识规则
        has_completion_rule = "✅ TASK_COMPLETED" in command
        print(f"   包含完成规则: {has_completion_rule}")
        
        if has_completion_rule:
            # 提取完成规则部分
            match = re.search(r'COMPLETION RULE:(.*?)(?:"|$)', command, re.DOTALL)
            if match:
                completion_text = match.group(1).strip()
                print(f"   完成规则预览: {completion_text[:100]}...")
        
        assert has_completion_rule, f"任务 {name} 缺少完成标识规则"

def test_analyze_final_result():
    """测试分析最终结果的逻辑"""
    print("\n🧪 测试结果分析逻辑...")
    
    from worker import ClaudeWorker
    from models import Task, TaskState, TaskType, TaskPriority
    from task_manager import TaskManager
    
    # 创建测试实例
    worker = ClaudeWorker(worker_id="test_worker")
    task_manager = TaskManager()
    
    # 创建测试任务
    task = task_manager.create_task(
        name="测试任务",
        command="echo 'test'",
        description="测试用任务",
        task_type=TaskType.LIGHTWEIGHT,
        priority=TaskPriority.NORMAL
    )
    
    # 测试不同的输出情况
    test_cases = [
        # (输出内容, 预期的interaction_needed, 预期的task_completed, 描述)
        ("执行成功\n✅ TASK_COMPLETED", False, True, "包含完成标识"),
        ('{"type":"result","result":"执行完成\\n✅ TASK_COMPLETED"}', False, True, "result中包含完成标识"),
        ("提供手动操作步骤...", False, False, "无完成标识，无交互需求"),
        ('{"type":"result","result":"需要您确认"}', True, False, "需要交互"),
        ("任务执行中...", False, False, "无result事件，无完成标识"),
    ]
    
    for output, expected_interaction, expected_completion, description in test_cases:
        interaction_needed, task_completed = worker._analyze_final_result(task, output)
        
        print(f"\n   测试: {description}")
        print(f"   输出: {output[:50]}...")
        print(f"   需要交互: {interaction_needed} (预期: {expected_interaction})")
        print(f"   任务完成: {task_completed} (预期: {expected_completion})")
        
        assert interaction_needed == expected_interaction, f"交互判断错误: {description}"
        assert task_completed == expected_completion, f"完成判断错误: {description}"
        
        print(f"   ✅ 测试通过")

def test_completion_scenarios():
    """测试完整的完成场景"""
    print("\n🧪 测试完整场景...")
    
    scenarios = [
        {
            "name": "task_c77090c5类型 - 技术故障提供手动指引",
            "output": """
I encountered technical issues with both the Twitter API and browser connection.
Here are manual instructions for finding and commenting on Qwen3-Max tweets:
1. Visit the Twitter search URL above
2. Find 5 relevant original English tweets
""",
            "expected_result": "任务未完成（无完成标识）"
        },
        {
            "name": "正常完成任务",
            "output": """
Successfully found 5 trending tweets about qwen3-max and posted constructive comments:
1. Posted comment on tweet by @user1...
2. Posted comment on tweet by @user2...
All tasks completed successfully.
✅ TASK_COMPLETED
""",
            "expected_result": "任务完成（有完成标识）"
        },
        {
            "name": "交互后完成任务", 
            "output": """
{"type":"result","result":"Successfully processed user confirmation and completed all actions.\\n✅ TASK_COMPLETED"}
""",
            "expected_result": "任务完成（result中有完成标识）"
        }
    ]
    
    from worker import ClaudeWorker
    from models import Task, TaskState, TaskType, TaskPriority
    from task_manager import TaskManager
    
    worker = ClaudeWorker(worker_id="test_worker")
    task_manager = TaskManager()
    
    task = task_manager.create_task(
        name="场景测试任务",
        command="echo 'test'", 
        description="场景测试",
        task_type=TaskType.LIGHTWEIGHT,
        priority=TaskPriority.NORMAL
    )
    
    for scenario in scenarios:
        print(f"\n   场景: {scenario['name']}")
        interaction_needed, task_completed = worker._analyze_final_result(task, scenario['output'])
        
        if task_completed:
            result = "任务完成"
        elif interaction_needed:
            result = "需要交互"
        else:
            result = "任务未完成"
            
        print(f"   实际结果: {result}")
        print(f"   预期结果: {scenario['expected_result']}")
        print(f"   ✅ 符合预期" if result in scenario['expected_result'] else "❌ 不符合预期")

if __name__ == "__main__":
    print("🚀 开始测试新的任务完成验证逻辑\n")
    
    try:
        test_command_generation()
        test_analyze_final_result()
        test_completion_scenarios()
        
        print("\n🎉 所有测试通过！新的完成验证逻辑工作正常。")
        print("\n📋 修改总结:")
        print("1. ✅ command_generator.py: 添加了固定完成标识规则")
        print("2. ✅ worker.py: 修改了结果分析逻辑，优先检查完成标识")
        print("3. ✅ 新逻辑区分了三种情况：完成、需要交互、未完成")
        print("\n🔧 效果:")
        print("- task_c77090c5类型的误判将被正确识别为未完成")
        print("- 只有明确包含 ✅ TASK_COMPLETED 的任务才会被标记为完成")
        print("- 大大简化了验证逻辑，提高了准确性")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()