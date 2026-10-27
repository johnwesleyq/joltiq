import pytest

from database_guard import require_test_database_urls


def test_test_database_guard_accepts_matching_test_databases():
    require_test_database_urls(
        "postgresql+psycopg://joltiq_app:secret@postgres:5432/joltiq_test",
        "postgresql+psycopg://postgres:secret@postgres:5432/joltiq_test",
    )


@pytest.mark.parametrize("database", ["joltiq", "production", "customer_data"])
def test_test_database_guard_refuses_non_test_database(database):
    with pytest.raises(RuntimeError, match="refuses to rebuild"):
        require_test_database_urls(
            f"postgresql+psycopg://joltiq_app:secret@postgres:5432/{database}",
            f"postgresql+psycopg://postgres:secret@postgres:5432/{database}",
        )


def test_test_database_guard_refuses_different_app_and_migration_databases():
    with pytest.raises(RuntimeError, match="same database"):
        require_test_database_urls(
            "postgresql+psycopg://joltiq_app:secret@postgres:5432/joltiq_test",
            "postgresql+psycopg://postgres:secret@postgres:5432/other_test",
        )
