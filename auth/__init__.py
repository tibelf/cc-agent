"""
安全认证模块
提供用户认证、授权、会话管理和安全防护功能
"""

from .models import User, Role, Permission, Session, AuthConfig
from .password_manager import PasswordManager
from .token_manager import TokenManager
from .session_manager import SessionManager
from .auth_service import AuthService
from .middleware import AuthMiddleware, require_permission, require_role
from .mfa import MFAService, TOTPProvider, SMSProvider
from .security import SecurityPolicy, LoginAttemptTracker, AuditLogger

__version__ = "1.0.0"
__all__ = [
    "User", "Role", "Permission", "Session", "AuthConfig",
    "PasswordManager", "TokenManager", "SessionManager", 
    "AuthService", "AuthMiddleware", "require_permission", "require_role",
    "MFAService", "TOTPProvider", "SMSProvider",
    "SecurityPolicy", "LoginAttemptTracker", "AuditLogger"
]