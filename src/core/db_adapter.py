"""
NovelForge 数据库适配器
将现有的 ProjectManager 与新数据库系统集成
"""

import json
import logging
from typing import Optional, Dict, Any, List

from .dal import (
    ProjectDAL, BookDAL, ChapterDAL, CharacterDAL,
    FactionDAL, LocationDAL, ForeshadowDAL, StoryFactDAL,
    ReviewDAL, StoryCommitDAL, TimelineDAL, OperationLogDAL
)
from .models import (
    StoryProject, Chapter, ChapterStatus, Character, Faction, Location,
    Foreshadowing, WorldSetting
)

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    数据库适配器
    将现有数据模型与数据库层集成
    """
    
    def __init__(self):
        self.project_dal = ProjectDAL()
        self.book_dal = BookDAL()
        self.chapter_dal = ChapterDAL()
        self.character_dal = CharacterDAL()
        self.faction_dal = FactionDAL()
        self.location_dal = LocationDAL()
        self.foreshadow_dal = ForeshadowDAL()
        self.story_fact_dal = StoryFactDAL()
        self.review_dal = ReviewDAL()
        self.story_commit_dal = StoryCommitDAL()
        self.timeline_dal = TimelineDAL()
        self.operation_log_dal = OperationLogDAL()
    
    # ========== 项目操作 ==========
    
    def create_project(self, name: str, genre: str = "", config: Any = None) -> StoryProject:
        """创建项目"""
        # 创建数据库记录
        project_id = self.project_dal.create({
            'name': name,
            'genre': genre,
            'target_chapters': config.get('project', 'target_chapters', default=100) if config else 100,
            'chapter_words_min': config.get('project', 'chapter_words_min', default=2000) if config else 2000,
            'chapter_words_max': config.get('project', 'chapter_words_max', default=4000) if config else 4000,
        })
        
        # 创建书籍记录
        self.book_dal.create({
            'project_id': project_id,
            'title': name,
            'genre': genre,
        })
        
        # 返回 StoryProject 对象
        project = StoryProject(
            id=project_id,
            name=name,
            genre=genre,
        )
        
        logger.info(f"项目创建成功: {project_id}")
        return project
    
    def load_project(self, project_id: str) -> Optional[StoryProject]:
        """加载项目"""
        project_row = self.project_dal.get(project_id)
        if project_row is None:
            return None
        
        # 获取书籍
        books = self.book_dal.get_by_project(project_id)
        book_id = books[0]['id'] if books else None
        
        # 获取角色
        characters = {}
        if book_id:
            for char in self.character_dal.list_by_book(book_id):
                characters[char['name']] = Character(
                    name=char['name'],
                    description=char.get('description', ''),
                    personality=char.get('personality', ''),
                    background=char.get('background', ''),
                )
        
        # 获取势力
        factions = {}
        if book_id:
            for faction in self.faction_dal.list_by_book(book_id):
                factions[faction['name']] = Faction(
                    name=faction['name'],
                    description=faction.get('description', ''),
                    goals=faction.get('goals', ''),
                )
        
        # 获取地点
        locations = {}
        if book_id:
            for loc in self.location_dal.list_by_book(book_id):
                locations[loc['name']] = Location(
                    name=loc['name'],
                    description=loc.get('description', ''),
                    significance=loc.get('significance', ''),
                )
        
        # 获取伏笔
        foreshadowing = {}
        if book_id:
            for fs in self.foreshadow_dal.list_by_book(book_id):
                foreshadowing[fs['id']] = Foreshadowing(
                    id=fs['id'],
                    description=fs.get('description', ''),
                    status=fs.get('status', 'open'),
                    planted_chapter=fs.get('created_chapter') or 0,
                )
        
        # 获取章节
        chapters = {}
        if book_id:
            for ch in self.chapter_dal.list_by_book(book_id):
                chapter = Chapter(
                    number=ch['number'],
                    title=ch.get('title', ''),
                    content=ch.get('content', ''),
                    word_count=ch.get('word_count', 0),
                    status=ChapterStatus(ch.get('status', 'draft')),
                    summary=ch.get('summary', ''),
                )
                chapters[ch['number']] = chapter
        
        # 构建 StoryProject
        project = StoryProject(
            id=project_id,
            name=project_row['name'],
            genre=project_row.get('genre', ''),
            characters=characters,
            factions=factions,
            locations=locations,
            foreshadowing=foreshadowing,
            chapters=chapters,
        )
        
        # 设置世界设定
        if project_row.get('world_setting'):
            try:
                world_data = json.loads(project_row['world_setting'])
                project.world = WorldSetting(**world_data)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        
        return project
    
    def save_project(self, project: StoryProject):
        """保存项目"""
        # 更新项目
        world_data = None
        if project.world:
            world_data = project.world.__dict__
        
        self.project_dal.update(project.id, {
            'name': project.name,
            'genre': project.genre,
            'world_setting': json.dumps(world_data) if world_data else None,
            'writing_style': project.writing_style,
            'author_intent': project.author_intent,
        })
        
        # 获取或创建书籍
        books = self.book_dal.get_by_project(project.id)
        if books:
            book_id = books[0]['id']
            self.book_dal.update(book_id, {
                'title': project.name,
                'genre': project.genre,
            })
        else:
            book_id = self.book_dal.create({
                'project_id': project.id,
                'title': project.name,
                'genre': project.genre,
            })
        
        # 保存角色
        for name, char in project.characters.items():
            existing = self.character_dal.list_by_book(book_id)
            char_row = next((c for c in existing if c['name'] == name), None)
            
            if char_row:
                self.character_dal.update(char_row['id'], {
                    'description': char.description,
                    'personality': char.personality,
                    'background': char.background,
                    'goals': char.goals,
                    'flaws': char.flaws,
                })
            else:
                self.character_dal.create({
                    'book_id': book_id,
                    'name': name,
                    'description': char.description,
                    'personality': char.personality,
                    'background': char.background,
                    'goals': char.goals,
                    'flaws': char.flaws,
                })
        
        # 保存势力
        for name, faction in project.factions.items():
            existing = self.faction_dal.list_by_book(book_id)
            faction_row = next((f for f in existing if f['name'] == name), None)
            
            if faction_row:
                self.faction_dal.update(faction_row['id'], {
                    'description': faction.description,
                    'goals': faction.goals,
                })
            else:
                self.faction_dal.create({
                    'book_id': book_id,
                    'name': name,
                    'description': faction.description,
                    'goals': faction.goals,
                })
        
        # 保存地点
        for name, loc in project.locations.items():
            existing = self.location_dal.list_by_book(book_id)
            loc_row = next((location for location in existing if location['name'] == name), None)
            
            if loc_row:
                self.location_dal.update(loc_row['id'], {
                    'description': loc.description,
                    'significance': loc.significance,
                })
            else:
                self.location_dal.create({
                    'book_id': book_id,
                    'name': name,
                    'description': loc.description,
                    'significance': loc.significance,
                })
        
        # 保存伏笔
        for title, fs in project.foreshadowing.items():
            existing = self.foreshadow_dal.list_by_book(book_id)
            fs_row = next((f for f in existing if f['title'] == title), None)
            
            if fs_row:
                self.foreshadow_dal.update(fs_row['id'], {
                    'description': fs.description,
                    'status': fs.status,
                })
            else:
                self.foreshadow_dal.create({
                    'book_id': book_id,
                    'created_chapter': fs.created_chapter,
                    'title': title,
                    'description': fs.description,
                    'status': fs.status,
                })
        
        logger.info(f"项目保存成功: {project.id}")
    
    def delete_project(self, project_id: str):
        """删除项目"""
        self.project_dal.delete(project_id)
        logger.info(f"项目删除成功: {project_id}")
    
    def list_projects(self) -> List[Dict]:
        """列出所有项目"""
        projects = self.project_dal.list_all()
        result = []
        
        for p in projects:
            # 获取书籍统计
            books = self.book_dal.get_by_project(p['id'])
            total_chapters = 0
            total_words = 0
            
            if books:
                book_id = books[0]['id']
                chapters = self.chapter_dal.list_by_book(book_id)
                total_chapters = len(chapters)
                total_words = sum(ch.get('word_count', 0) for ch in chapters)
            
            result.append({
                'id': p['id'],
                'name': p['name'],
                'genre': p.get('genre', ''),
                'chapters': total_chapters,
                'total_words': total_words,
                'created_at': p.get('created_at', ''),
                'updated_at': p.get('updated_at', ''),
            })
        
        return result
    
    # ========== 章节操作 ==========
    
    def save_chapter_content(self, project_id: str, chapter_number: int, content: str):
        """保存章节内容"""
        # 获取书籍
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return
        
        book_id = books[0]['id']
        
        # 获取或创建章节
        chapter = self.chapter_dal.get_by_number(book_id, chapter_number)
        
        if chapter:
            # 保存版本
            if chapter.get('content') and chapter['content'] != content:
                self.chapter_dal.save_version(
                    chapter['id'],
                    chapter['content'],
                    chapter.get('word_count', 0),
                    '自动保存'
                )
            
            # 更新章节
            self.chapter_dal.update(chapter['id'], {
                'content': content,
                'word_count': len(content),
            })
        else:
            # 创建章节
            self.chapter_dal.create({
                'book_id': book_id,
                'number': chapter_number,
                'content': content,
                'word_count': len(content),
                'status': 'draft',
            })
        
        # 更新书籍统计
        self.book_dal.update_stats(book_id)
    
    def load_chapter_content(self, project_id: str, chapter_number: int) -> str:
        """加载章节内容"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return ""
        
        book_id = books[0]['id']
        chapter = self.chapter_dal.get_by_number(book_id, chapter_number)
        
        return chapter.get('content', '') if chapter else ""
    
    def save_review(self, project_id: str, review_data: Dict):
        """保存审查结果"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return
        
        book_id = books[0]['id']
        chapter_number = review_data.get('chapter_number', 0)
        chapter = self.chapter_dal.get_by_number(book_id, chapter_number)
        
        if not chapter:
            return
        
        # 创建审查记录
        review_id = self.review_dal.create({
            'chapter_id': chapter['id'],
            'overall_score': review_data.get('overall_score', 0),
            'passed': review_data.get('passed', False),
            'verdict': review_data.get('verdict', ''),
        })
        
        # 保存维度
        for dim in review_data.get('dimensions', []):
            self.review_dal.add_dimension(
                review_id,
                dim.get('name', ''),
                dim.get('score', 0),
                dim.get('weight', 0)
            )
        
        # 保存问题
        for issue in review_data.get('issues', []):
            self.review_dal.add_issue(review_id, {
                'dimension': issue.get('dimension', ''),
                'severity': issue.get('severity', 'medium'),
                'blocking': issue.get('blocking', False),
                'description': issue.get('description', ''),
                'suggestion': issue.get('suggestion', ''),
            })
    
    def save_joint_review(self, project_id: str, review_data: Dict):
        """保存联合审查结果"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return
        
        book_id = books[0]['id']
        
        # 获取章节范围内的第一个章节
        chapter_range = review_data.get('chapter_range', '')
        if '-' in chapter_range:
            start = int(chapter_range.split('-')[0])
            chapter = self.chapter_dal.get_by_number(book_id, start)
            if chapter:
                self.review_dal.create({
                    'chapter_id': chapter['id'],
                    'review_type': 'joint',
                    'overall_score': review_data.get('overall_score', 0),
                    'passed': review_data.get('passed', False),
                    'verdict': review_data.get('verdict', ''),
                })
    
    # ========== 记忆操作 ==========
    
    def store_chapter_summary(self, project_id: str, chapter_number: int, 
                              summary: str, key_events: Optional[List[str]] = None,
                              characters: Optional[List[str]] = None,
                              locations: Optional[List[str]] = None):
        """存储章节摘要"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return
        
        book_id = books[0]['id']
        chapter = self.chapter_dal.get_by_number(book_id, chapter_number)
        
        if chapter:
            self.chapter_dal.update(chapter['id'], {
                'summary': summary,
                'key_events': json.dumps(key_events or []),
                'characters_appeared': json.dumps(characters or []),
                'locations_used': json.dumps(locations or []),
            })
    
    def store_story_fact(self, project_id: str, chapter_number: int, 
                        fact_type: str, content: str, entities: Optional[List[str]] = None):
        """存储故事事实"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return
        
        book_id = books[0]['id']
        chapter = self.chapter_dal.get_by_number(book_id, chapter_number)
        
        if chapter:
            self.story_fact_dal.create({
                'book_id': book_id,
                'chapter_id': chapter['id'],
                'fact_type': fact_type,
                'content': content,
                'entities': entities,
            })
    
    def store_timeline_event(self, project_id: str, event_time: str, 
                            event_type: str, description: str,
                            chapter_number: Optional[int] = None):
        """存储时间线事件"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return
        
        book_id = books[0]['id']
        chapter_id = None
        
        if chapter_number:
            chapter = self.chapter_dal.get_by_number(book_id, chapter_number)
            if chapter:
                chapter_id = chapter['id']
        
        self.timeline_dal.create({
            'book_id': book_id,
            'chapter_id': chapter_id,
            'event_time': event_time,
            'event_type': event_type,
            'description': description,
        })
    
    # ========== 查询操作 ==========
    
    def get_recent_summaries(self, project_id: str, count: int = 3) -> List[Dict]:
        """获取最近章节摘要"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return []
        
        book_id = books[0]['id']
        chapters = self.chapter_dal.list_by_book(book_id)
        
        # 按章节号排序，取最近的
        chapters.sort(key=lambda x: x['number'], reverse=True)
        recent = chapters[:count]
        
        return [{
            'number': ch['number'],
            'title': ch.get('title', ''),
            'summary': ch.get('summary', ''),
        } for ch in recent]
    
    def get_all_summaries(self, project_id: str) -> List[Dict]:
        """获取所有章节摘要"""
        return self.get_recent_summaries(project_id, count=1000)
    
    def search_facts(self, project_id: str, query: str) -> List[Dict]:
        """搜索故事事实"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return []
        
        book_id = books[0]['id']
        return self.story_fact_dal.search(book_id, query)
    
    def get_timeline(self, project_id: str) -> List[Dict]:
        """获取时间线"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return []
        
        book_id = books[0]['id']
        return self.timeline_dal.list_by_book(book_id)
    
    def get_open_foreshadowing(self, project_id: str) -> List[Dict]:
        """获取未解决的伏笔"""
        books = self.book_dal.get_by_project(project_id)
        if not books:
            return []
        
        book_id = books[0]['id']
        return self.foreshadow_dal.get_open(book_id)
    
    # ========== 任务日志 ==========
    
    def log_operation(self, operation: str, entity_type: Optional[str] = None,
                     entity_id: Optional[str] = None, details: Optional[Dict] = None,
                     duration_ms: Optional[int] = None, token_count: Optional[int] = None,
                     model_used: Optional[str] = None):
        """记录操作日志"""
        self.operation_log_dal.log(
            operation=operation,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            duration_ms=duration_ms,
            token_count=token_count,
            model_used=model_used,
        )


# 全局适配器实例
_adapter: Optional[DatabaseAdapter] = None


def get_adapter() -> DatabaseAdapter:
    """获取全局数据库适配器"""
    global _adapter
    if _adapter is None:
        _adapter = DatabaseAdapter()
    return _adapter
