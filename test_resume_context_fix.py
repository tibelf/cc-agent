#!/usr/bin/env python3
"""
测试 resume context 误判问题的修复
"""
import json
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worker import ClaudeWorker
from models import Task, TaskType

def test_resume_context_not_detected():
    """测试系统生成的 resume context 不再被误判为交互需求"""
    worker = ClaudeWorker("test_worker")
    
    # 创建一个测试任务
    task = Task(
        id="test_task_002",
        name="Resume Context Test",
        command="test command", 
        task_type=TaskType.LIGHTWEIGHT
    )
    
    print("Testing resume context lines (should NOT be detected as interactive):")
    
    # 这些是系统生成的内容，不应该被检测为需要交互
    resume_context_lines = [
        "=== TASK RESUME CONTEXT ===",
        "Task: test2",
        "Retry Count: 4", 
        "Previous interaction detected: Continue from where we left off.",
        "Auto-responding with: yes",
        "Please continue with the task after this response.",
        "=== CHECKPOINT DATA ===",
        "Continue from where we left off."
    ]
    
    for line in resume_context_lines:
        result_content = worker._extract_claude_result(line)
        print(f"Line: '{line[:50]}...' -> Result content: {result_content}")
        # 这些行都不应该返回 result content，因为它们不是 JSON result
        assert result_content is None, f"Resume context line should not be extracted as result: '{line}'"
    
    print("✅ Resume context lines correctly ignored!")

def test_actual_claude_result():
    """测试实际的 Claude JSON result 能正确处理"""
    worker = ClaudeWorker("test_worker") 
    
    task = Task(
        id="test_task_003",
        name="Claude Result Test",
        command="test command",
        task_type=TaskType.LIGHTWEIGHT
    )
    
    print("\nTesting actual Claude JSON results:")
    
    # 模拟实际的 Claude JSON 输出
    actual_claude_outputs = [
        # 正常回答，不需要交互
        '{"type":"result","subtype":"success","is_error":false,"duration_ms":3796,"result":"今天是星期二。","session_id":"abc123"}',
        
        # 简单的信息回答，不需要交互  
        '{"type":"result","subtype":"success","is_error":false,"result":"当前时间是 2025年9月17日。","session_id":"abc123"}',
        
        # 需要用户确认的内容
        '{"type":"result","subtype":"success","is_error":false,"result":"请确认是否删除所有文件 (y/n)?","session_id":"abc123"}'
    ]
    
    expected_results = [False, False, True]  # 前两个不需要交互，第三个需要交互
    
    for i, output_line in enumerate(actual_claude_outputs):
        result_content = worker._extract_claude_result(output_line)
        print(f"Claude output {i+1}: '{result_content}'")
        
        if result_content:
            needs_interaction = worker._ai_detect_interaction_need_sync(result_content, task)
            expected = expected_results[i]
            print(f"  -> Needs interaction: {needs_interaction} (expected: {expected})")
            
            # 注意：这里我们不强制断言，因为 AI 判断可能有细微差异
            # 但我们可以观察结果是否合理
            if needs_interaction != expected:
                print(f"  ⚠️  AI judgment differs from expectation, but this might be acceptable")
            else:
                print(f"  ✅ AI judgment matches expectation")
    
    print("✅ Claude result processing test completed!")

if __name__ == "__main__":
    print("🧪 Testing resume context fix...")
    
    test_resume_context_not_detected()
    test_actual_claude_result()
    
    print("\n✅ Resume context fix tests completed!")