"""
NovelForge 任务系统
提供持久化任务队列、状态机和检查点恢复
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from enum import Enum
from dataclasses import dataclass
from threading import Lock

from .database import get_db, generate_id

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """任务类型"""
    WRITE = "write"
    CONTINUOUS = "continuous"
    REVIEW = "review"
    EXPORT = "export"
    IMPORT = "import"
    BACKUP = "backup"


class TaskStatus(str, Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


class ContinuousStage(str, Enum):
    """连续创作阶段"""
    PREPARE = "prepare"
    WRITE_CHAPTER = "write_chapter"
    REVIEW_CHAPTER = "review_chapter"
    REVISION = "revision"
    QUALITY_GATE = "quality_gate"
    COMMIT_CHAPTER = "commit_chapter"
    CHECK_JOINT_REVIEW = "check_joint_review"
    NEXT_CHAPTER = "next_chapter"
    COMPLETE = "complete"


@dataclass
class TaskCheckpoint:
    """任务检查点"""
    stage: str
    state: Dict[str, Any]
    chapter_number: int = 0
    timestamp: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()


@dataclass
class Task:
    """任务"""
    id: str
    type: str
    status: str
    book_id: Optional[str] = None
    chapter_number: Optional[int] = None
    data: Optional[Dict] = None
    result: Optional[Dict] = None
    error: Optional[str] = None
    progress: int = 0
    total_steps: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
        if self.updated_at is None:
            self.updated_at = self.created_at


class TaskManager:
    """任务管理器"""
    
    def __init__(self):
        self.db = get_db()
        self._lock = Lock()
        self._callbacks: Dict[str, List[Callable]] = {}
    
    def create_task(
        self,
        task_type: TaskType,
        book_id: Optional[str] = None,
        chapter_number: Optional[int] = None,
        data: Optional[Dict] = None
    ) -> Task:
        """创建新任务"""
        task_id = generate_id()
        
        task_data = {
            'id': task_id,
            'type': task_type.value if isinstance(task_type, TaskType) else task_type,
            'status': TaskStatus.PENDING.value,
            'book_id': book_id,
            'chapter_number': chapter_number,
            'data': json.dumps(data) if data else None,
        }
        
        self.db.insert('tasks', task_data)
        
        task = Task(
            id=task_id,
            type=task_data['type'],
            status=task_data['status'],
            book_id=book_id,
            chapter_number=chapter_number,
            data=data,
        )
        
        logger.info(f"任务创建: {task_id} ({task_type})")
        return task
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        row = self.db.get_by_id('tasks', task_id)
        if row is None:
            return None
        
        return Task(
            id=row['id'],
            type=row['type'],
            status=row['status'],
            book_id=row['book_id'],
            chapter_number=row['chapter_number'],
            data=json.loads(row['data']) if row['data'] else None,
            result=json.loads(row['result']) if row['result'] else None,
            error=row['error'],
            progress=row['progress'],
            total_steps=row['total_steps'],
            started_at=row['started_at'],
            completed_at=row['completed_at'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
        )
    
    def update_task(self, task_id: str, **kwargs) -> bool:
        """更新任务"""
        with self._lock:
            update_data = {}
            
            if 'status' in kwargs:
                status = kwargs['status']
                if isinstance(status, TaskStatus):
                    status = status.value
                update_data['status'] = status
                
                if status == TaskStatus.RUNNING.value and 'started_at' not in kwargs:
                    update_data['started_at'] = datetime.now().isoformat()
                elif status in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value]:
                    update_data['completed_at'] = datetime.now().isoformat()
            
            if 'progress' in kwargs:
                update_data['progress'] = kwargs['progress']
            
            if 'total_steps' in kwargs:
                update_data['total_steps'] = kwargs['total_steps']
            
            if 'result' in kwargs:
                update_data['result'] = json.dumps(kwargs['result'])
            
            if 'error' in kwargs:
                update_data['error'] = kwargs['error']
            
            if 'chapter_number' in kwargs:
                update_data['chapter_number'] = kwargs['chapter_number']
            
            if update_data:
                self.db.update('tasks', update_data, 'id = ?', (task_id,))
                
                # 触发回调
                self._trigger_callbacks(task_id, kwargs.get('status'))
            
            return True
    
    def start_task(self, task_id: str) -> bool:
        """启动任务"""
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.PENDING.value:
            return False
        
        self.update_task(task_id, status=TaskStatus.RUNNING)
        return True
    
    def complete_task(self, task_id: str, result: Optional[Dict] = None) -> bool:
        """完成任务"""
        self.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            result=result or {}
        )
        return True
    
    def fail_task(self, task_id: str, error: str) -> bool:
        """任务失败"""
        self.update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=error
        )
        return True
    
    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self.get_task(task_id)
        if task is None:
            return False
        
        if task.status in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value]:
            return False
        
        self.update_task(task_id, status=TaskStatus.CANCELLED)
        return True
    
    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.RUNNING.value:
            return False
        
        self.update_task(task_id, status=TaskStatus.PAUSED)
        return True
    
    def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        task = self.get_task(task_id)
        if task is None or task.status != TaskStatus.PAUSED.value:
            return False
        
        self.update_task(task_id, status=TaskStatus.RUNNING)
        return True
    
    def list_tasks(
        self,
        book_id: Optional[str] = None,
        task_type: Optional[TaskType] = None,
        status: Optional[TaskStatus] = None,
        limit: int = 50
    ) -> List[Task]:
        """列出任务"""
        conditions = []
        params = []
        
        if book_id:
            conditions.append("book_id = ?")
            params.append(book_id)
        
        if task_type:
            conditions.append("type = ?")
            params.append(task_type.value if isinstance(task_type, TaskType) else task_type)
        
        if status:
            conditions.append("status = ?")
            params.append(status.value if isinstance(status, TaskStatus) else status)
        
        where = " AND ".join(conditions) if conditions else ""
        
        rows = self.db.fetchall(
            f"SELECT * FROM tasks {'WHERE ' + where if where else ''} ORDER BY created_at DESC LIMIT ?",
            tuple(params) + (limit,)
        )
        
        tasks = []
        for row in rows:
            tasks.append(Task(
                id=row['id'],
                type=row['type'],
                status=row['status'],
                book_id=row['book_id'],
                chapter_number=row['chapter_number'],
                data=json.loads(row['data']) if row['data'] else None,
                result=json.loads(row['result']) if row['result'] else None,
                error=row['error'],
                progress=row['progress'],
                total_steps=row['total_steps'],
                started_at=row['started_at'],
                completed_at=row['completed_at'],
                created_at=row['created_at'],
                updated_at=row['updated_at'],
            ))
        
        return tasks
    
    def get_running_tasks(self) -> List[Task]:
        """获取正在运行的任务"""
        return self.list_tasks(status=TaskStatus.RUNNING)
    
    def get_pending_tasks(self) -> List[Task]:
        """获取待处理任务"""
        return self.list_tasks(status=TaskStatus.PENDING)
    
    def get_unfinished_tasks(self) -> List[Task]:
        """获取未完成任务（用于恢复）"""
        return self.list_tasks(status=TaskStatus.PAUSED) + \
               self.list_tasks(status=TaskStatus.RUNNING)
    
    # 检查点管理
    
    def save_checkpoint(self, task_id: str, stage: str, state: Dict[str, Any]) -> str:
        """保存检查点"""
        checkpoint_id = generate_id()
        
        checkpoint_data = {
            'id': checkpoint_id,
            'task_id': task_id,
            'stage': stage,
            'state': json.dumps(state),
        }
        
        self.db.insert('task_checkpoints', checkpoint_data)
        logger.info(f"检查点保存: {task_id} @ {stage}")
        return checkpoint_id
    
    def get_latest_checkpoint(self, task_id: str) -> Optional[TaskCheckpoint]:
        """获取最新检查点"""
        row = self.db.fetchone(
            "SELECT * FROM task_checkpoints WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,)
        )
        
        if row is None:
            return None
        
        return TaskCheckpoint(
            stage=row['stage'],
            state=json.loads(row['state']),
            timestamp=row['created_at']
        )
    
    def get_checkpoints(self, task_id: str) -> List[TaskCheckpoint]:
        """获取所有检查点"""
        rows = self.db.fetchall(
            "SELECT * FROM task_checkpoints WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,)
        )
        
        checkpoints = []
        for row in rows:
            checkpoints.append(TaskCheckpoint(
                stage=row['stage'],
                state=json.loads(row['state']),
                timestamp=row['created_at']
            ))
        
        return checkpoints
    
    def clear_checkpoints(self, task_id: str):
        """清除任务的所有检查点"""
        self.db.delete('task_checkpoints', 'task_id = ?', (task_id,))
    
    # 回调管理
    
    def register_callback(self, task_id: str, callback: Callable):
        """注册任务状态回调"""
        if task_id not in self._callbacks:
            self._callbacks[task_id] = []
        self._callbacks[task_id].append(callback)
    
    def _trigger_callbacks(self, task_id: str, status: Optional[str]):
        """触发回调"""
        if task_id in self._callbacks:
            for callback in self._callbacks[task_id]:
                try:
                    callback(task_id, status)
                except Exception as e:
                    logger.error(f"回调执行失败: {e}")
    
    # 统计
    
    def get_stats(self, book_id: Optional[str] = None) -> Dict[str, Any]:
        """获取任务统计"""
        where = "book_id = ?" if book_id else ""
        params = (book_id,) if book_id else ()
        
        stats = {
            'total': self.db.count('tasks', where, params),
            'pending': self.db.count('tasks', f"status = 'pending' {'AND ' + where if where else ''}", params),
            'running': self.db.count('tasks', f"status = 'running' {'AND ' + where if where else ''}", params),
            'completed': self.db.count('tasks', f"status = 'completed' {'AND ' + where if where else ''}", params),
            'failed': self.db.count('tasks', f"status = 'failed' {'AND ' + where if where else ''}", params),
            'cancelled': self.db.count('tasks', f"status = 'cancelled' {'AND ' + where if where else ''}", params),
        }
        
        return stats


# 全局任务管理器实例（线程安全）
import threading as _threading
_task_manager_lock = _threading.Lock()
_task_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """获取全局任务管理器"""
    global _task_manager
    if _task_manager is None:
        with _task_manager_lock:
            if _task_manager is None:
                _task_manager = TaskManager()
    return _task_manager
