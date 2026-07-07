"""
Pydantic models for MCP Plan File Manager

Provides data validation and serialization models.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field


class Project(BaseModel):
    """Project model"""
    id: Optional[UUID] = None
    name: str
    description: Optional[str] = ""
    project_root: Optional[str] = None
    plan_directory: str = ".plans"
    auto_gitignore: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PlanFile(BaseModel):
    """Plan file model"""
    id: Optional[UUID] = None
    project_id: UUID
    name: str
    description: Optional[str] = ""
    current_version: int = 1
    auto_version: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Version(BaseModel):
    """Version model"""
    id: Optional[UUID] = None
    plan_file_id: UUID
    version: int
    file_path: str
    content_hash: str
    created_by: str = "user"
    created_at: Optional[datetime] = None
    notes: Optional[str] = ""

    class Config:
        from_attributes = True


# Request/Response Schemas

class CreateProjectRequest(BaseModel):
    """Request to create a project"""
    name: str = Field(..., description="Project name (must be unique)")
    description: str = Field("", description="Project description")
    project_root: Optional[str] = Field(None, description="Absolute path to project root (auto-detected if not provided)")
    plan_directory: str = Field(".plans", description="Relative path for plan files within project")
    auto_gitignore: bool = Field(True, description="Auto-update .gitignore to exclude plan files")


class UpdateProjectRequest(BaseModel):
    """Request to update a project"""
    project_root: Optional[str] = None
    plan_directory: Optional[str] = None
    auto_gitignore: Optional[bool] = None
    description: Optional[str] = None


class CreatePlanFileRequest(BaseModel):
    """Request to create a plan file"""
    project_id: UUID = Field(..., description="ID of the project")
    name: str = Field(..., description="Plan file name (without .md extension)")
    content: str = Field(..., description="Plan file content (markdown body, without frontmatter)")
    description: str = Field("", description="Plan file description")
    created_by: str = Field("user", description="Who created this plan file (user, claude, codex)")


class UpdatePlanFileRequest(BaseModel):
    """Request to update a plan file (creates new version)"""
    plan_file_id: UUID = Field(..., description="ID of the plan file to update")
    content: str = Field(..., description="Updated content")
    notes: str = Field("", description="Version notes / changelog")
    created_by: str = Field("user", description="Who created this version")


class PlanConfig(BaseModel):
    """Plan file configuration"""
    plan_directory: str
    project_root: Optional[str] = None
    full_path_example: Optional[str] = None
    file_format: dict = {
        "frontmatter_required": True,
        "frontmatter_fields": ["mcp_plan_file", "project_id", "version", "created_by"],
        "version_suffix": True
    }
    naming_convention: str = "{plan_name}_v{version}.md"
    auto_gitignore: bool = True


# Response Models

class ProjectResponse(BaseModel):
    """Response for project operations"""
    id: UUID
    name: str
    description: str
    project_root: Optional[str]
    plan_directory: str
    auto_gitignore: bool
    full_plan_path: Optional[str] = None
    message: Optional[str] = None

    class Config:
        from_attributes = True


class PlanFileResponse(BaseModel):
    """Response for plan file operations"""
    id: UUID
    name: str
    version: int
    file_path: str
    message: Optional[str] = None

    class Config:
        from_attributes = True


class VersionResponse(BaseModel):
    """Response for version operations"""
    id: UUID
    version: int
    file_path: str
    content_hash: str
    created_by: str
    created_at: datetime
    notes: str
    message: Optional[str] = None

    class Config:
        from_attributes = True


class PlanFileWithContent(BaseModel):
    """Plan file with actual content"""
    plan_file: PlanFile
    version: Version
    content: str
    frontmatter: dict


class ProjectWithStats(BaseModel):
    """Project with statistics"""
    project: Project
    total_plan_files: int
    total_versions: int
    last_updated: Optional[datetime]


# JIRA Models

class JiraConfig(BaseModel):
    """JIRA configuration model"""
    id: Optional[UUID] = None
    project_id: UUID
    jira_url: str
    jira_project_key: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class JiraLink(BaseModel):
    """JIRA link model"""
    id: Optional[UUID] = None
    plan_file_id: UUID
    jira_issue_key: str
    jira_issue_type: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: str = "user"

    class Config:
        from_attributes = True


class JiraConfigRequest(BaseModel):
    """Request to configure JIRA for a project"""
    project_id: UUID = Field(..., description="ID of the project")
    jira_url: str = Field(..., description="JIRA base URL (e.g., https://company.atlassian.net)")
    jira_project_key: Optional[str] = Field(None, description="Default JIRA project key (e.g., PROJ)")


class JiraLinkRequest(BaseModel):
    """Request to link a plan file to a JIRA issue"""
    plan_file_id: UUID = Field(..., description="ID of the plan file")
    jira_issue_key: str = Field(..., description="JIRA issue key (e.g., PROJ-123)")
    jira_issue_type: Optional[str] = Field(None, description="JIRA issue type (Epic, Story, Task, etc.)")
    notes: Optional[str] = Field(None, description="Notes about the link")
    created_by: str = Field("user", description="Who created the link")


class JiraLinkUpdateRequest(BaseModel):
    """Request to update a JIRA link"""
    jira_issue_type: Optional[str] = None
    notes: Optional[str] = None


class JiraLinkResponse(BaseModel):
    """Response for JIRA link operations"""
    id: UUID
    plan_file_id: UUID
    jira_issue_key: str
    jira_issue_type: Optional[str]
    notes: Optional[str]
    jira_url: Optional[str] = None
    created_at: datetime
    created_by: str
    message: Optional[str] = None

    class Config:
        from_attributes = True
