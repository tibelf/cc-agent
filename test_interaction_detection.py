#!/usr/bin/env python3
"""
测试新的 AI 交互检测逻辑
"""
import json
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worker import ClaudeWorker
from models import Task, TaskType

def test_extract_claude_result():
    """测试 JSON result 提取功能"""
    worker = ClaudeWorker("test_worker")
    
    # 测试正常的 result JSON
    test_line1 = '{"type":"result","subtype":"success","is_error":false,"result":"今天是星期二。","session_id":"123"}'
    result1 = worker._extract_claude_result(test_line1)
    print(f"Test 1 - Extract result: '{result1}'")
    assert result1 == "今天是星期二。", f"Expected '今天是星期二。', got '{result1}'"
    
    # 测试不是 result 类型的 JSON
    test_line2 = '{"type":"system","subtype":"init","session_id":"123"}'
    result2 = worker._extract_claude_result(test_line2)
    print(f"Test 2 - Non-result JSON: {result2}")
    assert result2 is None, f"Expected None, got '{result2}'"
    
    # 测试普通文本行
    test_line3 = "=== TASK RESUME CONTEXT ==="
    result3 = worker._extract_claude_result(test_line3)
    print(f"Test 3 - Plain text: {result3}")
    assert result3 is None, f"Expected None, got '{result3}'"
    
    # 测试包含交互需求的 result
    test_line4 = '{"type":"result","subtype":"success","is_error":false,"result":"请确认是否继续操作 (y/n)","session_id":"123"}'
    result4 = worker._extract_claude_result(test_line4)
    print(f"Test 4 - Interactive result: '{result4}'")
    assert result4 == "请确认是否继续操作 (y/n)", f"Expected interactive text, got '{result4}'"
    
    print("✅ All extract tests passed!")

def test_ai_interaction_detection():
    """测试 AI 交互检测功能（需要实际的 claude 命令）"""
    worker = ClaudeWorker("test_worker")
    
    # 创建一个测试任务
    task = Task(
        id="test_task_001",
        name="Test Task",
        command="test command",
        task_type=TaskType.LIGHTWEIGHT
    )
    
    print("Testing AI interaction detection...")
    
    # 测试非交互内容
    non_interactive = "今天是星期二。"
    result1 = worker._ai_detect_interaction_need_sync(non_interactive, task)
    print(f"Non-interactive result: {result1}")
    
    # 测试交互内容
    interactive = "请确认是否继续操作 (y/n)"
    result2 = worker._ai_detect_interaction_need_sync(interactive, task)
    print(f"Interactive result: {result2}")
    
    print("✅ AI detection tests completed!")

if __name__ == "__main__":
    print("🧪 Testing new interaction detection logic...")
    
    print("\n1. Testing JSON result extraction:")
    test_extract_claude_result()
    
    print("\n2. Testing AI interaction detection:")
    test_ai_interaction_detection()
    
    print("\n✅ All tests completed successfully!")