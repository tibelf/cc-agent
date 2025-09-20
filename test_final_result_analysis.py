#!/usr/bin/env python3
"""
Test script for final result analysis fix
Tests if _analyze_final_result can correctly extract and analyze JSON results
"""

import json
from worker import ClaudeWorker
from models import Task, TaskType

def test_extract_from_task_output():
    """Test extraction from actual task output"""
    
    # Load actual task output from task_3dd1f01e
    task_output_file = "/Users/tibelf/Github/cc-agent/tasks/task_3dd1f01e/output.log"
    
    try:
        with open(task_output_file, 'r', encoding='utf-8') as f:
            total_output = f.read()
        
        print(f"Loaded output: {len(total_output)} characters")
        
        # Create a test task
        test_task = Task(
            id="test_task",
            name="Test Task",
            description="Test description",
            command="test command",
            task_type=TaskType.LIGHTWEIGHT
        )
        
        # Create worker instance
        worker = ClaudeWorker("test_worker")
        
        # Test the new _analyze_final_result method
        print("\n=== Testing _analyze_final_result ===")
        interaction_needed = worker._analyze_final_result(test_task, total_output)
        
        print(f"Interaction needed: {interaction_needed}")
        
        # Also test the old method for comparison (but don't let it affect return value)
        try:
            print("\n=== Testing old _extract_claude_result on individual lines ===")
            lines = total_output.split('\n')
            found_results = []
            
            for i, line in enumerate(lines):
                if '"type":"result"' in line:
                    print(f"Line {i}: Found potential result line (length: {len(line)})")
                    try:
                        result = worker._extract_claude_result(line)
                        if result:
                            found_results.append(result)
                            print(f"  -> Extracted result: {result[:100]}...")
                        else:
                            print(f"  -> Failed to extract (JSON parse error?)")
                    except Exception as e:
                        print(f"  -> Error in old method: {e}")
            
            print(f"\nOld method found {len(found_results)} results")
        except Exception as e:
            print(f"Old method test failed: {e}")
        
        return interaction_needed
        
    except Exception as e:
        print(f"Error: {e}")
        return False

def test_simple_json():
    """Test with a simple JSON result"""
    
    simple_output = '''[{"type":"system","session_id":"test123"},{"type":"result","subtype":"success","result":"Please follow these manual steps to complete the task:\n1. Go to Twitter\n2. Search for tweets\n3. Post comments","session_id":"test123"}]'''
    
    test_task = Task(
        id="simple_test",
        name="Simple Test",
        description="Simple test",
        command="test",
        task_type=TaskType.LIGHTWEIGHT
    )
    
    worker = ClaudeWorker("test_worker")
    
    print("\n=== Testing with simple JSON ===")
    interaction_needed = worker._analyze_final_result(test_task, simple_output)
    print(f"Simple test interaction needed: {interaction_needed}")
    
    return interaction_needed

if __name__ == "__main__":
    print("Testing final result analysis fix...")
    
    # Test with actual task output
    actual_result = test_extract_from_task_output()
    
    # Test with simple JSON
    simple_result = test_simple_json()
    
    print(f"\n=== Summary ===")
    print(f"Actual task_3dd1f01e analysis: interaction_needed = {actual_result}")
    print(f"Simple JSON test: interaction_needed = {simple_result}")
    
    if actual_result:
        print("\n✅ SUCCESS: The fix correctly detected that task_3dd1f01e needs user interaction!")
    else:
        print("\n❌ ISSUE: The fix did not detect interaction need for task_3dd1f01e")