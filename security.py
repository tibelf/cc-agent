import re
import os
import hashlib
import logging
import asyncio
from typing import List, Dict, Set, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from models import Task, TaskState, AlertLevel
from database import db
from config.config import config
from utils import create_alert, atomic_write


logger = logging.getLogger(__name__)


class SecurityLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ViolationType(Enum):
    SENSITIVE_DATA = "sensitive_data"
    MALICIOUS_COMMAND = "malicious_command"
    POLICY_VIOLATION = "policy_violation"
    CREDENTIAL_EXPOSURE = "credential_exposure"
    SUSPICIOUS_BEHAVIOR = "suspicious_behavior"


@dataclass
class SecurityViolation:
    violation_type: ViolationType
    severity: SecurityLevel
    description: str
    evidence: str
    task_id: Optional[str] = None
    detected_at: datetime = None
    remediation_action: Optional[str] = None
    
    def __post_init__(self):
        if self.detected_at is None:
            self.detected_at = datetime.utcnow()


class SensitiveDataDetector:
    """Detect and sanitize sensitive data in text"""
    
    def __init__(self):
        self.patterns = self._compile_patterns()
        self.custom_patterns = []
        self._load_custom_patterns()
    
    def _compile_patterns(self) -> Dict[str, re.Pattern]:
        """Compile regex patterns for sensitive data detection"""
        return {
            # Email addresses
            'email': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', re.IGNORECASE),
            
            # Phone numbers (various formats)
            'phone': re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
            'intl_phone': re.compile(r'\+\d{1,3}[-.\s]?\d{1,14}\b'),
            
            # Credit card numbers
            'credit_card': re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
            
            # Social Security Numbers
            'ssn': re.compile(r'\b\d{3}-?\d{2}-?\d{4}\b'),
            
            # API Keys and tokens
            'api_key': re.compile(r'\b[Aa][Pp][Ii][-_]?[Kk][Ee][Yy][-_]?[:\s=]*[\'"]?([A-Za-z0-9+/]{16,})[\'"]?', re.IGNORECASE),
            'openai_key': re.compile(r'\bsk-[a-zA-Z0-9]{48}\b'),
            'claude_key': re.compile(r'\bsk-ant-[a-zA-Z0-9-]{95}\b'),
            'bearer_token': re.compile(r'\bBearer\s+([A-Za-z0-9+/]{20,})', re.IGNORECASE),
            
            # AWS credentials
            'aws_access_key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
            'aws_secret_key': re.compile(r'\b[A-Za-z0-9+/]{40}\b'),
            
            # Private keys
            'private_key': re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----.*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----', re.DOTALL | re.IGNORECASE),
            'ssh_private': re.compile(r'-----BEGIN\s+OPENSSH\s+PRIVATE\s+KEY-----.*?-----END\s+OPENSSH\s+PRIVATE\s+KEY-----', re.DOTALL | re.IGNORECASE),
            
            # Database connection strings
            'db_connection': re.compile(r'\b(?:mysql|postgres|mongodb)://[^@\s]+:[^@\s]+@[^\s]+', re.IGNORECASE),
            
            # Generic passwords in config-like format
            'password_field': re.compile(r'\b(?:password|passwd|pwd|secret)\s*[=:]\s*[\'"]?([^\'"\s\n]{8,})[\'"]?', re.IGNORECASE),
            
            # JWT tokens
            'jwt_token': re.compile(r'\bey[A-Za-z0-9+/]{10,}\.[A-Za-z0-9+/]{10,}\.[A-Za-z0-9+/_-]{10,}\b'),
            
            # Generic secrets (base64-like)
            'base64_secret': re.compile(r'\b[A-Za-z0-9+/]{32,}={0,2}\b'),
        }
    
    def _load_custom_patterns(self):
        """Load custom patterns from configuration"""
        # Allow users to define custom sensitive data patterns
        custom_file = config.config_dir / "custom_patterns.txt"
        if custom_file.exists():
            try:
                with open(custom_file, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            try:
                                self.custom_patterns.append(re.compile(line, re.IGNORECASE))
                            except re.error as e:
                                logger.warning(f"Invalid custom pattern '{line}': {e}")
            except Exception as e:
                logger.error(f"Error loading custom patterns: {e}")
    
    def detect_sensitive_data(self, text: str) -> List[Tuple[str, str, int, int]]:
        """
        Detect sensitive data in text
        Returns: List of (pattern_name, matched_text, start_pos, end_pos)
        """
        findings = []
        
        # Check built-in patterns
        for pattern_name, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                findings.append((
                    pattern_name,
                    match.group(),
                    match.start(),
                    match.end()
                ))
        
        # Check custom patterns
        for i, pattern in enumerate(self.custom_patterns):
            for match in pattern.finditer(text):
                findings.append((
                    f"custom_{i}",
                    match.group(),
                    match.start(),
                    match.end()
                ))
        
        return findings
    
    def sanitize_text(self, text: str) -> str:
        """Sanitize text by replacing sensitive data with masked versions"""
        sanitized = text
        
        # Sort findings by position (reverse order to maintain positions during replacement)
        findings = self.detect_sensitive_data(text)
        findings.sort(key=lambda x: x[2], reverse=True)
        
        for pattern_name, matched_text, start_pos, end_pos in findings:
            # Create masked version
            if len(matched_text) > 4:
                masked = '***' + matched_text[-4:]
            else:
                masked = '***'
            
            # Replace in text
            sanitized = sanitized[:start_pos] + masked + sanitized[end_pos:]
        
        return sanitized
    
    def analyze_sensitivity(self, text: str) -> Dict[str, any]:
        """Analyze text for sensitivity levels"""
        findings = self.detect_sensitive_data(text)
        
        # Categorize findings by severity
        critical_patterns = {'private_key', 'ssh_private', 'aws_secret_key'}
        high_patterns = {'api_key', 'openai_key', 'claude_key', 'jwt_token', 'db_connection'}
        medium_patterns = {'credit_card', 'ssn', 'password_field'}
        low_patterns = {'email', 'phone'}
        
        severity_counts = {
            SecurityLevel.CRITICAL: 0,
            SecurityLevel.HIGH: 0,
            SecurityLevel.MEDIUM: 0,
            SecurityLevel.LOW: 0
        }
        
        detailed_findings = []
        
        for pattern_name, matched_text, start_pos, end_pos in findings:
            if pattern_name in critical_patterns:
                severity = SecurityLevel.CRITICAL
            elif pattern_name in high_patterns:
                severity = SecurityLevel.HIGH
            elif pattern_name in medium_patterns:
                severity = SecurityLevel.MEDIUM
            else:
                severity = SecurityLevel.LOW
            
            severity_counts[severity] += 1
            detailed_findings.append({
                'pattern': pattern_name,
                'severity': severity.value,
                'position': (start_pos, end_pos),
                'context': text[max(0, start_pos-20):min(len(text), end_pos+20)]
            })
        
        return {
            'total_findings': len(findings),
            'severity_counts': {k.value: v for k, v in severity_counts.items()},
            'detailed_findings': detailed_findings,
            'overall_risk': self._calculate_risk_level(severity_counts)
        }
    
    def _calculate_risk_level(self, severity_counts: Dict[SecurityLevel, int]) -> SecurityLevel:
        """Calculate overall risk level based on findings"""
        if severity_counts[SecurityLevel.CRITICAL] > 0:
            return SecurityLevel.CRITICAL
        elif severity_counts[SecurityLevel.HIGH] > 0:
            return SecurityLevel.HIGH
        elif severity_counts[SecurityLevel.MEDIUM] > 2:
            return SecurityLevel.HIGH
        elif severity_counts[SecurityLevel.MEDIUM] > 0 or severity_counts[SecurityLevel.LOW] > 5:
            return SecurityLevel.MEDIUM
        elif severity_counts[SecurityLevel.LOW] > 0:
            return SecurityLevel.LOW
        else:
            return SecurityLevel.LOW


class CommandSecurityAnalyzer:
    """Analyze commands for security risks"""
    
    def __init__(self):
        self.dangerous_commands = {
            # System modification
            'rm -rf', 'sudo rm', 'chmod 777', 'chown', 'usermod', 'passwd',
            # Network operations
            'wget', 'curl', 'nc', 'netcat', 'ssh', 'scp', 'rsync',
            # Process control
            'kill -9', 'killall', 'pkill', 'nohup',
            # File operations
            'dd', 'shred', 'wipe',
            # Package management
            'apt install', 'yum install', 'pip install', 'npm install',
            # Service control
            'systemctl', 'service', 'crontab',
            # Archive operations
            'tar', 'zip', 'unzip', 'gunzip'
        }
        
        self.suspicious_patterns = [
            # Code execution
            r'\beval\s*\(',
            r'\bexec\s*\(',
            r'__import__\s*\(',
            # File inclusion
            r'include\s+["\'](?:\.\.\/|\/etc\/)',
            # SQL injection patterns
            r'union\s+select', r'drop\s+table', r'insert\s+into',
            # Shell injection
            r'[;&|`]\s*(?:rm|cat|ls|ps|id|whoami)',
            # Environment variable access
            r'\$\{?(?:PATH|HOME|USER|SHELL)',
            # Reverse shells
            r'bash\s+-i', r'sh\s+-i', r'/dev/tcp/',
            # Base64 encoded commands
            r'base64\s+-d', r'echo\s+[A-Za-z0-9+/]{20,}\s*\|\s*base64'
        ]
        
        self.compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self.suspicious_patterns]
    
    def analyze_command(self, command: str) -> Dict[str, any]:
        """Analyze command for security risks"""
        risks = []
        risk_level = SecurityLevel.LOW
        
        command_lower = command.lower()
        
        # Check for dangerous commands
        for dangerous_cmd in self.dangerous_commands:
            if dangerous_cmd in command_lower:
                risks.append(f"Dangerous command detected: {dangerous_cmd}")
                risk_level = max(risk_level, SecurityLevel.MEDIUM, key=lambda x: x.name)
        
        # Check for suspicious patterns
        for pattern in self.compiled_patterns:
            matches = pattern.finditer(command)
            for match in matches:
                risks.append(f"Suspicious pattern: {match.group()}")
                risk_level = max(risk_level, SecurityLevel.HIGH, key=lambda x: x.name)
        
        # Check for command chaining (multiple commands)
        if any(sep in command for sep in [';', '&&', '||', '|']):
            if len(command.split()) > 10:  # Long command chains are suspicious
                risks.append("Complex command chaining detected")
                risk_level = max(risk_level, SecurityLevel.MEDIUM, key=lambda x: x.name)
        
        # Check for unusual characters
        unusual_chars = set(command) - set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;:()[]{}/-_=+*&^%$#@!?\'"|\\`~')
        if unusual_chars:
            risks.append(f"Unusual characters detected: {unusual_chars}")
            risk_level = max(risk_level, SecurityLevel.MEDIUM, key=lambda x: x.name)
        
        return {
            'command': command,
            'risk_level': risk_level.value,
            'risks': risks,
            'safe_to_execute': risk_level in [SecurityLevel.LOW, SecurityLevel.MEDIUM]
        }


class ComplianceChecker:
    """Check tasks for compliance violations"""
    
    def __init__(self):
        self.violation_patterns = self._load_violation_patterns()
        self.audit_log_file = config.logs_dir / "security_audit.log"
        
        # Ensure audit log exists
        config.logs_dir.mkdir(exist_ok=True)
        self.audit_log_file.touch()
    
    def _load_violation_patterns(self) -> Dict[str, List[str]]:
        """Load compliance violation patterns"""
        return {
            'data_privacy': [
                r'\b(?:personal|private|confidential)\s+(?:data|information)',
                r'\b(?:GDPR|CCPA|HIPAA)\s+violation',
                r'\bcollect\s+(?:user|customer)\s+data',
                r'\bstore\s+(?:passwords|credentials)\s+in\s+plain'
            ],
            'malicious_intent': [
                r'\b(?:hack|exploit|bypass|circumvent)\s+security',
                r'\b(?:steal|extract|exfiltrate)\s+(?:data|credentials)',
                r'\b(?:backdoor|trojan|malware|virus)',
                r'\bdenial\s+of\s+service',
                r'\bbrute\s+force\s+attack'
            ],
            'unauthorized_access': [
                r'\b(?:unauthorized|illegal)\s+access',
                r'\bescalate\s+privileges',
                r'\bbypass\s+authentication',
                r'\b(?:crack|break)\s+(?:password|encryption)'
            ],
            'resource_abuse': [
                r'\b(?:mine|mining)\s+(?:bitcoin|cryptocurrency)',
                r'\bexcessive\s+(?:CPU|memory|disk)\s+usage',
                r'\b(?:spam|flood|bombard)\s+(?:emails|requests)',
                r'\bDDoS\s+attack'
            ]
        }
    
    def check_compliance(self, task: Task, content: str = None) -> List[SecurityViolation]:
        """Check task for compliance violations"""
        violations = []
        
        # Check command for violations
        if task.command:
            command_violations = self._check_text_compliance(
                task.command, 
                f"Task {task.id} command",
                task.id
            )
            violations.extend(command_violations)
        
        # Check description for violations
        if task.description:
            desc_violations = self._check_text_compliance(
                task.description,
                f"Task {task.id} description", 
                task.id
            )
            violations.extend(desc_violations)
        
        # Check additional content if provided
        if content:
            content_violations = self._check_text_compliance(
                content,
                f"Task {task.id} content",
                task.id
            )
            violations.extend(content_violations)
        
        # Log violations
        for violation in violations:
            self._log_security_event(violation)
        
        return violations
    
    def _check_text_compliance(self, text: str, context: str, task_id: str = None) -> List[SecurityViolation]:
        """Check text content for compliance violations"""
        violations = []
        text_lower = text.lower()
        
        for category, patterns in self.violation_patterns.items():
            for pattern_str in patterns:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                matches = pattern.finditer(text)
                
                for match in matches:
                    severity = self._determine_severity(category, match.group())
                    
                    violation = SecurityViolation(
                        violation_type=ViolationType.POLICY_VIOLATION,
                        severity=severity,
                        description=f"Compliance violation in {context}: {category}",
                        evidence=f"Pattern: {pattern_str}, Match: {match.group()}",
                        task_id=task_id
                    )
                    violations.append(violation)
        
        return violations
    
    def _determine_severity(self, category: str, matched_text: str) -> SecurityLevel:
        """Determine severity based on violation category and content"""
        high_risk_categories = {'malicious_intent', 'unauthorized_access'}
        
        if category in high_risk_categories:
            return SecurityLevel.CRITICAL
        elif 'hack' in matched_text.lower() or 'exploit' in matched_text.lower():
            return SecurityLevel.CRITICAL
        elif category == 'data_privacy':
            return SecurityLevel.HIGH
        else:
            return SecurityLevel.MEDIUM
    
    def _log_security_event(self, violation: SecurityViolation):
        """Log security event to audit log"""
        try:
            event = {
                'timestamp': violation.detected_at.isoformat(),
                'type': violation.violation_type.value,
                'severity': violation.severity.value,
                'description': violation.description,
                'evidence': violation.evidence,
                'task_id': violation.task_id
            }
            
            log_entry = f"{datetime.utcnow().isoformat()} SECURITY_VIOLATION {event}\n"
            
            with open(self.audit_log_file, 'a') as f:
                f.write(log_entry)
                
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")


class SecurityManager:
    """Main security manager coordinating all security features"""
    
    def __init__(self):
        self.sensitive_detector = SensitiveDataDetector()
        self.command_analyzer = CommandSecurityAnalyzer()
        self.compliance_checker = ComplianceChecker()
        self.blocked_tasks: Set[str] = set()
        
    async def scan_task(self, task: Task) -> Dict[str, any]:
        """Comprehensive security scan of a task"""
        scan_results = {
            'task_id': task.id,
            'scan_timestamp': datetime.utcnow().isoformat(),
            'violations': [],
            'risk_level': SecurityLevel.LOW.value,
            'blocked': False,
            'recommendations': []
        }
        
        try:
            # Command security analysis
            if task.command:
                command_analysis = self.command_analyzer.analyze_command(task.command)
                if not command_analysis['safe_to_execute']:
                    scan_results['violations'].append({
                        'type': 'unsafe_command',
                        'severity': command_analysis['risk_level'],
                        'details': command_analysis['risks']
                    })
            
            # Compliance checking
            compliance_violations = self.compliance_checker.check_compliance(task)
            for violation in compliance_violations:
                scan_results['violations'].append({
                    'type': violation.violation_type.value,
                    'severity': violation.severity.value,
                    'description': violation.description,
                    'evidence': violation.evidence
                })
            
            # Sensitive data analysis
            text_to_scan = f"{task.name} {task.description or ''} {task.command}"
            sensitivity_analysis = self.sensitive_detector.analyze_sensitivity(text_to_scan)
            
            if sensitivity_analysis['total_findings'] > 0:
                scan_results['violations'].append({
                    'type': 'sensitive_data',
                    'severity': sensitivity_analysis['overall_risk'].value,
                    'details': sensitivity_analysis
                })
            
            # Determine overall risk level and blocking decision
            scan_results['risk_level'] = self._calculate_overall_risk(scan_results['violations'])
            scan_results['blocked'] = self._should_block_task(scan_results['risk_level'])
            
            if scan_results['blocked']:
                self.blocked_tasks.add(task.id)
                task.task_state = TaskState.NEEDS_HUMAN_REVIEW
                task.add_error(f"Task blocked due to security violations: {scan_results['risk_level']}")
                
                # Create alert
                create_alert(
                    level=AlertLevel.P1,
                    title=f"Task {task.id} blocked by security scan",
                    message=f"Task blocked due to {len(scan_results['violations'])} security violations",
                    task_id=task.id,
                    metadata=scan_results
                )
            
            # Generate recommendations
            scan_results['recommendations'] = self._generate_recommendations(scan_results)
            
        except Exception as e:
            logger.error(f"Error during security scan: {e}")
            scan_results['violations'].append({
                'type': 'scan_error',
                'severity': SecurityLevel.MEDIUM.value,
                'description': f"Security scan failed: {str(e)}"
            })
        
        return scan_results
    
    def _calculate_overall_risk(self, violations: List[Dict]) -> str:
        """Calculate overall risk level from violations"""
        if not violations:
            return SecurityLevel.LOW.value
        
        max_severity = SecurityLevel.LOW
        for violation in violations:
            severity = SecurityLevel(violation.get('severity', 'low'))
            if severity.name > max_severity.name:  # Compare enum ordering
                max_severity = severity
        
        return max_severity.value
    
    def _should_block_task(self, risk_level: str) -> bool:
        """Determine if task should be blocked based on risk level"""
        risk = SecurityLevel(risk_level)
        return risk in [SecurityLevel.CRITICAL, SecurityLevel.HIGH]
    
    def _generate_recommendations(self, scan_results: Dict) -> List[str]:
        """Generate security recommendations based on scan results"""
        recommendations = []
        
        for violation in scan_results['violations']:
            violation_type = violation.get('type', '')
            
            if violation_type == 'unsafe_command':
                recommendations.append("Review command for potentially dangerous operations")
                recommendations.append("Consider running command in sandboxed environment")
            
            elif violation_type == 'sensitive_data':
                recommendations.append("Remove or mask sensitive data before execution")
                recommendations.append("Use environment variables for sensitive configuration")
            
            elif violation_type == 'policy_violation':
                recommendations.append("Review task compliance with organizational policies")
                recommendations.append("Consult with security team before proceeding")
        
        if scan_results['blocked']:
            recommendations.append("Task requires manual security review before execution")
        
        return list(set(recommendations))  # Remove duplicates
    
    def sanitize_output(self, text: str) -> str:
        """Sanitize output text to remove sensitive information"""
        return self.sensitive_detector.sanitize_text(text)
    
    def is_task_blocked(self, task_id: str) -> bool:
        """Check if a task is blocked by security"""
        return task_id in self.blocked_tasks
    
    def unblock_task(self, task_id: str, reason: str) -> bool:
        """Manually unblock a task after security review"""
        if task_id in self.blocked_tasks:
            self.blocked_tasks.remove(task_id)
            
            # Log the unblock action
            logger.info(f"Task {task_id} unblocked by security review: {reason}")
            
            # Update task state
            task = db.get_task(task_id)
            if task and task.task_state == TaskState.NEEDS_HUMAN_REVIEW:
                task.task_state = TaskState.PENDING
                task.add_error(f"Unblocked after security review: {reason}")
                db.save_task(task)
            
            return True
        
        return False
    
    def get_security_report(self) -> Dict[str, any]:
        """Generate security report"""
        # Get tasks that need human review
        blocked_tasks = db.get_tasks_by_state([TaskState.NEEDS_HUMAN_REVIEW.value])
        
        # Count violations from audit log
        violation_counts = self._count_recent_violations()
        
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'blocked_tasks_count': len(blocked_tasks),
            'blocked_task_ids': [task.id for task in blocked_tasks],
            'recent_violations': violation_counts,
            'security_status': 'healthy' if len(blocked_tasks) == 0 else 'needs_attention'
        }
    
    def _count_recent_violations(self) -> Dict[str, int]:
        """Count recent violations from audit log"""
        counts = {}
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        
        try:
            if self.compliance_checker.audit_log_file.exists():
                with open(self.compliance_checker.audit_log_file, 'r') as f:
                    for line in f:
                        if 'SECURITY_VIOLATION' in line:
                            timestamp_str = line.split()[0]
                            try:
                                timestamp = datetime.fromisoformat(timestamp_str)
                                if timestamp > cutoff_time:
                                    # Extract violation type from log entry
                                    if "'type':" in line:
                                        start = line.find("'type': '") + 9
                                        end = line.find("'", start)
                                        violation_type = line[start:end] if start < end else 'unknown'
                                        counts[violation_type] = counts.get(violation_type, 0) + 1
                            except ValueError:
                                continue
        except Exception as e:
            logger.error(f"Error counting violations: {e}")
        
        return counts


# Global security manager instance
security_manager = SecurityManager()