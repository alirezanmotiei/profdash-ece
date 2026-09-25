import pytest
from profdash.profile import Profile, Identity, Venues
from profdash.workers.tasks import compose_email, select_relevant_papers, BAD_TITLE_PREFIXES


@pytest.fixture
def sample_profile():
    return Profile(
        identity=Identity(
            name="Alex Morgan",
            email="alex.morgan@example.edu",
            bio="BSc student in Electrical Engineering with research focus on bioelectronics and instrumentation.",
            interests_short="bioelectronics and analog circuits",
            signature="Alex Morgan\nDepartment of Electrical Engineering\nalex.morgan@example.edu",
        ),
        venues=Venues(
            preset="ee-bio",
            strong=["nature", "science", "ieee tbme", "lab on a chip"],
            moderate=["biosensors and bioelectronics", "advanced healthcare materials"],
        ),
    )


def test_compose_email(sample_profile):
    prof = {
        "id": "test-prof",
        "professor": "George M. Whitesides",
        "university": "Harvard University",
        "research_bucket": "biomed",
    }
    recs = [
        {
            "title": "Engineered affinity proteins enable sensitive label-free electrochemical detection",
            "venue": "Biosensors and Bioelectronics X",
            "year": 2026,
        },
        {
            "title": "Point-of-need diagnostics in a post-Covid world",
            "venue": "Lab on a Chip",
            "year": 2025,
        },
    ]
    subject, body = compose_email(prof, recs, sample_profile)

    assert "Harvard University" in subject
    assert "Alex Morgan" in subject
    assert "Dear Professor Whitesides," in body
    assert "bioelectronics and instrumentation" in body
    assert "Biosensors and Bioelectronics X, 2026" in body
    assert "Lab on a Chip, 2025" in body
    assert "Sincerely,\nAlex Morgan\nDepartment of Electrical Engineering" in body


def test_select_relevant_papers_filtering(sample_profile):
    papers = [
        {
            "title": "Author response for Point-of-need diagnostics",
            "venue": "Lab on a Chip",
            "year": 2025,
            "type": "article",
        },
        {
            "title": "Engineered affinity proteins",
            "venue": "Biosensors and Bioelectronics",
            "year": 2025,
            "type": "article",
        },
        {
            "title": "Old Paper From Past",
            "venue": "Lab on a Chip",
            "year": 2010,
            "type": "article",
        },
    ]
    selected = select_relevant_papers(papers, sample_profile, limit=4)
    # 2010 paper should be excluded by cutoff
    assert len(selected) <= 2
    titles = [s["title"] for s in selected]
    assert "Engineered affinity proteins" in titles
    assert "Old Paper From Past" not in titles
