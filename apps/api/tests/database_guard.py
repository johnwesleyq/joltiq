from sqlalchemy.engine import make_url


def require_test_database_urls(app_url: str, migration_url: str) -> None:
    app = make_url(app_url)
    migration = make_url(migration_url)
    app_target = (app.host, app.port, app.database)
    migration_target = (migration.host, migration.port, migration.database)
    if app_target != migration_target:
        raise RuntimeError("The test app and migration URLs must target the same database")
    if not app.database or not app.database.endswith("_test"):
        raise RuntimeError(f"The test harness refuses to rebuild non-test database {app.database!r}")
