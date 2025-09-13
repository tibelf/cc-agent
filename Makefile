# Auto-Claude System Makefile

.PHONY: help install test clean run stop status setup-service

# Default target
help:
	@echo "Auto-Claude System - Available targets:"
	@echo "  install      - Install dependencies and setup system"
	@echo "  test         - Run test suite"
	@echo "  clean        - Clean up generated files"
	@echo "  run          - Run the system directly"
	@echo "  setup-service- Setup systemd service (requires sudo)"
	@echo "  start        - Start systemd service"
	@echo "  stop         - Stop systemd service"
	@echo "  status       - Show system status"
	@echo "  logs         - Follow system logs"

# Install dependencies and setup
install:
	@echo "Installing Auto-Claude system..."
	pip install -r requirements.txt
	python taskctl.py init
	@echo "Installation complete!"

# Run tests
test:
	@echo "Running test suite..."
	python -m pytest tests/ -v
	@echo "Tests completed!"

# Clean up generated files
clean:
	@echo "Cleaning up..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf build/ dist/
	@echo "Cleanup complete!"

# Run system directly
run:
	@echo "Starting Auto-Claude system..."
	python auto_claude.py

# Setup systemd service
setup-service:
	@echo "Setting up systemd service..."
	sudo python taskctl.py init
	sudo systemctl daemon-reload
	@echo "Service setup complete! Use 'make start' to start the service."

# Start service
start:
	@echo "Starting Auto-Claude service..."
	sudo systemctl start auto-claude
	sudo systemctl status auto-claude

# Stop service
stop:
	@echo "Stopping Auto-Claude service..."
	sudo systemctl stop auto-claude

# Show status
status:
	@echo "Auto-Claude system status:"
	@if systemctl is-active --quiet auto-claude; then \
		echo "✅ Service is running"; \
		sudo systemctl status auto-claude; \
	else \
		echo "❌ Service is not running"; \
	fi
	@echo ""
	@echo "Task status:"
	@python taskctl.py system status

# Follow logs
logs:
	@echo "Following Auto-Claude logs..."
	@if systemctl is-active --quiet auto-claude; then \
		sudo journalctl -f -u auto-claude; \
	else \
		echo "Service not running. Showing log file:"; \
		tail -f logs/auto_claude.log 2>/dev/null || echo "No log file found."; \
	fi

# Development targets
dev-setup:
	@echo "Setting up development environment..."
	pip install -e .
	pip install pytest pytest-asyncio black flake8
	@echo "Development setup complete!"

format:
	@echo "Formatting code..."
	black .
	@echo "Code formatting complete!"

lint:
	@echo "Linting code..."
	flake8 . --max-line-length=100 --ignore=E203,W503
	@echo "Linting complete!"