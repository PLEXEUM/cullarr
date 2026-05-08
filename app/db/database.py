import sqlite3
import os
import asyncio
from functools import partial
from pathlib import Path

DB_PATH = Path("/app/config") / "cullarr.db"

def get_connection():
    """Get a database connection with WAL mode enabled."""
    os.makedirs(DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)  # ← ADD timeout parameter
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    # Radarr configuration
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS radarr_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            url TEXT,
            api_key TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Plex configuration
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plex_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            url TEXT,
            api_key TEXT,
            collection_key TEXT,
            enabled BOOLEAN DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Scoring weights (6 factors, sum to 100%)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scoring_weights (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            age_weight INTEGER DEFAULT 25,
            size_weight INTEGER DEFAULT 25,
            rating_weight INTEGER DEFAULT 15,
            quality_weight INTEGER DEFAULT 15,
            monitored_weight INTEGER DEFAULT 10,
            watched_weight INTEGER DEFAULT 10,
            age_max_days INTEGER DEFAULT 365,
            size_max_gb INTEGER DEFAULT 100,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Settings (schedules, rules, etc.)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled BOOLEAN DEFAULT 0,
            score_cron TEXT DEFAULT '0 3 * * 0',
            cull_cron TEXT DEFAULT '0 2 * * *',
            max_queued INTEGER DEFAULT 20,
            deletions_per_day INTEGER DEFAULT 0,
            delete_after_days INTEGER DEFAULT 7,
            protection_days INTEGER DEFAULT 30,
            collection_grouping BOOLEAN DEFAULT 0,
            min_score_threshold INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Scheduled deletions queue
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_deletions (
            id INTEGER PRIMARY KEY,
            movie_id INTEGER UNIQUE,
            movie_title TEXT,
            movie_year INTEGER,
            tmdb_id INTEGER,
            tmdb_rating REAL,
            size_gb REAL,
            quality TEXT,
            monitored BOOLEAN,
            score REAL,
            score_factors TEXT,
            scheduled_date DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'scheduled',
            collection_name TEXT
        )
    """)

    # Deletion history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS deletion_history (
            id INTEGER PRIMARY KEY,
            movie_id INTEGER,
            movie_title TEXT,
            movie_year INTEGER,
            size_gb REAL,
            score REAL,
            status TEXT,
            error_message TEXT,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            age_days INTEGER,
            tmdb_rating REAL,
            quality TEXT
        )
    """)

    # Run locks for preventing concurrent runs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            is_running BOOLEAN DEFAULT 0,
            run_type TEXT,
            started_at DATETIME,
            updated_at DATETIME
        )
    """)

    # Scored movies cache — populated by score runs, read by dashboard
    # Avoids live Radarr/Plex API calls on every dashboard page load
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scored_movies_cache (
            id INTEGER PRIMARY KEY,
            movie_id INTEGER UNIQUE,
            movie_title TEXT,
            movie_year INTEGER,
            tmdb_id INTEGER,
            tmdb_rating REAL,
            size_gb REAL,
            age_days INTEGER,
            quality TEXT,
            monitored BOOLEAN,
            normalized_score REAL,
            raw_score REAL,
            factors TEXT,
            plex_play_count INTEGER,
            collection_name TEXT,
            collection_id INTEGER,
            is_collection BOOLEAN DEFAULT 0,
            scheduled_for_deletion BOOLEAN DEFAULT 0,
            scheduled_date DATETIME,
            cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            manual_for_deletion BOOLEAN DEFAULT 0
        )
    """)

    # Add indexes after table creation
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_scored_movies_cache_manual ON scored_movies_cache(manual_for_deletion)")

    # Insert default records if not exists
    cursor.execute("INSERT OR IGNORE INTO radarr_config (id) VALUES (1)")
    cursor.execute("INSERT OR IGNORE INTO plex_config (id) VALUES (1)")
    cursor.execute("INSERT OR IGNORE INTO scoring_weights (id) VALUES (1)")
    cursor.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    cursor.execute("INSERT OR IGNORE INTO run_state (id, is_running) VALUES (1, 0)")

    conn.commit()
    conn.close()
    print("Database initialized successfully.")


def migrate_db():
    """
    Apply schema migrations for existing installs.
    Safe to run on every startup — all migrations are idempotent.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Migration: add scored_movies_cache if upgrading from a version without it
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scored_movies_cache (
            id INTEGER PRIMARY KEY,
            movie_id INTEGER UNIQUE,
            movie_title TEXT,
            movie_year INTEGER,
            tmdb_id INTEGER,
            tmdb_rating REAL,
            size_gb REAL,
            age_days INTEGER,
            quality TEXT,
            monitored BOOLEAN,
            normalized_score REAL,
            raw_score REAL,
            factors TEXT,
            plex_play_count INTEGER,
            collection_name TEXT,
            collection_id INTEGER,
            is_collection BOOLEAN DEFAULT 0,
            cached_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: add collection_name to scheduled_deletions if upgrading
    try:
        cursor.execute(
            "ALTER TABLE scheduled_deletions ADD COLUMN collection_name TEXT"
        )
        print("Migration applied: added collection_name to scheduled_deletions")
    except Exception:
        pass  # Column already exists, safe to ignore

    # Migration: add collection columns to scored_movies_cache if upgrading
    for column, definition in [
        ("collection_name", "TEXT"),
        ("collection_id", "INTEGER"),
        ("is_collection", "BOOLEAN DEFAULT 0"),
    ]:
        try:
            cursor.execute(
                f"ALTER TABLE scored_movies_cache ADD COLUMN {column} {definition}"
            )
            print(f"Migration applied: added {column} to scored_movies_cache")
        except Exception:
            pass  # Column already exists, safe to ignore

    # Migration: add min_score_threshold to settings table
    try:
        cursor.execute(
            "ALTER TABLE settings ADD COLUMN min_score_threshold INTEGER DEFAULT 0"
        )
        print("Migration applied: added min_score_threshold to settings")
    except Exception:
        pass  # Column already exists, safe to ignore

    # Migration: Add deletions_per_day to settings table
    try:
        cursor.execute(
            "ALTER TABLE settings ADD COLUMN deletions_per_day INTEGER DEFAULT 0"
        )
        print("Migration applied: added deletions_per_day to settings")
    except Exception:
        pass  # Column already exists, safe to ignore

    # Migration: add raw weight columns for 1-10 scale storage
    for column, default in [
        ("age_raw", "5"),
        ("size_raw", "5"),
        ("rating_raw", "5"),
        ("quality_raw", "5"),
        ("watched_raw", "5"),
    ]:
        try:
            cursor.execute(
                f"ALTER TABLE scoring_weights ADD COLUMN {column} INTEGER DEFAULT {default}"
            )
            print(f"Migration applied: added {column} to scoring_weights")
        except Exception:
            pass  # Column already exists, safe to ignore
    
    # Migration: Set monitored_weight = 0 (Issue #2)
    try:
        cursor.execute("UPDATE scoring_weights SET monitored_weight = 0 WHERE id = 1")
        print("Migration applied: set monitored_weight = 0")
    except Exception:
        pass  # Table may not exist yet

    # Migration: Backfill any NULL raw values from existing percentages (Issue #1)
    try:
        # Check if raw columns exist and have NULL values
        cursor.execute("SELECT age_raw, size_raw, rating_raw, quality_raw, watched_raw FROM scoring_weights WHERE id = 1")
        row = cursor.fetchone()
        if row:
            updates = []
            if row[0] is None:
                # Calculate from percentage weight: raw = (percentage / total_percent) * 10
                # Use default 5 if cannot calculate
                updates.append("age_raw = 5")
            if row[1] is None:
                updates.append("size_raw = 5")
            if row[2] is None:
                updates.append("rating_raw = 5")
            if row[3] is None:
                updates.append("quality_raw = 5")
            if row[4] is None:
                updates.append("watched_raw = 5")
            
            if updates:
                cursor.execute(f"UPDATE scoring_weights SET {', '.join(updates)} WHERE id = 1")
                print(f"Migration applied: backfilled raw values - {', '.join(updates)}")
    except Exception:
        pass  # Columns may not exist yet

    # Migration: Add index on scored_movies_cache.movie_id for faster cleanup (Issue #4)
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scored_movies_cache_movie_id ON scored_movies_cache(movie_id)")
        print("Migration applied: added index on scored_movies_cache.movie_id")
    except Exception:
        pass  # Table may not exist yet

    # Migration: Add collection_key to plex_config for direct key reference
    try:
        cursor.execute("ALTER TABLE plex_config ADD COLUMN collection_key TEXT")
        print("Migration applied: added collection_key to plex_config")
    except Exception:
        pass  # Column already exists, safe to ignore

    # Migration: Add plex_rating_key to scheduled_deletions table
    try:
        cursor.execute("ALTER TABLE scheduled_deletions ADD COLUMN plex_rating_key TEXT")
        print("Migration applied: added plex_rating_key to scheduled_deletions")
    except Exception:
        pass  # Column already exists, safe to ignore

    # Add indexes for better query performance (safe, no functional change)
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_deletions_status_date ON scheduled_deletions(status, scheduled_date)")
        print("Migration applied: added index on scheduled_deletions(status, scheduled_date)")
    except Exception:
        pass  # Index may already exist
    
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_deletions_movie_id ON scheduled_deletions(movie_id)")
        print("Migration applied: added index on scheduled_deletions(movie_id)")
    except Exception:
        pass  # Index may already exist
    
    # Migration: Add scheduled_for_deletion and scheduled_date to scored_movies_cache
    try:
        cursor.execute("ALTER TABLE scored_movies_cache ADD COLUMN scheduled_for_deletion BOOLEAN DEFAULT 0")
        print("Migration applied: added scheduled_for_deletion to scored_movies_cache")
    except Exception:
        pass  # Column already exists
    
    try:
        cursor.execute("ALTER TABLE scored_movies_cache ADD COLUMN scheduled_date DATETIME")
        print("Migration applied: added scheduled_date to scored_movies_cache")
    except Exception:
        pass  # Column already exists
    
    # Migration: Copy existing scheduled_deletions data to scored_movies_cache
    try:
        # Check if scheduled_deletions table has data and if we haven't already migrated
        count = cursor.execute("SELECT COUNT(*) as count FROM scheduled_deletions WHERE status = 'scheduled'").fetchone()
        if count and count["count"] > 0:
            # Update scored_movies_cache with scheduled info from scheduled_deletions
            cursor.execute("""
                UPDATE scored_movies_cache 
                SET scheduled_for_deletion = 1, 
                    scheduled_date = (
                        SELECT scheduled_date 
                        FROM scheduled_deletions 
                        WHERE scheduled_deletions.movie_id = scored_movies_cache.movie_id
                        AND scheduled_deletions.status = 'scheduled'
                        LIMIT 1
                    )
                WHERE movie_id IN (
                    SELECT movie_id FROM scheduled_deletions WHERE status = 'scheduled'
                )
            """)
            print(f"Migration applied: copied {cursor.rowcount} scheduled movies to scored_movies_cache")
    except Exception as e:
        print(f"Migration note: could not copy scheduled data (may already be migrated): {e}")
    
    # Migration: Add index on scheduled_for_deletion for faster queries
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scored_movies_cache_scheduled ON scored_movies_cache(scheduled_for_deletion, scheduled_date)")
        print("Migration applied: added index on scored_movies_cache(scheduled_for_deletion, scheduled_date)")
    except Exception:
        pass  # Index may already exist

    # Migration: Add index on manual_for_deletion for faster queries
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_scored_movies_cache_manual ON scored_movies_cache(manual_for_deletion)")
        print("Migration applied: added index on scored_movies_cache(manual_for_deletion)")
    except Exception:
        pass  # Index may already exist

    # Migration: Drop old scheduled_deletions table (cleanup)
    try:
        cursor.execute("DROP TABLE IF EXISTS scheduled_deletions")
        print("Migration applied: dropped old scheduled_deletions table")
    except Exception as e:
        print(f"Migration note: could not drop scheduled_deletions: {e}") 

    # Migration: Add missing columns to deletion_history
    for column, column_type in [
        ("age_days", "INTEGER"),
        ("tmdb_rating", "REAL"),
        ("quality", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE deletion_history ADD COLUMN {column} {column_type}")
            print(f"Migration applied: added {column} to deletion_history")
        except Exception:
            pass  # Column already exists, safe to ignore

    # Migration: Add manual_for_deletion to scored_movies_cache
    try:
        cursor.execute("ALTER TABLE scored_movies_cache ADD COLUMN manual_for_deletion BOOLEAN DEFAULT 0")
        print("Migration applied: added manual_for_deletion to scored_movies_cache")
    except Exception:
        pass  # Column already exists, safe to ignore   

    conn.commit()
    conn.close()


async def execute_async(conn, query, params=None):
    """
    Execute a database query asynchronously using a thread pool.
    Prevents blocking the event loop during long-running queries.
    """
    if params is None:
        params = ()
    return await asyncio.to_thread(conn.execute, query, params)


async def fetch_all_async(conn, query, params=None):
    """
    Fetch all results asynchronously.
    """
    result = await execute_async(conn, query, params)
    return await asyncio.to_thread(result.fetchall)


async def fetch_one_async(conn, query, params=None):
    """
    Fetch one result asynchronously.
    """
    result = await execute_async(conn, query, params)
    return await asyncio.to_thread(result.fetchone)


async def commit_async(conn):
    """
    Commit transaction asynchronously.
    """
    return await asyncio.to_thread(conn.commit)


async def rollback_async(conn):
    """
    Rollback transaction asynchronously.
    """
    return await asyncio.to_thread(conn.rollback)