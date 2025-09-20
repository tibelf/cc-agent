"""
认证模块数据模型
定义用户、角色、权限、会话等核心数据结构
"""

import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Set
from enum import Enum
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, validator
import secrets


class UserStatus(Enum):
    """用户状态枚举"""
    ACTIVE = "active"
    INACTIVE = "inactive" 
    LOCKED = "locked"
    SUSPENDED = "suspended"
    PENDING_ACTIVATION = "pending_activation"


class SessionStatus(Enum):
    """会话状态枚举"""
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    INVALID = "invalid"


class PermissionLevel(Enum):
    """权限级别枚举"""
    READ = "read"
    WRITE = "write" 
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


@dataclass
class Permission:
    """权限模型"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    resource: str = ""  # 资源名称，如 'tasks', 'users', 'system'
    level: PermissionLevel = PermissionLevel.READ
    description: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def __str__(self):
        return f"{self.resource}:{self.level.value}"
    
    def matches(self, resource: str, required_level: PermissionLevel) -> bool:
        """检查权限是否匹配指定资源和级别"""
        if self.resource != resource and self.resource != '*':
            return False
        
        # 权限级别层次: READ < WRITE < ADMIN < SUPER_ADMIN
        level_hierarchy = {
            PermissionLevel.READ: 1,
            PermissionLevel.WRITE: 2,
            PermissionLevel.ADMIN: 3,
            PermissionLevel.SUPER_ADMIN: 4
        }
        
        return level_hierarchy[self.level] >= level_hierarchy[required_level]


@dataclass
class Role:
    """角色模型"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    permissions: List[Permission] = field(default_factory=list)
    is_system_role: bool = False  # 系统角色不能删除
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    def add_permission(self, permission: Permission):
        """添加权限"""
        if permission not in self.permissions:
            self.permissions.append(permission)
            self.updated_at = datetime.utcnow()
    
    def remove_permission(self, permission: Permission):
        """移除权限"""
        if permission in self.permissions:
            self.permissions.remove(permission)
            self.updated_at = datetime.utcnow()
    
    def has_permission(self, resource: str, level: PermissionLevel) -> bool:
        """检查角色是否具有指定权限"""
        return any(perm.matches(resource, level) for perm in self.permissions)


@dataclass
class User:
    """用户模型"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    username: str = ""
    email: str = ""
    password_hash: str = ""
    salt: str = field(default_factory=lambda: secrets.token_hex(16))
    
    # 基本信息
    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    
    # 状态和时间
    status: UserStatus = UserStatus.PENDING_ACTIVATION
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    
    # 角色和权限
    roles: List[Role] = field(default_factory=list)
    
    # 安全设置
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    password_changed_at: Optional[datetime] = None
    must_change_password: bool = False
    
    # MFA设置
    mfa_enabled: bool = False
    mfa_secret: Optional[str] = None
    backup_codes: List[str] = field(default_factory=list)
    
    # 会话追踪
    max_concurrent_sessions: int = 3
    active_sessions: Set[str] = field(default_factory=set)
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.display_name and (self.first_name or self.last_name):
            self.display_name = f"{self.first_name} {self.last_name}".strip()
        elif not self.display_name:
            self.display_name = self.username
    
    @property
    def full_name(self) -> str:
        """获取用户全名"""
        return f"{self.first_name} {self.last_name}".strip()
    
    @property
    def is_active(self) -> bool:
        """检查用户是否激活"""
        return self.status == UserStatus.ACTIVE
    
    @property
    def is_locked(self) -> bool:
        """检查用户是否被锁定"""
        if self.status == UserStatus.LOCKED:
            return True
        if self.locked_until and datetime.utcnow() < self.locked_until:
            return True
        return False
    
    def add_role(self, role: Role):
        """添加角色"""
        if role not in self.roles:
            self.roles.append(role)
            self.updated_at = datetime.utcnow()
    
    def remove_role(self, role: Role):
        """移除角色"""
        if role in self.roles:
            self.roles.remove(role)
            self.updated_at = datetime.utcnow()
    
    def has_permission(self, resource: str, level: PermissionLevel) -> bool:
        """检查用户是否具有指定权限"""
        if not self.is_active or self.is_locked:
            return False
        
        return any(role.has_permission(resource, level) for role in self.roles)
    
    def has_role(self, role_name: str) -> bool:
        """检查用户是否具有指定角色"""
        return any(role.name == role_name for role in self.roles)
    
    def lock_account(self, duration_minutes: int = None):
        """锁定用户账户"""
        self.status = UserStatus.LOCKED
        if duration_minutes:
            self.locked_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
        self.updated_at = datetime.utcnow()
    
    def unlock_account(self):
        """解锁用户账户"""
        if self.status == UserStatus.LOCKED:
            self.status = UserStatus.ACTIVE
        self.locked_until = None
        self.failed_login_attempts = 0
        self.updated_at = datetime.utcnow()
    
    def increment_failed_login(self):
        """增加失败登录次数"""
        self.failed_login_attempts += 1
        self.updated_at = datetime.utcnow()
    
    def reset_failed_login(self):
        """重置失败登录次数"""
        self.failed_login_attempts = 0
        self.updated_at = datetime.utcnow()
    
    def update_last_activity(self):
        """更新最后活动时间"""
        self.last_activity = datetime.utcnow()
    
    def can_create_session(self) -> bool:
        """检查是否可以创建新会话"""
        return len(self.active_sessions) < self.max_concurrent_sessions


@dataclass
class Session:
    """会话模型"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = ""
    token: str = field(default_factory=lambda: secrets.token_urlsafe(64))
    refresh_token: str = field(default_factory=lambda: secrets.token_urlsafe(64))
    
    # 时间信息
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(hours=24))
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    
    # 状态
    status: SessionStatus = SessionStatus.ACTIVE
    
    # 客户端信息
    ip_address: str = ""
    user_agent: str = ""
    device_fingerprint: str = ""
    
    # 会话上下文
    context: Dict[str, Any] = field(default_factory=dict)
    
    # 安全标记
    is_secure: bool = True
    is_http_only: bool = True
    same_site: str = "Strict"
    
    @property
    def is_valid(self) -> bool:
        """检查会话是否有效"""
        return (
            self.status == SessionStatus.ACTIVE and
            datetime.utcnow() < self.expires_at
        )
    
    @property
    def is_expired(self) -> bool:
        """检查会话是否过期"""
        return datetime.utcnow() >= self.expires_at
    
    def refresh(self, extend_hours: int = 24):
        """刷新会话"""
        if self.is_valid:
            self.expires_at = datetime.utcnow() + timedelta(hours=extend_hours)
            self.last_accessed = datetime.utcnow()
            self.refresh_token = secrets.token_urlsafe(64)
    
    def revoke(self):
        """撤销会话"""
        self.status = SessionStatus.REVOKED
    
    def update_access(self):
        """更新访问时间"""
        self.last_accessed = datetime.utcnow()


class AuthConfig(BaseModel):
    """认证配置模型"""
    
    # 密码策略
    password_min_length: int = Field(default=8, ge=6, le=128)
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_numbers: bool = True
    password_require_symbols: bool = True
    password_history_size: int = Field(default=5, ge=0, le=20)
    
    # 账户锁定策略
    max_failed_attempts: int = Field(default=5, ge=1, le=20)
    lockout_duration_minutes: int = Field(default=30, ge=1, le=1440)
    auto_unlock_after_duration: bool = True
    
    # 会话管理
    session_timeout_hours: int = Field(default=24, ge=1, le=168)
    session_refresh_threshold_hours: int = Field(default=4, ge=1, le=24)
    max_concurrent_sessions_per_user: int = Field(default=3, ge=1, le=10)
    remember_me_duration_days: int = Field(default=30, ge=1, le=365)
    
    # 令牌设置
    access_token_expires_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_expires_days: int = Field(default=7, ge=1, le=30)
    jwt_secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(64))
    jwt_algorithm: str = "HS256"
    
    # MFA设置
    mfa_token_validity_seconds: int = Field(default=30, ge=10, le=300)
    backup_codes_count: int = Field(default=8, ge=5, le=20)
    
    # 安全策略
    enable_brute_force_protection: bool = True
    enable_session_hijacking_detection: bool = True
    enable_suspicious_activity_detection: bool = True
    require_password_change_days: int = Field(default=90, ge=0, le=365)
    
    # 日志和审计
    enable_security_logging: bool = True
    log_failed_attempts: bool = True
    log_successful_logins: bool = True
    log_session_activities: bool = True
    
    @validator('jwt_secret_key')
    def validate_jwt_secret(cls, v):
        if len(v) < 32:
            raise ValueError('JWT secret key must be at least 32 characters')
        return v
    
    @validator('session_refresh_threshold_hours')
    def validate_refresh_threshold(cls, v, values):
        if 'session_timeout_hours' in values and v >= values['session_timeout_hours']:
            raise ValueError('Session refresh threshold must be less than session timeout')
        return v


# 默认角色定义
DEFAULT_ROLES = {
    "super_admin": Role(
        name="super_admin",
        description="超级管理员，拥有所有权限",
        permissions=[
            Permission(name="all", resource="*", level=PermissionLevel.SUPER_ADMIN)
        ],
        is_system_role=True
    ),
    "admin": Role(
        name="admin", 
        description="系统管理员，拥有管理权限",
        permissions=[
            Permission(name="system_admin", resource="system", level=PermissionLevel.ADMIN),
            Permission(name="user_admin", resource="users", level=PermissionLevel.ADMIN),
            Permission(name="task_admin", resource="tasks", level=PermissionLevel.ADMIN)
        ],
        is_system_role=True
    ),
    "user": Role(
        name="user",
        description="普通用户，基本权限",
        permissions=[
            Permission(name="task_read", resource="tasks", level=PermissionLevel.READ),
            Permission(name="task_write", resource="tasks", level=PermissionLevel.WRITE),
            Permission(name="profile_read", resource="profile", level=PermissionLevel.READ),
            Permission(name="profile_write", resource="profile", level=PermissionLevel.WRITE)
        ],
        is_system_role=True
    ),
    "readonly": Role(
        name="readonly",
        description="只读用户，仅查看权限", 
        permissions=[
            Permission(name="task_read", resource="tasks", level=PermissionLevel.READ),
            Permission(name="profile_read", resource="profile", level=PermissionLevel.READ)
        ],
        is_system_role=True
    )
}