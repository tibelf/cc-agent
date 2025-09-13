#!/usr/bin/env python3
"""
Auto-Claude Main Entry Point
Coordinates all system components for unattended task execution
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

from task_manager import TaskManager
from worker import ClaudeWorker
from recovery_manager import AutoRecoveryManager
from rate_limit_manager import WaitingUnbanManager
from monitoring import MonitoringService
from config.config import config
from utils import setup_logging


logger = logging.getLogger(__name__)


class AutoClaudeSystem:
    """Main system orchestrator"""
    
    def __init__(self):
        self.running = False
        self.components = {}
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown"""
        logger.info(f"Received signal {signum}, initiating shutdown...")
        self.running = False
    
    async def start(self):
        """Start the Auto-Claude system"""
        logger.info("Starting Auto-Claude system...")
        self.running = True
        
        try:
            # Initialize components
            self.components['task_manager'] = TaskManager()
            self.components['recovery_manager'] = AutoRecoveryManager()
            self.components['rate_limit_manager'] = WaitingUnbanManager()
            self.components['monitoring'] = MonitoringService()
            
            # Start workers (configurable number)
            num_workers = getattr(config, 'num_workers', 2)
            self.components['workers'] = []
            
            for i in range(num_workers):
                worker = ClaudeWorker(f"worker_{i:02d}")
                self.components['workers'].append(worker)
            
            # Start all components
            tasks = []
            
            # Task manager
            tasks.append(asyncio.create_task(
                self.components['task_manager'].start(),
                name="task_manager"
            ))
            
            # Recovery manager
            tasks.append(asyncio.create_task(
                self.components['recovery_manager'].start(),
                name="recovery_manager"
            ))
            
            # Rate limit manager
            tasks.append(asyncio.create_task(
                self.components['rate_limit_manager'].start(),
                name="rate_limit_manager"
            ))
            
            # Monitoring service
            tasks.append(asyncio.create_task(
                self.components['monitoring'].start(),
                name="monitoring"
            ))
            
            # Workers
            for i, worker in enumerate(self.components['workers']):
                tasks.append(asyncio.create_task(
                    worker.start(),
                    name=f"worker_{i}"
                ))
            
            logger.info(f"Started {len(tasks)} system components")
            
            # Wait for shutdown signal or component failure
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Check if any component failed
            for task in done:
                if task.exception():
                    logger.error(f"Component {task.get_name()} failed: {task.exception()}")
                    self.running = False
                    break
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            logger.info("All components stopped")
            
        except Exception as e:
            logger.error(f"System error: {e}")
            raise
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop all system components"""
        logger.info("Stopping Auto-Claude system...")
        
        # Stop components in reverse order
        stop_tasks = []
        
        # Stop workers first
        if 'workers' in self.components:
            for worker in self.components['workers']:
                stop_tasks.append(worker.stop())
        
        # Stop other components
        for name in ['monitoring', 'rate_limit_manager', 'recovery_manager', 'task_manager']:
            if name in self.components:
                component = self.components[name]
                if hasattr(component, 'stop'):
                    stop_tasks.append(component.stop())
        
        # Wait for all components to stop
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        logger.info("Auto-Claude system stopped")


async def main():
    """Main entry point"""
    # Setup logging
    log_file = config.logs_dir / "auto_claude.log"
    config.logs_dir.mkdir(exist_ok=True)
    setup_logging("INFO", str(log_file))
    
    logger.info("=" * 60)
    logger.info("Auto-Claude Task Automation System Starting")
    logger.info("=" * 60)
    logger.info(f"Base directory: {config.base_dir}")
    logger.info(f"Tasks directory: {config.tasks_dir}")
    logger.info(f"Database path: {config.db_path}")
    logger.info(f"Metrics port: {config.metrics_port}")
    
    try:
        system = AutoClaudeSystem()
        await system.start()
    except KeyboardInterrupt:
        logger.info("System interrupted by user")
    except Exception as e:
        logger.error(f"System failure: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Ensure we're running in the correct directory
    script_dir = Path(__file__).parent
    if script_dir.name == "auto-claude":
        import os
        os.chdir(script_dir)
    
    # Run the system
    asyncio.run(main())