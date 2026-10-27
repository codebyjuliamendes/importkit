from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from importkit import services
from importkit.config import Settings
from importkit.db import create_db_engine, create_session_factory
from importkit.models import Contact

CSV = "name,email\n" + "".join(f"Person {i},p{i}@example.com\n" for i in range(50))


def test_parallel_commits_of_same_batch_insert_once(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        record = services.create_preview(
            session, text=CSV, filename="a.csv", mapping=None, settings=settings
        ).record

    def commit() -> services.CommitOutcome:
        with factory() as session:
            return services.commit_import(session, record.id)

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(lambda _: commit(), range(6)))

    assert sum(1 for o in outcomes if not o.replayed) == 1
    assert {o.created for o in outcomes} == {50}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Contact)) == 50
    engine.dispose()


def test_overlapping_batches_never_duplicate_contacts(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    ids = []
    for _ in range(4):
        with factory() as session:
            ids.append(
                services.create_preview(
                    session, text=CSV, filename="b.csv", mapping=None, settings=settings
                ).record.id
            )

    def commit(import_id: int) -> int:
        with factory() as session:
            return services.commit_import(session, import_id).created

    with ThreadPoolExecutor(max_workers=4) as pool:
        created = list(pool.map(commit, ids))

    assert sum(created) == 50
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Contact)) == 50
    engine.dispose()


def test_bulk_insert_falls_back_to_row_by_row(make_settings: Callable[..., Settings]) -> None:
    """Simulate a race the pre-check cannot see: the bulk insert hits the unique index."""
    settings = make_settings()
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as session:
        first = services.create_preview(
            session,
            text="name,email\nA1,a@example.com\nB1,b@example.com",
            filename="1.csv",
            mapping=None,
            settings=settings,
        ).record
    with factory() as session:
        second = services.create_preview(
            session,
            text="name,email\nA2,a@example.com",
            filename="2.csv",
            mapping=None,
            settings=settings,
        ).record
    with factory() as session:
        services.commit_import(session, second.id)

    original = services.existing_emails
    services.existing_emails = lambda session, emails: set()  # type: ignore[assignment]
    try:
        with factory() as session:
            outcome = services.commit_import(session, first.id)
    finally:
        services.existing_emails = original
    assert outcome.created == 1
    with factory() as session:
        detail = services.get_import(session, first.id)
        assert [r.status for r in detail.rows] == ["invalid", "valid"]
        assert detail.rows[0].errors_json[0]["code"] == "email_conflict"
    engine.dispose()
