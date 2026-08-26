"""
NovelForge 核心模块测试
"""

import pytest
from pathlib import Path

# 设置测试数据库路径
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.database import Database
from src.core.dal import (
    ProjectDAL, BookDAL, ChapterDAL, CharacterDAL,
    ForeshadowDAL, StoryFactDAL, ReviewDAL, StoryCommitDAL
)


@pytest.fixture
def db(tmp_path):
    """创建临时数据库"""
    db_path = str(tmp_path / "test.db")
    # 初始化全局数据库实例
    from src.core import database
    database._db_instance = Database(db_path)
    return database._db_instance


@pytest.fixture
def project_dal(db):
    """创建项目 DAL"""
    return ProjectDAL()


@pytest.fixture
def book_dal(db):
    """创建书籍 DAL"""
    return BookDAL()


@pytest.fixture
def chapter_dal(db):
    """创建章节 DAL"""
    return ChapterDAL()


@pytest.fixture
def character_dal(db):
    """创建角色 DAL"""
    return CharacterDAL()


# ========== 数据库基础测试 ==========

class TestDatabase:
    """数据库基础测试"""
    
    def test_init(self, db):
        """测试数据库初始化"""
        assert db.db_path.exists()
        assert db.get_version() == 1
    
    def test_table_exists(self, db):
        """测试表是否存在"""
        assert db.table_exists('projects')
        assert db.table_exists('books')
        assert db.table_exists('chapters')
        assert db.table_exists('characters')
        assert db.table_exists('tasks')
        assert db.table_exists('story_facts')
        assert db.table_exists('reviews')
    
    def test_insert_and_get(self, db):
        """测试插入和查询"""
        data = {
            'id': 'test-001',
            'name': 'Test Project',
            'genre': 'fantasy'
        }
        db.insert('projects', data)
        
        result = db.get_by_id('projects', 'test-001')
        assert result is not None
        assert result['name'] == 'Test Project'
    
    def test_update(self, db):
        """测试更新"""
        db.insert('projects', {'id': 'test-002', 'name': 'Old Name'})
        
        rows = db.update('projects', {'name': 'New Name'}, 'id = ?', ('test-002',))
        assert rows == 1
        
        result = db.get_by_id('projects', 'test-002')
        assert result['name'] == 'New Name'
    
    def test_delete(self, db):
        """测试删除"""
        db.insert('projects', {'id': 'test-003', 'name': 'To Delete'})
        
        rows = db.delete('projects', 'id = ?', ('test-003',))
        assert rows == 1
        
        result = db.get_by_id('projects', 'test-003')
        assert result is None
    
    def test_count(self, db):
        """测试计数"""
        db.insert('projects', {'id': 'p1', 'name': 'P1'})
        db.insert('projects', {'id': 'p2', 'name': 'P2'})
        
        count = db.count('projects')
        assert count == 2
    
    def test_list_all(self, db):
        """测试列表"""
        db.insert('projects', {'id': 'p1', 'name': 'P1'})
        db.insert('projects', {'id': 'p2', 'name': 'P2'})
        
        results = db.list_all('projects')
        assert len(results) == 2


# ========== 项目 DAL 测试 ==========

class TestProjectDAL:
    """项目 DAL 测试"""
    
    def test_create(self, project_dal):
        """测试创建项目"""
        project_id = project_dal.create({
            'name': 'My Novel',
            'genre': 'fantasy',
            'target_chapters': 100
        })
        
        assert project_id is not None
        
        project = project_dal.get(project_id)
        assert project is not None
        assert project['name'] == 'My Novel'
    
    def test_update(self, project_dal):
        """测试更新项目"""
        project_id = project_dal.create({'name': 'Old Name'})
        
        project_dal.update(project_id, {'name': 'New Name'})
        
        project = project_dal.get(project_id)
        assert project['name'] == 'New Name'
    
    def test_delete(self, project_dal):
        """测试删除项目"""
        project_id = project_dal.create({'name': 'To Delete'})
        
        project_dal.delete(project_id)
        
        project = project_dal.get(project_id)
        assert project is None
    
    def test_list_all(self, project_dal):
        """测试列出项目"""
        project_dal.create({'name': 'P1'})
        project_dal.create({'name': 'P2'})
        
        projects = project_dal.list_all()
        assert len(projects) == 2


# ========== 书籍 DAL 测试 ==========

class TestBookDAL:
    """书籍 DAL 测试"""
    
    def test_create_and_get(self, book_dal, project_dal):
        """测试创建和获取书籍"""
        project_id = project_dal.create({'name': 'Test Project'})
        
        book_id = book_dal.create({
            'project_id': project_id,
            'title': 'My Book',
            'genre': 'fantasy'
        })
        
        book = book_dal.get(book_id)
        assert book is not None
        assert book['title'] == 'My Book'
    
    def test_get_by_project(self, book_dal, project_dal):
        """测试获取项目的书籍"""
        project_id = project_dal.create({'name': 'Test Project'})
        
        book_dal.create({'project_id': project_id, 'title': 'Book 1'})
        book_dal.create({'project_id': project_id, 'title': 'Book 2'})
        
        books = book_dal.get_by_project(project_id)
        assert len(books) == 2


# ========== 章节 DAL 测试 ==========

class TestChapterDAL:
    """章节 DAL 测试"""
    
    def test_create_and_get(self, chapter_dal, book_dal, project_dal):
        """测试创建和获取章节"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        chapter_id = chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'title': 'Chapter 1',
            'content': 'Once upon a time...',
            'word_count': 20
        })
        
        chapter = chapter_dal.get(chapter_id)
        assert chapter is not None
        assert chapter['title'] == 'Chapter 1'
        assert chapter['word_count'] == 20
    
    def test_get_by_number(self, chapter_dal, book_dal, project_dal):
        """测试根据编号获取章节"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'title': 'Chapter 1',
            'content': 'Content 1'
        })
        
        chapter = chapter_dal.get_by_number(book_id, 1)
        assert chapter is not None
        assert chapter['title'] == 'Chapter 1'
    
    def test_save_version(self, chapter_dal, book_dal, project_dal):
        """测试保存章节版本"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        chapter_id = chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'content': 'Version 1',
            'word_count': 10
        })
        
        # 保存版本
        version_id = chapter_dal.save_version(chapter_id, 'Version 2', 15, 'Updated')
        assert version_id is not None
        
        versions = chapter_dal.get_versions(chapter_id)
        assert len(versions) == 1
        assert versions[0]['content'] == 'Version 2'


# ========== 角色 DAL 测试 ==========

class TestCharacterDAL:
    """角色 DAL 测试"""
    
    def test_create_and_get(self, character_dal, book_dal, project_dal):
        """测试创建和获取角色"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        char_id = character_dal.create({
            'book_id': book_id,
            'name': 'Hero',
            'description': 'The main character',
            'personality': 'Brave and kind'
        })
        
        char = character_dal.get(char_id)
        assert char is not None
        assert char['name'] == 'Hero'
    
    def test_list_by_book(self, character_dal, book_dal, project_dal):
        """测试列出书籍的角色"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        character_dal.create({'book_id': book_id, 'name': 'Hero'})
        character_dal.create({'book_id': book_id, 'name': 'Villain'})
        
        chars = character_dal.list_by_book(book_id)
        assert len(chars) == 2




# ========== 伏笔 DAL 测试 ==========

class TestForeshadowDAL:
    """伏笔 DAL 测试"""
    
    def test_create_and_get(self, db, book_dal, project_dal):
        """测试创建和获取伏笔"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        dal = ForeshadowDAL()
        fs_id = dal.create({
            'book_id': book_id,
            'created_chapter': 1,
            'title': 'Mysterious Symbol',
            'description': 'A strange symbol appears on the wall',
            'status': 'open'
        })
        
        fs = dal.get(fs_id)
        assert fs is not None
        assert fs['title'] == 'Mysterious Symbol'
    
    def test_resolve(self, db, book_dal, project_dal):
        """测试解决伏笔"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        
        dal = ForeshadowDAL()
        fs_id = dal.create({
            'book_id': book_id,
            'created_chapter': 1,
            'title': 'Mystery',
            'status': 'open'
        })
        
        dal.resolve(fs_id, 10)
        
        fs = dal.get(fs_id)
        assert fs is not None
        assert fs['status'] == 'resolved'
        assert fs['resolved_chapter'] == 10


# ========== Story Fact DAL 测试 ==========

class TestStoryFactDAL:
    """Story Fact DAL 测试"""
    
    def test_create_and_list(self, db, book_dal, project_dal, chapter_dal):
        """测试创建和列出 Story Facts"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        chapter_id = chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'content': 'Test'
        })
        
        dal = StoryFactDAL()
        fact_id = dal.create({
            'book_id': book_id,
            'chapter_id': chapter_id,
            'fact_type': 'character_action',
            'content': 'Hero defeated the dragon',
            'entities': ['Hero', 'Dragon']
        })
        
        facts = dal.list_by_book(book_id)
        assert len(facts) == 1
        assert facts[0]['content'] == 'Hero defeated the dragon'
        assert facts[0]['source'] == 'legacy_dal'
        assert facts[0]['verification_status'] == 'unverified'
        assert facts[0]['commit_id'] is None

        commit_dal = StoryCommitDAL()
        commit_id = commit_dal.create({
            'chapter_id': chapter_id,
            'status': 'accepted',
            'facts_extracted': [],
            'state_changes': {},
        })
        commit = db.get_by_id('story_commits', commit_id)
        assert commit is not None
        assert commit['status'] == 'pending'
        with pytest.raises(ValueError, match='StoryRepository'):
            commit_dal.update(commit_id, {'status': 'accepted'})


# ========== Review DAL 测试 ==========

class TestReviewDAL:
    """Review DAL 测试"""
    
    def test_create_and_get(self, db, book_dal, project_dal, chapter_dal):
        """测试创建和获取审查"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        chapter_id = chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'content': 'Test'
        })
        
        dal = ReviewDAL()
        review_id = dal.create({
            'chapter_id': chapter_id,
            'overall_score': 95.5,
            'passed': True,
            'verdict': 'Good chapter'
        })
        
        review = dal.get(review_id)
        assert review is not None
        assert review['overall_score'] == 95.5
        assert review['passed'] == 1  # SQLite stores boolean as integer
    
    def test_add_dimension(self, db, book_dal, project_dal, chapter_dal):
        """测试添加审查维度"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        chapter_id = chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'content': 'Test'
        })
        
        dal = ReviewDAL()
        review_id = dal.create({
            'chapter_id': chapter_id,
            'overall_score': 90
        })
        
        dal.add_dimension(review_id, 'plot_coherence', 92, 0.15)
        dal.add_dimension(review_id, 'character_consistency', 88, 0.15)
        
        dimensions = dal.get_dimensions(review_id)
        assert len(dimensions) == 2
    
    def test_add_issue(self, db, book_dal, project_dal, chapter_dal):
        """测试添加审查问题"""
        project_id = project_dal.create({'name': 'Test'})
        book_id = book_dal.create({'project_id': project_id, 'title': 'Book'})
        chapter_id = chapter_dal.create({
            'book_id': book_id,
            'number': 1,
            'content': 'Test'
        })
        
        dal = ReviewDAL()
        review_id = dal.create({
            'chapter_id': chapter_id,
            'overall_score': 85
        })
        
        issue_id = dal.add_issue(review_id, {
            'dimension': 'plot_coherence',
            'severity': 'high',
            'blocking': True,
            'description': 'Plot hole detected',
            'suggestion': 'Add explanation'
        })
        
        issues = dal.get_issues(review_id)
        assert len(issues) == 1
        assert issues[0]['blocking'] == 1
        
        blocking = dal.get_blocking_issues(review_id)
        assert len(blocking) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
