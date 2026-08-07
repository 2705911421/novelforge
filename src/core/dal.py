"""
NovelForge 数据访问层
封装数据库操作，提供高级API
"""

import json
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from .database import get_db, generate_id

logger = logging.getLogger(__name__)


class ProjectDAL:
    """项目数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建项目"""
        project_id = generate_id()
        data['id'] = project_id
        if 'world_setting' in data and isinstance(data['world_setting'], dict):
            data['world_setting'] = json.dumps(data['world_setting'])
        self.db.insert('projects', data)
        return project_id
    
    def get(self, project_id: str) -> Optional[Dict]:
        """获取项目"""
        row = self.db.get_by_id('projects', project_id)
        if row and row.get('world_setting'):
            row['world_setting'] = json.loads(row['world_setting'])
        return row
    
    def update(self, project_id: str, data: Dict) -> bool:
        """更新项目"""
        if 'world_setting' in data and isinstance(data['world_setting'], dict):
            data['world_setting'] = json.dumps(data['world_setting'])
        rows = self.db.update('projects', data, 'id = ?', (project_id,))
        return rows > 0
    
    def delete(self, project_id: str) -> bool:
        """删除项目"""
        rows = self.db.delete('projects', 'id = ?', (project_id,))
        return rows > 0
    
    def list_all(self, limit: int = 100) -> List[Dict]:
        """列出所有项目"""
        return self.db.list_all('projects', order_by='updated_at DESC', limit=limit)


class BookDAL:
    """书籍数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建书籍"""
        book_id = generate_id()
        data['id'] = book_id
        self.db.insert('books', data)
        return book_id
    
    def get(self, book_id: str) -> Optional[Dict]:
        """获取书籍"""
        return self.db.get_by_id('books', book_id)
    
    def get_by_project(self, project_id: str) -> List[Dict]:
        """获取项目下的书籍"""
        return self.db.fetchall(
            "SELECT * FROM books WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,)
        )
    
    def update(self, book_id: str, data: Dict) -> bool:
        """更新书籍"""
        rows = self.db.update('books', data, 'id = ?', (book_id,))
        return rows > 0
    
    def delete(self, book_id: str) -> bool:
        """删除书籍"""
        rows = self.db.delete('books', 'id = ?', (book_id,))
        return rows > 0
    
    def update_stats(self, book_id: str):
        """更新书籍统计"""
        stats = self.db.fetchone(
            """SELECT 
                COUNT(*) as total_chapters,
                COALESCE(SUM(word_count), 0) as total_words
            FROM chapters WHERE book_id = ?""",
            (book_id,)
        )
        if stats:
            self.db.update('books', {
                'total_chapters': stats['total_chapters'],
                'total_words': stats['total_words']
            }, 'id = ?', (book_id,))


class ChapterDAL:
    """章节数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建章节"""
        chapter_id = generate_id()
        data['id'] = chapter_id
        
        # JSON 字段处理
        for field in ['key_events', 'characters_appeared', 'locations_used']:
            if field in data and isinstance(data[field], list):
                data[field] = json.dumps(data[field])
        
        self.db.insert('chapters', data)
        return chapter_id
    
    def get(self, chapter_id: str) -> Optional[Dict]:
        """获取章节"""
        row = self.db.get_by_id('chapters', chapter_id)
        if row:
            for field in ['key_events', 'characters_appeared', 'locations_used']:
                if row.get(field):
                    row[field] = json.loads(row[field])
        return row
    
    def get_by_number(self, book_id: str, number: int) -> Optional[Dict]:
        """根据编号获取章节"""
        row = self.db.fetchone(
            "SELECT * FROM chapters WHERE book_id = ? AND number = ?",
            (book_id, number)
        )
        if row:
            for field in ['key_events', 'characters_appeared', 'locations_used']:
                if row.get(field):
                    row[field] = json.loads(row[field])
        return row
    
    def get_latest_number(self, book_id: str) -> int:
        """获取最新章节编号"""
        row = self.db.fetchone(
            "SELECT MAX(number) as max_num FROM chapters WHERE book_id = ?",
            (book_id,)
        )
        return row['max_num'] if row and row['max_num'] else 0
    
    def list_by_book(self, book_id: str, limit: int = 100) -> List[Dict]:
        """列出书籍的章节"""
        return self.db.fetchall(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY number ASC LIMIT ?",
            (book_id, limit)
        )
    
    def update(self, chapter_id: str, data: Dict) -> bool:
        """更新章节"""
        for field in ['key_events', 'characters_appeared', 'locations_used']:
            if field in data and isinstance(data[field], list):
                data[field] = json.dumps(data[field])
        rows = self.db.update('chapters', data, 'id = ?', (chapter_id,))
        return rows > 0
    
    def delete(self, chapter_id: str) -> bool:
        """删除章节"""
        rows = self.db.delete('chapters', 'id = ?', (chapter_id,))
        return rows > 0
    
    def save_version(self, chapter_id: str, content: str, word_count: int, change_summary: str = "") -> str:
        """保存章节版本"""
        # 获取当前最大版本号
        row = self.db.fetchone(
            "SELECT MAX(version) as max_ver FROM chapter_versions WHERE chapter_id = ?",
            (chapter_id,)
        )
        version = (row['max_ver'] or 0) + 1
        
        version_id = generate_id()
        self.db.insert('chapter_versions', {
            'id': version_id,
            'chapter_id': chapter_id,
            'version': version,
            'content': content,
            'word_count': word_count,
            'change_summary': change_summary,
        })
        
        return version_id
    
    def get_versions(self, chapter_id: str) -> List[Dict]:
        """获取章节版本历史"""
        return self.db.fetchall(
            "SELECT * FROM chapter_versions WHERE chapter_id = ? ORDER BY version DESC",
            (chapter_id,)
        )


class CharacterDAL:
    """角色数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建角色"""
        char_id = generate_id()
        data['id'] = char_id
        self.db.insert('characters', data)
        return char_id
    
    def get(self, char_id: str) -> Optional[Dict]:
        """获取角色"""
        return self.db.get_by_id('characters', char_id)
    
    def list_by_book(self, book_id: str) -> List[Dict]:
        """列出书籍的角色"""
        return self.db.fetchall(
            "SELECT * FROM characters WHERE book_id = ? ORDER BY name",
            (book_id,)
        )
    
    def update(self, char_id: str, data: Dict) -> bool:
        """更新角色"""
        rows = self.db.update('characters', data, 'id = ?', (char_id,))
        return rows > 0
    
    def delete(self, char_id: str) -> bool:
        """删除角色"""
        rows = self.db.delete('characters', 'id = ?', (char_id,))
        return rows > 0
    
    def save_state(self, char_id: str, chapter_id: str, state: Dict) -> str:
        """保存角色状态"""
        state_id = generate_id()
        state_data = {
            'id': state_id,
            'character_id': char_id,
            'chapter_id': chapter_id,
        }
        
        # JSON 字段处理
        for field in ['relationships', 'knowledge']:
            if field in state and isinstance(state[field], (dict, list)):
                state[field] = json.dumps(state[field])
        
        state_data.update(state)
        self.db.insert('character_states', state_data)
        return state_id
    
    def get_states(self, char_id: str, limit: int = 50) -> List[Dict]:
        """获取角色状态历史"""
        rows = self.db.fetchall(
            "SELECT * FROM character_states WHERE character_id = ? ORDER BY created_at DESC LIMIT ?",
            (char_id, limit)
        )
        for row in rows:
            for field in ['relationships', 'knowledge']:
                if row.get(field):
                    row[field] = json.loads(row[field])
        return rows


class FactionDAL:
    """势力数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建势力"""
        faction_id = generate_id()
        data['id'] = faction_id
        self.db.insert('factions', data)
        return faction_id
    
    def get(self, faction_id: str) -> Optional[Dict]:
        """获取势力"""
        return self.db.get_by_id('factions', faction_id)
    
    def list_by_book(self, book_id: str) -> List[Dict]:
        """列出书籍的势力"""
        return self.db.fetchall(
            "SELECT * FROM factions WHERE book_id = ? ORDER BY name",
            (book_id,)
        )
    
    def update(self, faction_id: str, data: Dict) -> bool:
        """更新势力"""
        rows = self.db.update('factions', data, 'id = ?', (faction_id,))
        return rows > 0
    
    def delete(self, faction_id: str) -> bool:
        """删除势力"""
        rows = self.db.delete('factions', 'id = ?', (faction_id,))
        return rows > 0


class LocationDAL:
    """地点数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建地点"""
        location_id = generate_id()
        data['id'] = location_id
        self.db.insert('locations', data)
        return location_id
    
    def get(self, location_id: str) -> Optional[Dict]:
        """获取地点"""
        return self.db.get_by_id('locations', location_id)
    
    def list_by_book(self, book_id: str) -> List[Dict]:
        """列出书籍的地点"""
        return self.db.fetchall(
            "SELECT * FROM locations WHERE book_id = ? ORDER BY name",
            (book_id,)
        )
    
    def get_tree(self, book_id: str) -> List[Dict]:
        """获取地点层级树"""
        locations = self.list_by_book(book_id)
        # 构建树结构
        location_map = {loc['id']: loc for loc in locations}
        tree = []
        for loc in locations:
            loc['children'] = []
            if loc['parent_id'] and loc['parent_id'] in location_map:
                location_map[loc['parent_id']]['children'].append(loc)
            else:
                tree.append(loc)
        return tree
    
    def update(self, location_id: str, data: Dict) -> bool:
        """更新地点"""
        rows = self.db.update('locations', data, 'id = ?', (location_id,))
        return rows > 0
    
    def delete(self, location_id: str) -> bool:
        """删除地点"""
        rows = self.db.delete('locations', 'id = ?', (location_id,))
        return rows > 0


class ForeshadowDAL:
    """伏笔数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建伏笔"""
        fs_id = generate_id()
        data['id'] = fs_id
        self.db.insert('foreshadows', data)
        return fs_id
    
    def get(self, fs_id: str) -> Optional[Dict]:
        """获取伏笔"""
        return self.db.get_by_id('foreshadows', fs_id)
    
    def list_by_book(self, book_id: str, status: Optional[str] = None) -> List[Dict]:
        """列出书籍的伏笔"""
        if status:
            return self.db.fetchall(
                "SELECT * FROM foreshadows WHERE book_id = ? AND status = ? ORDER BY created_chapter",
                (book_id, status)
            )
        return self.db.fetchall(
            "SELECT * FROM foreshadows WHERE book_id = ? ORDER BY created_chapter",
            (book_id,)
        )
    
    def get_open(self, book_id: str) -> List[Dict]:
        """获取未解决的伏笔"""
        return self.list_by_book(book_id, status='open')
    
    def update(self, fs_id: str, data: Dict) -> bool:
        """更新伏笔"""
        rows = self.db.update('foreshadows', data, 'id = ?', (fs_id,))
        return rows > 0
    
    def resolve(self, fs_id: str, chapter_number: int) -> bool:
        """解决伏笔"""
        return self.update(fs_id, {
            'status': 'resolved',
            'resolved_chapter': chapter_number
        })


class StoryFactDAL:
    """Story Fact 数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建 Story Fact"""
        fact_id = generate_id()
        data['id'] = fact_id
        if 'entities' in data and isinstance(data['entities'], list):
            data['entities'] = json.dumps(data['entities'])
        self.db.insert('story_facts', data)
        return fact_id
    
    def create_batch(self, facts: List[Dict]) -> List[str]:
        """批量创建 Story Facts"""
        ids = []
        for fact in facts:
            fact_id = self.create(fact)
            ids.append(fact_id)
        return ids
    
    def list_by_book(self, book_id: str, limit: int = 100) -> List[Dict]:
        """列出书籍的 Story Facts"""
        rows = self.db.fetchall(
            "SELECT * FROM story_facts WHERE book_id = ? ORDER BY created_at DESC LIMIT ?",
            (book_id, limit)
        )
        for row in rows:
            if row.get('entities'):
                row['entities'] = json.loads(row['entities'])
        return rows
    
    def list_by_chapter(self, chapter_id: str) -> List[Dict]:
        """列出章节的 Story Facts"""
        rows = self.db.fetchall(
            "SELECT * FROM story_facts WHERE chapter_id = ? ORDER BY created_at",
            (chapter_id,)
        )
        for row in rows:
            if row.get('entities'):
                row['entities'] = json.loads(row['entities'])
        return rows
    
    def search(self, book_id: str, query: str) -> List[Dict]:
        """搜索 Story Facts"""
        rows = self.db.fetchall(
            "SELECT * FROM story_facts WHERE book_id = AND content LIKE ? ORDER BY created_at DESC",
            (book_id, f"%{query}%")
        )
        for row in rows:
            if row.get('entities'):
                row['entities'] = json.loads(row['entities'])
        return rows


class ReviewDAL:
    """审查数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建审查"""
        review_id = generate_id()
        data['id'] = review_id
        self.db.insert('reviews', data)
        return review_id
    
    def get(self, review_id: str) -> Optional[Dict]:
        """获取审查"""
        return self.db.get_by_id('reviews', review_id)
    
    def get_by_chapter(self, chapter_id: str) -> List[Dict]:
        """获取章节的审查"""
        return self.db.fetchall(
            "SELECT * FROM reviews WHERE chapter_id = ? ORDER BY created_at DESC",
            (chapter_id,)
        )
    
    def add_dimension(self, review_id: str, dimension: str, score: float, weight: float) -> str:
        """添加审查维度"""
        dim_id = generate_id()
        self.db.insert('review_dimensions', {
            'id': dim_id,
            'review_id': review_id,
            'dimension': dimension,
            'score': score,
            'weight': weight,
        })
        return dim_id
    
    def get_dimensions(self, review_id: str) -> List[Dict]:
        """获取审查维度"""
        return self.db.fetchall(
            "SELECT * FROM review_dimensions WHERE review_id = ?",
            (review_id,)
        )
    
    def add_issue(self, review_id: str, issue: Dict) -> str:
        """添加审查问题"""
        issue_id = generate_id()
        issue['id'] = issue_id
        issue['review_id'] = review_id
        self.db.insert('review_issues', issue)
        return issue_id
    
    def get_issues(self, review_id: str, status: Optional[str] = None) -> List[Dict]:
        """获取审查问题"""
        if status:
            return self.db.fetchall(
                "SELECT * FROM review_issues WHERE review_id = ? AND status = ? ORDER BY severity DESC",
                (review_id, status)
            )
        return self.db.fetchall(
            "SELECT * FROM review_issues WHERE review_id = ? ORDER BY severity DESC",
            (review_id,)
        )
    
    def update_issue(self, issue_id: str, data: Dict) -> bool:
        """更新审查问题"""
        rows = self.db.update('review_issues', data, 'id = ?', (issue_id,))
        return rows > 0
    
    def get_blocking_issues(self, review_id: str) -> List[Dict]:
        """获取阻塞性问题"""
        return self.db.fetchall(
            "SELECT * FROM review_issues WHERE review_id = ? AND blocking = 1 AND status = 'open'",
            (review_id,)
        )


class StoryCommitDAL:
    """Story Commit 数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建 Story Commit"""
        commit_id = generate_id()
        data['id'] = commit_id
        
        for field in ['facts_extracted', 'state_changes']:
            if field in data and isinstance(data[field], (dict, list)):
                data[field] = json.dumps(data[field])
        
        self.db.insert('story_commits', data)
        return commit_id
    
    def get(self, commit_id: str) -> Optional[Dict]:
        """获取 Story Commit"""
        row = self.db.get_by_id('story_commits', commit_id)
        if row:
            for field in ['facts_extracted', 'state_changes']:
                if row.get(field):
                    row[field] = json.loads(row[field])
        return row
    
    def get_by_chapter(self, chapter_id: str) -> Optional[Dict]:
        """获取章节的 Story Commit"""
        row = self.db.fetchone(
            "SELECT * FROM story_commits WHERE chapter_id = ? ORDER BY created_at DESC LIMIT 1",
            (chapter_id,)
        )
        if row:
            for field in ['facts_extracted', 'state_changes']:
                if row.get(field):
                    row[field] = json.loads(row[field])
        return row
    
    def update(self, commit_id: str, data: Dict) -> bool:
        """更新 Story Commit"""
        for field in ['facts_extracted', 'state_changes']:
            if field in data and isinstance(data[field], (dict, list)):
                data[field] = json.dumps(data[field])
        rows = self.db.update('story_commits', data, 'id = ?', (commit_id,))
        return rows > 0


class TimelineDAL:
    """时间线数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def create(self, data: Dict) -> str:
        """创建时间线事件"""
        event_id = generate_id()
        data['id'] = event_id
        if 'characters_involved' in data and isinstance(data['characters_involved'], list):
            data['characters_involved'] = json.dumps(data['characters_involved'])
        self.db.insert('timeline_events', data)
        return event_id
    
    def list_by_book(self, book_id: str, limit: int = 100) -> List[Dict]:
        """列出书籍的时间线事件"""
        rows = self.db.fetchall(
            "SELECT * FROM timeline_events WHERE book_id = ? ORDER BY event_time LIMIT ?",
            (book_id, limit)
        )
        for row in rows:
            if row.get('characters_involved'):
                row['characters_involved'] = json.loads(row['characters_involved'])
        return rows


class OperationLogDAL:
    """操作日志数据访问"""
    
    def __init__(self):
        self.db = get_db()
    
    def log(self, operation: str, entity_type: str = None, entity_id: str = None,
            details: Dict = None, duration_ms: int = None, token_count: int = None,
            model_used: str = None) -> str:
        """记录操作日志"""
        log_id = generate_id()
        self.db.insert('operation_logs', {
            'id': log_id,
            'operation': operation,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'details': json.dumps(details) if details else None,
            'duration_ms': duration_ms,
            'token_count': token_count,
            'model_used': model_used,
        })
        return log_id
    
    def list_recent(self, limit: int = 100) -> List[Dict]:
        """列出最近的操作日志"""
        rows = self.db.fetchall(
            "SELECT * FROM operation_logs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        )
        for row in rows:
            if row.get('details'):
                row['details'] = json.loads(row['details'])
        return rows
    
    def get_stats(self) -> Dict[str, Any]:
        """获取日志统计"""
        stats = self.db.fetchone("""
            SELECT 
                COUNT(*) as total,
                SUM(token_count) as total_tokens,
                SUM(duration_ms) as total_duration
            FROM operation_logs
        """)
        return stats or {}


# 全局 DAL 实例
_dal_instances = {}


def get_dal(name: str):
    """获取 DAL 实例"""
    if name not in _dal_instances:
        dal_classes = {
            'project': ProjectDAL,
            'book': BookDAL,
            'chapter': ChapterDAL,
            'character': CharacterDAL,
            'faction': FactionDAL,
            'location': LocationDAL,
            'foreshadow': ForeshadowDAL,
            'story_fact': StoryFactDAL,
            'review': ReviewDAL,
            'story_commit': StoryCommitDAL,
            'timeline': TimelineDAL,
            'operation_log': OperationLogDAL,
        }
        if name in dal_classes:
            _dal_instances[name] = dal_classes[name]()
    return _dal_instances.get(name)
