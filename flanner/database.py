"""
Database layer for Flanner

Provides SQLAlchemy models and database operations.
"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship, sessionmaker
from sqlalchemy.types import CHAR, TypeDecorator

from .exceptions import DatabaseError, DuplicateError, NotFoundError

logger = logging.getLogger(__name__)

Base = declarative_base()


class GUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses PostgreSQL's UUID type on PostgreSQL, otherwise uses
    CHAR(32), storing as stringified hex values.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                return str(uuid.UUID(value))
            else:
                return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                value = uuid.UUID(value)
            return value


class ProjectModel(Base):
    """Project model - represents a project with plan files"""

    __tablename__ = "projects"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text)
    project_root = Column(String)  # Absolute path to project root (where .git is)
    plan_directory = Column(String, default=".plans")  # Relative path within project
    auto_gitignore = Column(Boolean, default=True)  # Auto-update .gitignore
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    plan_files = relationship(
        "PlanFileModel", back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Project(id={self.id}, name='{self.name}')>"


class PlanFileModel(Base):
    """Plan file model - represents a plan file with multiple versions"""

    __tablename__ = "plan_files"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    project_id = Column(GUID, ForeignKey("projects.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # Plan name (without .md extension)
    description = Column(Text)
    current_version = Column(Integer, default=1)
    auto_version = Column(Boolean, default=True)  # Auto-increment version on update
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("ProjectModel", back_populates="plan_files")
    versions = relationship(
        "VersionModel", back_populates="plan_file", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<PlanFile(id={self.id}, name='{self.name}', version={self.current_version})>"


class VersionModel(Base):
    """Version model - represents a specific version of a plan file"""

    __tablename__ = "versions"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    plan_file_id = Column(GUID, ForeignKey("plan_files.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    file_path = Column(String, nullable=False)  # Absolute path to the markdown file
    content_hash = Column(String)  # SHA256 hash for change detection
    created_by = Column(String, default="user")  # 'user', 'claude', 'codex', etc.
    created_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text)  # Version notes/changelog

    # Relationships
    plan_file = relationship("PlanFileModel", back_populates="versions")

    def __repr__(self):
        return f"<Version(id={self.id}, version={self.version}, created_by='{self.created_by}')>"


class JiraConfigModel(Base):
    """JIRA configuration model - stores JIRA settings per project"""

    __tablename__ = "jira_config"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    project_id = Column(GUID, ForeignKey("projects.id"), unique=True, nullable=False, index=True)
    jira_url = Column(String, nullable=False)  # Base URL (e.g., https://company.atlassian.net)
    jira_project_key = Column(String)  # Default JIRA project key (e.g., PROJ)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    project = relationship("ProjectModel", backref="jira_config", uselist=False)

    def __repr__(self):
        return (
            f"<JiraConfig(id={self.id}, project_id={self.project_id}, jira_url='{self.jira_url}')>"
        )


class JiraLinkModel(Base):
    """JIRA link model - links plan files to JIRA issues"""

    __tablename__ = "jira_links"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    plan_file_id = Column(GUID, ForeignKey("plan_files.id"), nullable=False, index=True)
    jira_issue_key = Column(String, nullable=False)  # e.g., PROJ-123
    jira_issue_type = Column(String)  # Epic, Story, Task, Sub-task, etc.
    notes = Column(Text)  # User notes about the link
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String, default="user")  # Who created the link

    # Relationships
    plan_file = relationship("PlanFileModel", backref="jira_links")

    def __repr__(self):
        return (
            f"<JiraLink(id={self.id}, plan_file_id={self.plan_file_id}, "
            f"issue_key='{self.jira_issue_key}')>"
        )


# Database session management
_engine = None
_SessionLocal = None


def init_database(db_path: str | None = None) -> None:
    """
    Initialize the database and create tables.

    Args:
        db_path: Path to SQLite database file. If None, uses default ~/.flanner/data.db
    """
    global _engine, _SessionLocal

    if db_path is None:
        # Default path
        mcp_dir = Path.home() / ".flanner"
        mcp_dir.mkdir(exist_ok=True)
        db_path = str(mcp_dir / "data.db")
    else:
        # Ensure parent directory exists
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

    # Create engine
    _engine = create_engine(f"sqlite:///{db_path}", echo=False)

    # Create tables
    Base.metadata.create_all(_engine)

    # Create session factory
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    logger.info("Database initialized at: %s", db_path)


def get_session() -> Session:
    """
    Get a database session.

    Returns:
        SQLAlchemy session

    Raises:
        RuntimeError: If database hasn't been initialized
    """
    if _SessionLocal is None:
        raise DatabaseError("Database not initialized. Call init_database() first.")

    return _SessionLocal()


def get_db_path() -> str | None:
    """Get the current database path"""
    if _engine is None:
        return None
    return str(_engine.url).replace("sqlite:///", "")


# CRUD Operations


def _commit(session: Session) -> None:
    """Commit, rolling back on failure so the session stays usable."""
    try:
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        raise DatabaseError(f"Database write failed: {e}") from e


def create_project(
    session: Session,
    name: str,
    description: str = "",
    project_root: str | None = None,
    plan_directory: str = ".plans",
    auto_gitignore: bool = True,
) -> ProjectModel:
    """
    Create a new project.

    Args:
        session: Database session
        name: Project name (must be unique)
        description: Project description
        project_root: Absolute path to project root
        plan_directory: Relative path for plan files
        auto_gitignore: Whether to auto-update .gitignore

    Returns:
        Created project model

    Raises:
        ValueError: If project with same name already exists
    """
    # Check if project exists
    existing = session.query(ProjectModel).filter_by(name=name).first()
    if existing:
        raise DuplicateError(f"Project '{name}' already exists")

    project = ProjectModel(
        name=name,
        description=description,
        project_root=project_root,
        plan_directory=plan_directory,
        auto_gitignore=auto_gitignore,
    )
    session.add(project)
    _commit(session)
    session.refresh(project)

    return project


def get_project(session: Session, project_id: uuid.UUID) -> ProjectModel | None:
    """Get project by ID"""
    return session.query(ProjectModel).filter_by(id=project_id).first()


def get_project_by_name(session: Session, name: str) -> ProjectModel | None:
    """Get project by name"""
    return session.query(ProjectModel).filter_by(name=name).first()


def list_projects(session: Session) -> list[ProjectModel]:
    """List all projects"""
    return session.query(ProjectModel).order_by(ProjectModel.created_at.desc()).all()


def update_project(
    session: Session,
    project_id: uuid.UUID,
    project_root: str | None = None,
    plan_directory: str | None = None,
    auto_gitignore: bool | None = None,
    description: str | None = None,
) -> ProjectModel | None:
    """Update project configuration"""
    project = session.query(ProjectModel).filter_by(id=project_id).first()
    if not project:
        return None

    if project_root is not None:
        project.project_root = project_root
    if plan_directory is not None:
        project.plan_directory = plan_directory
    if auto_gitignore is not None:
        project.auto_gitignore = auto_gitignore
    if description is not None:
        project.description = description

    project.updated_at = datetime.utcnow()
    _commit(session)
    session.refresh(project)

    return project


def create_plan_file(
    session: Session,
    project_id: uuid.UUID,
    name: str,
    description: str = "",
    auto_version: bool = True,
) -> PlanFileModel:
    """
    Create a new plan file.

    Args:
        session: Database session
        project_id: ID of the project
        name: Plan file name (without .md extension)
        description: Plan file description
        auto_version: Whether to auto-increment version on update

    Returns:
        Created plan file model

    Raises:
        ValueError: If project doesn't exist or plan file already exists
    """
    # Check if project exists
    project = session.query(ProjectModel).filter_by(id=project_id).first()
    if not project:
        raise NotFoundError(f"Project with ID {project_id} not found")

    # Check if plan file already exists
    existing = session.query(PlanFileModel).filter_by(project_id=project_id, name=name).first()
    if existing:
        raise DuplicateError(f"Plan file '{name}' already exists in project '{project.name}'")

    plan_file = PlanFileModel(
        project_id=project_id,
        name=name,
        description=description,
        current_version=1,
        auto_version=auto_version,
    )
    session.add(plan_file)
    _commit(session)
    session.refresh(plan_file)

    return plan_file


def get_plan_file(session: Session, plan_file_id: uuid.UUID) -> PlanFileModel | None:
    """Get plan file by ID"""
    return session.query(PlanFileModel).filter_by(id=plan_file_id).first()


def list_plan_files(session: Session, project_id: uuid.UUID) -> list[PlanFileModel]:
    """List all plan files for a project"""
    return (
        session.query(PlanFileModel)
        .filter_by(project_id=project_id)
        .order_by(PlanFileModel.created_at.desc())
        .all()
    )


def create_version(
    session: Session,
    plan_file_id: uuid.UUID,
    version: int,
    file_path: str,
    content_hash: str,
    created_by: str = "user",
    notes: str = "",
) -> VersionModel:
    """
    Create a new version of a plan file.

    Args:
        session: Database session
        plan_file_id: ID of the plan file
        version: Version number
        file_path: Absolute path to the markdown file
        content_hash: SHA256 hash of content
        created_by: Who created this version
        notes: Version notes

    Returns:
        Created version model
    """
    version_model = VersionModel(
        plan_file_id=plan_file_id,
        version=version,
        file_path=file_path,
        content_hash=content_hash,
        created_by=created_by,
        notes=notes,
    )
    session.add(version_model)
    _commit(session)
    session.refresh(version_model)

    return version_model


def get_version(
    session: Session, plan_file_id: uuid.UUID, version: int | None = None
) -> VersionModel | None:
    """
    Get a specific version or the latest version.

    Args:
        session: Database session
        plan_file_id: ID of the plan file
        version: Version number (if None, returns latest)

    Returns:
        Version model or None
    """
    query = session.query(VersionModel).filter_by(plan_file_id=plan_file_id)

    if version is not None:
        return query.filter_by(version=version).first()
    else:
        return query.order_by(VersionModel.version.desc()).first()


def list_versions(session: Session, plan_file_id: uuid.UUID) -> list[VersionModel]:
    """List all versions of a plan file"""
    return (
        session.query(VersionModel)
        .filter_by(plan_file_id=plan_file_id)
        .order_by(VersionModel.version.desc())
        .all()
    )


def delete_project(session: Session, project_id: uuid.UUID) -> bool:
    """
    Delete a project and all associated plan files and versions.

    Args:
        session: Database session
        project_id: ID of the project to delete

    Returns:
        True if deleted, False if project not found
    """
    project = session.query(ProjectModel).filter_by(id=project_id).first()
    if not project:
        return False

    session.delete(project)
    _commit(session)

    return True


def get_project_by_root(session: Session, project_root: str) -> ProjectModel | None:
    """
    Get project by its project_root path.

    Args:
        session: Database session
        project_root: Absolute path to project root

    Returns:
        ProjectModel or None if not found
    """
    normalized_root = os.path.normpath(project_root)

    # Query all projects and compare normalized paths
    projects = session.query(ProjectModel).all()
    for project in projects:
        if project.project_root:
            if os.path.normpath(project.project_root) == normalized_root:
                return project

    return None


# JIRA Configuration Operations


def create_jira_config(
    session: Session, project_id: uuid.UUID, jira_url: str, jira_project_key: str | None = None
) -> JiraConfigModel:
    """
    Create or update JIRA configuration for a project.

    Args:
        session: Database session
        project_id: ID of the project
        jira_url: JIRA base URL (e.g., https://company.atlassian.net)
        jira_project_key: Default JIRA project key (e.g., PROJ)

    Returns:
        Created or updated JIRA config model

    Raises:
        ValueError: If project doesn't exist
    """
    # Check if project exists
    project = session.query(ProjectModel).filter_by(id=project_id).first()
    if not project:
        raise NotFoundError(f"Project with ID {project_id} not found")

    # Check if config already exists
    existing = session.query(JiraConfigModel).filter_by(project_id=project_id).first()
    if existing:
        # Update existing config
        existing.jira_url = jira_url
        if jira_project_key is not None:
            existing.jira_project_key = jira_project_key
        existing.updated_at = datetime.utcnow()
        _commit(session)
        session.refresh(existing)
        return existing
    else:
        # Create new config
        jira_config = JiraConfigModel(
            project_id=project_id, jira_url=jira_url, jira_project_key=jira_project_key
        )
        session.add(jira_config)
        _commit(session)
        session.refresh(jira_config)
        return jira_config


def get_jira_config(session: Session, project_id: uuid.UUID) -> JiraConfigModel | None:
    """Get JIRA configuration for a project"""
    return session.query(JiraConfigModel).filter_by(project_id=project_id).first()


def delete_jira_config(session: Session, project_id: uuid.UUID) -> bool:
    """
    Delete JIRA configuration for a project.

    Args:
        session: Database session
        project_id: ID of the project

    Returns:
        True if deleted, False if config not found
    """
    config = session.query(JiraConfigModel).filter_by(project_id=project_id).first()
    if not config:
        return False

    session.delete(config)
    _commit(session)
    return True


# JIRA Link Operations


def create_jira_link(
    session: Session,
    plan_file_id: uuid.UUID,
    jira_issue_key: str,
    jira_issue_type: str | None = None,
    notes: str | None = None,
    created_by: str = "user",
) -> JiraLinkModel:
    """
    Create a JIRA link for a plan file.

    Args:
        session: Database session
        plan_file_id: ID of the plan file
        jira_issue_key: JIRA issue key (e.g., PROJ-123)
        jira_issue_type: Type of JIRA issue (Epic, Story, Task, etc.)
        notes: Notes about the link
        created_by: Who created the link

    Returns:
        Created JIRA link model

    Raises:
        ValueError: If plan file doesn't exist or link already exists
    """
    # Check if plan file exists
    plan_file = session.query(PlanFileModel).filter_by(id=plan_file_id).first()
    if not plan_file:
        raise NotFoundError(f"Plan file with ID {plan_file_id} not found")

    # Check if link already exists
    existing = (
        session.query(JiraLinkModel)
        .filter_by(plan_file_id=plan_file_id, jira_issue_key=jira_issue_key)
        .first()
    )
    if existing:
        raise DuplicateError(f"Plan file is already linked to {jira_issue_key}")

    jira_link = JiraLinkModel(
        plan_file_id=plan_file_id,
        jira_issue_key=jira_issue_key,
        jira_issue_type=jira_issue_type,
        notes=notes,
        created_by=created_by,
    )
    session.add(jira_link)
    _commit(session)
    session.refresh(jira_link)

    return jira_link


def get_jira_links(session: Session, plan_file_id: uuid.UUID) -> list[JiraLinkModel]:
    """Get all JIRA links for a plan file"""
    return (
        session.query(JiraLinkModel)
        .filter_by(plan_file_id=plan_file_id)
        .order_by(JiraLinkModel.created_at.desc())
        .all()
    )


def get_jira_link(session: Session, link_id: uuid.UUID) -> JiraLinkModel | None:
    """Get a specific JIRA link by ID"""
    return session.query(JiraLinkModel).filter_by(id=link_id).first()


def update_jira_link(
    session: Session,
    link_id: uuid.UUID,
    jira_issue_type: str | None = None,
    notes: str | None = None,
) -> JiraLinkModel | None:
    """
    Update a JIRA link.

    Args:
        session: Database session
        link_id: ID of the link to update
        jira_issue_type: New issue type
        notes: New notes

    Returns:
        Updated JIRA link model or None if not found
    """
    link = session.query(JiraLinkModel).filter_by(id=link_id).first()
    if not link:
        return None

    if jira_issue_type is not None:
        link.jira_issue_type = jira_issue_type
    if notes is not None:
        link.notes = notes

    _commit(session)
    session.refresh(link)
    return link


def delete_jira_link(session: Session, link_id: uuid.UUID) -> bool:
    """
    Delete a JIRA link.

    Args:
        session: Database session
        link_id: ID of the link to delete

    Returns:
        True if deleted, False if link not found
    """
    link = session.query(JiraLinkModel).filter_by(id=link_id).first()
    if not link:
        return False

    session.delete(link)
    _commit(session)
    return True


def delete_jira_link_by_key(
    session: Session, plan_file_id: uuid.UUID, jira_issue_key: str
) -> bool:
    """
    Delete a JIRA link by plan file ID and issue key.

    Args:
        session: Database session
        plan_file_id: ID of the plan file
        jira_issue_key: JIRA issue key to unlink

    Returns:
        True if deleted, False if link not found
    """
    link = (
        session.query(JiraLinkModel)
        .filter_by(plan_file_id=plan_file_id, jira_issue_key=jira_issue_key)
        .first()
    )
    if not link:
        return False

    session.delete(link)
    _commit(session)
    return True


def delete_all_jira_links(session: Session, plan_file_id: uuid.UUID) -> int:
    """
    Delete all JIRA links for a plan file.

    Args:
        session: Database session
        plan_file_id: ID of the plan file

    Returns:
        Number of links deleted
    """
    links = session.query(JiraLinkModel).filter_by(plan_file_id=plan_file_id).all()
    count = len(links)

    for link in links:
        session.delete(link)

    _commit(session)
    return count


def list_all_jira_links(session: Session, project_id: uuid.UUID) -> list[dict]:
    """
    List all JIRA links for all plan files in a project.

    Args:
        session: Database session
        project_id: ID of the project

    Returns:
        List of dicts with plan file and JIRA link information
    """
    # Get all plan files for the project
    plan_files = session.query(PlanFileModel).filter_by(project_id=project_id).all()

    results = []
    for plan_file in plan_files:
        links = session.query(JiraLinkModel).filter_by(plan_file_id=plan_file.id).all()
        for link in links:
            results.append(
                {
                    "plan_file_id": plan_file.id,
                    "plan_file_name": plan_file.name,
                    "jira_link_id": link.id,
                    "jira_issue_key": link.jira_issue_key,
                    "jira_issue_type": link.jira_issue_type,
                    "notes": link.notes,
                    "created_at": link.created_at,
                    "created_by": link.created_by,
                }
            )

    return results
