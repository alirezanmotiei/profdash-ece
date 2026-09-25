import sqlite3
import pytest
from profdash.dashboard.db import update_professor_contact, get_professor_by_id


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE professors (
            id TEXT PRIMARY KEY,
            professor TEXT NOT NULL,
            university TEXT NOT NULL,
            email TEXT,
            homepage TEXT,
            scholar TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO professors (id, professor, university, email, homepage, scholar)
        VALUES ('test-prof', 'George Whitesides', 'Harvard University', '', '', '')
        """
    )
    conn.commit()
    yield conn
    conn.close()


def test_update_professor_contact(memory_db):
    update_professor_contact(
        "test-prof",
        email="gwhitesides@gmwgroup.harvard.edu",
        homepage="https://gmwgroup.harvard.edu",
        scholar="https://scholar.google.com/citations?user=test",
        conn=memory_db,
    )
    prof = get_professor_by_id("test-prof", conn=memory_db)
    assert prof is not None
    assert prof["email"] == "gwhitesides@gmwgroup.harvard.edu"
    assert prof["homepage"] == "https://gmwgroup.harvard.edu"
    assert prof["scholar"] == "https://scholar.google.com/citations?user=test"
