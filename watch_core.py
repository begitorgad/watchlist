# watch_core.py
from __future__ import annotations

import os
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Literal, Any
import re

import requests

MediaType = Literal["movie", "show", "youtube"]
TMDBMediaType = Literal["movie", "tv"]
_norm_re = re.compile(r"[^a-z0-9]+")
TMDB_BASE = "https://api.themoviedb.org/3"


# -------------------------
# Data shapes (GUI-friendly)
# -------------------------
@dataclass(frozen=True)
class TitleItem:
    id: int
    title: str
    type: MediaType
    seen: bool
    tmdb_id: Optional[int]
    year: Optional[int]
    runtime_minutes: Optional[int]
    genres: list[str]


@dataclass(frozen=True)
class TmdbChoice:
    media_type: TMDBMediaType  
    id: int                   
    title: str
    year: Optional[int]
    overview: str



@dataclass(frozen=True)
class AddOrShowResult:
    status: Literal["exists", "needs_choice", "added", "cancelled", "error"]
    item: Optional[TitleItem] = None
    choices: Optional[list[TmdbChoice]] = None
    message: Optional[str] = None


# -------------------------
# Utilities
# -------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def norm_title(s: str) -> str:
    s = s.strip().lower()
    s = s.replace("&", "and")
    s = _norm_re.sub(" ", s)
    return " ".join(s.split())

# -------------------------
# DB wrapper
# -------------------------
class WatchDB:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS titles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    title_norm TEXT NOT NULL UNIQUE,
                    type TEXT NOT NULL DEFAULT 'movie',  -- movie|show|youtube
                    seen INTEGER NOT NULL DEFAULT 0,

                    tmdb_id INTEGER,        -- for movie/show added via TMDB
                    year INTEGER,
                    runtime_minutes INTEGER,

                    notes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS genres (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS title_genres (
                    title_id INTEGER NOT NULL,
                    genre_id INTEGER NOT NULL,
                    PRIMARY KEY (title_id, genre_id),
                    FOREIGN KEY (title_id) REFERENCES titles(id) ON DELETE CASCADE,
                    FOREIGN KEY (genre_id) REFERENCES genres(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_titles_seen ON titles(seen);
                CREATE INDEX IF NOT EXISTS idx_titles_type ON titles(type);
                CREATE INDEX IF NOT EXISTS idx_title_genres_genre ON title_genres(genre_id);
                

                CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL          -- store as "#RRGGBB"
                );

                CREATE TABLE IF NOT EXISTS title_tags (
                    title_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    PRIMARY KEY (title_id, tag_id),
                    FOREIGN KEY (title_id) REFERENCES titles(id) ON DELETE CASCADE,
                    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_tags_name_nocase ON tags(name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_title_tags_title ON title_tags(title_id);
                CREATE INDEX IF NOT EXISTS idx_title_tags_tag ON title_tags(tag_id);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_titles_type_tmdb ON titles(type, tmdb_id) WHERE tmdb_id IS NOT NULL;

                """
            )
            conn.commit()

    def get_by_tmdb(self, tmdb_id: int, type_: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM titles WHERE tmdb_id = ? AND type = ?",
                (tmdb_id, type_),
            ).fetchone()


    # -------- titles --------
    def get_by_title_norm(self, title_norm: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM titles WHERE title_norm = ?",
                (title_norm,),
            ).fetchone()

    def get_by_id(self, title_id: int) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()

    def search_like(self, title: str, limit: int = 10) -> list[sqlite3.Row]:
        words = norm_title(title).split()
        if not words:
            return []
        where = " AND ".join(["title_norm LIKE ?"] * len(words))
        params = [f"%{w}%" for w in words] + [limit]

        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM titles WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()


    def set_seen(self, title_id: int, seen: bool) -> TitleItem:
        ts = now_iso()
        with self.connect() as conn:
            conn.execute(
                "UPDATE titles SET seen = ?, updated_at = ? WHERE id = ?",
                (1 if seen else 0, ts, title_id),
            )
            conn.commit()
        return self.get_item(title_id)

    def insert_local(self, title: str, type_: MediaType = "movie", notes: Optional[str] = None) -> TitleItem:
        title = title.strip()
        tnorm = norm_title(title)
        ts = now_iso()

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO titles (title, title_norm, type, seen, tmdb_id, year, runtime_minutes, notes, created_at, updated_at)
                VALUES (?, ?, ?, 0, NULL, NULL, NULL, ?, ?, ?)
                """,
                (title, tnorm, type_, notes, ts, ts),
            )
            conn.commit()

            row = conn.execute("SELECT id FROM titles WHERE title_norm = ?", (tnorm,)).fetchone()
            if not row:
                raise RuntimeError("Failed to insert local title.")
            return self.get_item(int(row["id"]))

    def insert_tmdb(
        self,
        title: str,
        type_: MediaType,
        tmdb_id: int,
        year: Optional[int],
        runtime_minutes: Optional[int],
        genres: Iterable[str],
    ) -> tuple[TitleItem, bool]:
        title = title.strip()
        tnorm = norm_title(title)
        ts = now_iso()

        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM titles WHERE tmdb_id = ? AND type = ?",
                (tmdb_id, type_),
            ).fetchone()
            if existing:
                return self.get_item(int(existing["id"])), False

            existing = conn.execute(
                "SELECT id FROM titles WHERE title_norm = ?",
                (tnorm,),
            ).fetchone()
            if existing:
                return self.get_item(int(existing["id"])), False

            conn.execute(
                """
                INSERT INTO titles (title, title_norm, type, seen, tmdb_id, year, runtime_minutes, notes, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?, ?, NULL, ?, ?)
                """,
                (title, tnorm, type_, tmdb_id, year, runtime_minutes, ts, ts),
            )
            conn.commit()

            row = conn.execute("SELECT id FROM titles WHERE tmdb_id = ? AND type = ?", (tmdb_id, type_)).fetchone()
            if not row:
                raise RuntimeError("Failed to insert TMDB title.")
            title_id = int(row["id"])

            self._set_title_genres(conn, title_id, genres)
            conn.commit()

        return self.get_item(title_id), True


    def list_titles(
            self,
            unseen_only: bool = False,
            type_: Optional[MediaType] = None,
            genre: Optional[str] = None,
            tag: Optional[str] = None,
            limit: int = 100,
    ) -> list[TitleItem]:
        where: list[str] = []
        args: list[Any] = []
        joins: list[str] = []
    
        if unseen_only:
            where.append("t.seen = 0")
    
        if type_:
            where.append("t.type = ?")
            args.append(type_)
    
        if genre:
            joins.append(
                """
                JOIN title_genres tg ON tg.title_id = t.id
                JOIN genres g ON g.id = tg.genre_id
                """
            )
            where.append("g.name = ?")
            args.append(genre.strip())
    
        if tag:
            joins.append(
                """
                JOIN title_tags tt ON tt.title_id = t.id
                JOIN tags tagt ON tagt.id = tt.tag_id
                """
            )
            where.append("tagt.name = ?")
            args.append(tag.strip())
    
        q = "SELECT DISTINCT t.* FROM titles t " + " ".join(joins)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY t.updated_at DESC LIMIT ?"
        args.append(limit)
    
        with self.connect() as conn:
            rows = conn.execute(q, args).fetchall()
    
            ids = [int(r["id"]) for r in rows]
            genres_map = self._fetch_genres_for_title_ids(conn, ids)
    
        return [
            self._row_to_item_with_genres(r, genres_map.get(int(r["id"]), []))
            for r in rows
        ]


    def list_genres(self) -> list[tuple[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT g.name, COUNT(*) AS count
                FROM genres g
                JOIN title_genres tg ON tg.genre_id = g.id
                GROUP BY g.id
                ORDER BY g.name ASC
                """
            ).fetchall()
        return [(r["name"], int(r["count"])) for r in rows]

    def random_pick_one(
            self,
            unseen_only: bool = False,
            type_: Optional[MediaType] = None,
            genre: Optional[str] = None,
            tag: Optional[str] = None,
    ) -> Optional[TitleItem]:
        where: list[str] = []
        args: list[Any] = []
        joins: list[str] = []
    
        if unseen_only:
            where.append("t.seen = 0")
    
        if type_:
            where.append("t.type = ?")
            args.append(type_)
    
        if genre:
            joins.append(
                """
                JOIN title_genres tg ON tg.title_id = t.id
                JOIN genres g ON g.id = tg.genre_id
                """
            )
            where.append("g.name = ?")
            args.append(genre.strip())
    
        if tag:
            joins.append(
                """
                JOIN title_tags tt ON tt.title_id = t.id
                JOIN tags tagt ON tagt.id = tt.tag_id
                """
            )
            where.append("tagt.name = ?")
            args.append(tag.strip())
    
        q = "SELECT DISTINCT t.* FROM titles t " + " ".join(joins)
        if where:
            q += " WHERE " + " AND ".join(where)
    
        q += " ORDER BY RANDOM() LIMIT 1"
    
        with self.connect() as conn:
            row = conn.execute(q, args).fetchone()
            if not row:
                return None
    
            genres_map = self._fetch_genres_for_title_ids(conn, [int(row["id"])])
            genres = genres_map.get(int(row["id"]), [])
    
        return self._row_to_item_with_genres(row, genres)


    def get_item(self, title_id: int) -> TitleItem:
        row = self.get_by_id(title_id)
        if not row:
            raise RuntimeError("Title not found.")
        return self._row_to_item(row)


    def delete_title(self, title_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM titles WHERE id = ?", (title_id,))
            conn.commit()

    # -------- genres internal --------
    def _get_or_create_genre_id(self, conn: sqlite3.Connection, name: str) -> int:
        name = name.strip()
        row = conn.execute("SELECT id FROM genres WHERE name = ?", (name,)).fetchone()
        if row:
            return int(row["id"])
        conn.execute("INSERT INTO genres (name) VALUES (?)", (name,))
        row = conn.execute("SELECT id FROM genres WHERE name = ?", (name,)).fetchone()
        if not row:
            raise RuntimeError("Failed to create genre.")
        return int(row["id"])

    def _set_title_genres(self, conn: sqlite3.Connection, title_id: int, genre_names: Iterable[str]) -> None:
        conn.execute("DELETE FROM title_genres WHERE title_id = ?", (title_id,))
        for g in genre_names:
            if not g:
                continue
            gid = self._get_or_create_genre_id(conn, g)
            conn.execute(
                "INSERT OR IGNORE INTO title_genres (title_id, genre_id) VALUES (?, ?)",
                (title_id, gid),
            )

    def _fetch_title_genres(self, conn: sqlite3.Connection, title_id: int) -> list[str]:
        rows = conn.execute(
            """
            SELECT g.name
            FROM genres g
            JOIN title_genres tg ON tg.genre_id = g.id
            WHERE tg.title_id = ?
            ORDER BY g.name ASC
            """,
            (title_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _row_to_item(self, row: sqlite3.Row) -> TitleItem:
        with self.connect() as conn:
            genres = self._fetch_title_genres(conn, int(row["id"]))
        return TitleItem(
            id=int(row["id"]),
            title=str(row["title"]),
            type=row["type"],
            seen=bool(row["seen"]),
            tmdb_id=int(row["tmdb_id"]) if row["tmdb_id"] is not None else None,
            year=int(row["year"]) if row["year"] is not None else None,
            runtime_minutes=int(row["runtime_minutes"]) if row["runtime_minutes"] is not None else None,
            genres=genres,
        )

    def _fetch_genres_for_title_ids(self, conn: sqlite3.Connection, title_ids: list[int]) -> dict[int, list[str]]:
        if not title_ids:
            return {}
        qmarks = ",".join(["?"] * len(title_ids))
        rows = conn.execute(
            f"""
            SELECT tg.title_id, g.name
            FROM title_genres tg
            JOIN genres g ON g.id = tg.genre_id
            WHERE tg.title_id IN ({qmarks})
            ORDER BY g.name ASC
            """,
            title_ids,
        ).fetchall()
    
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(int(r["title_id"]), []).append(str(r["name"]))
        return out

    def _row_to_item_with_genres(self, row: sqlite3.Row, genres: list[str]) -> TitleItem:
        return TitleItem(
            id=int(row["id"]),
            title=str(row["title"]),
            type=row["type"],
            seen=bool(row["seen"]),
            tmdb_id=int(row["tmdb_id"]) if row["tmdb_id"] is not None else None,
            year=int(row["year"]) if row["year"] is not None else None,
            runtime_minutes=int(row["runtime_minutes"]) if row["runtime_minutes"] is not None else None,
            genres=genres,
        )

    def search_like_items(self, title: str, limit: int = 10) -> list[TitleItem]:
        words = norm_title(title).split()
        if not words:
            return []
    
        where = " AND ".join(["t.title_norm LIKE ?"] * len(words))
        params = [f"%{w}%" for w in words] + [limit]
    
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT t.* FROM titles t WHERE {where} ORDER BY t.updated_at DESC LIMIT ?",
                params,
            ).fetchall()
    
            ids = [int(r["id"]) for r in rows]
            genres_map = self._fetch_genres_for_title_ids(conn, ids)
    
        return [self._row_to_item_with_genres(r, genres_map.get(int(r["id"]), [])) for r in rows]



    # -------- tags internal --------

    def list_tags(self) -> list[tuple[int, str, str]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id, name, color FROM tags ORDER BY name ASC").fetchall()
        return [(int(r["id"]), r["name"], r["color"]) for r in rows]

    def create_tag(self, name: str, color: str) -> int:
        name = name.strip()
        color = color.strip()
        try:
            with self.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO tags (name, color) VALUES (?, ?)",
                    (name, color),
                )
                conn.commit()
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            raise ValueError(f"Tag '{name}' already exists.")

    def delete_tag(self, tag_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()

    def set_title_tags(self, title_id: int, tag_ids: list[int]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM title_tags WHERE title_id = ?", (title_id,))
            for tid in tag_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO title_tags (title_id, tag_id) VALUES (?, ?)",
                    (title_id, tid),
                )
            conn.commit()

    def get_title_tags(self, title_id: int) -> list[tuple[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.name, t.color
                FROM tags t
                JOIN title_tags tt ON tt.tag_id = t.id
                WHERE tt.title_id = ?
                ORDER BY t.name ASC
                """,
                (title_id,),
            ).fetchall()
        return [(r["name"], r["color"]) for r in rows]

    def get_tags_for_title_ids(self, title_ids: list[int]) -> dict[int, list[tuple[str, str]]]:
        if not title_ids:
            return {}
        qmarks = ",".join(["?"] * len(title_ids))
        sql = f"""
        SELECT tt.title_id, t.name, t.color
        FROM title_tags tt
        JOIN tags t ON t.id = tt.tag_id
        WHERE tt.title_id IN ({qmarks})
        ORDER BY t.name ASC
        """
        with self.connect() as conn:
            rows = conn.execute(sql, title_ids).fetchall()

        out: dict[int, list[tuple[str, str]]] = {}
        for r in rows:
            out.setdefault(int(r["title_id"]), []).append((str(r["name"]), str(r["color"])))
        return out


    def update_tag(self, tag_id: int, name: str, color: str) -> None:
        name = name.strip()
        color = color.strip()
        try:
            with self.connect() as conn:
                conn.execute(
                    "UPDATE tags SET name = ?, color = ? WHERE id = ?",
                    (name, color, tag_id),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"Tag '{name}' already exists.")





# -------------------------
# TMDB client (no prompting/printing)
# -------------------------
class TMDBClient:
    def __init__(self, token_env: str = "TMDB_TOKEN"):
        self.token_env = token_env

    def _headers(self) -> dict:
        token = os.getenv(self.token_env)
        if not token:
            raise RuntimeError(f"Missing {self.token_env} env var (TMDB v4 Read Access Token).")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def search_movie(self, query: str, language: str = "en-US") -> list[dict]:
        r = requests.get(
            f"{TMDB_BASE}/search/movie",
            headers=self._headers(),
            params={"query": query, "include_adult": "false", "language": language},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("results", [])

    def search_tv(self, query: str, language: str = "en-US") -> list[dict]:
        r = requests.get(
            f"{TMDB_BASE}/search/tv",
            headers=self._headers(),
            params={"query": query, "include_adult": "false", "language": language},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("results", [])

    def movie_details(self, movie_id: int, language: str = "en-US") -> dict:
        r = requests.get(
            f"{TMDB_BASE}/movie/{movie_id}",
            headers=self._headers(),
            params={"language": language},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def tv_details(self, tv_id: int, language: str = "en-US") -> dict:
        r = requests.get(
            f"{TMDB_BASE}/tv/{tv_id}",
            headers=self._headers(),
            params={"language": language},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def search_any(self, query: str, language: str = "en-US", limit: int = 8) -> list[TmdbChoice]:
        movies = self.search_movie(query, language=language)[:10]
        tvs = self.search_tv(query, language=language)[:10]

        merged: list[dict] = []
        for r in movies:
            if not r.get("id"):
                continue
            title = (r.get("title") or "?").strip()
            date = (r.get("release_date") or "").strip()
            year = int(date[:4]) if date[:4].isdigit() else None
            merged.append(
                dict(
                    media_type="movie",
                    id=int(r["id"]),
                    title=title,
                    year=year,
                    overview=(r.get("overview") or "").strip(),
                    popularity=float(r.get("popularity") or 0),
                    vote_count=int(r.get("vote_count") or 0),
                )
            )

        for r in tvs:
            if not r.get("id"):
                continue
            title = (r.get("name") or "?").strip()
            date = (r.get("first_air_date") or "").strip()
            year = int(date[:4]) if date[:4].isdigit() else None
            merged.append(
                dict(
                    media_type="tv",
                    id=int(r["id"]),
                    title=title,
                    year=year,
                    overview=(r.get("overview") or "").strip(),
                    popularity=float(r.get("popularity") or 0),
                    vote_count=int(r.get("vote_count") or 0),
                )
            )

        merged.sort(key=lambda x: (x["popularity"], x["vote_count"]), reverse=True)
        merged = merged[:limit]

        return [
            TmdbChoice(
                media_type=item["media_type"],
                id=item["id"],
                title=item["title"],
                year=item["year"],
                overview=item["overview"],
            )
            for item in merged
        ]

    def fetch_details_as_local_fields(self, choice: TmdbChoice, language: str = "en-US") -> tuple[str, MediaType, int, Optional[int], Optional[int], list[str]]:
        if choice.media_type == "movie":
            details = self.movie_details(choice.id, language=language)
            title = (details.get("title") or choice.title).strip()
            date = (details.get("release_date") or "").strip()
            year = int(date[:4]) if date[:4].isdigit() else None
            runtime = details.get("runtime")
            genres = [g.get("name") for g in (details.get("genres") or []) if g.get("name")]
            return title, "movie", choice.id, year, runtime, genres

        details = self.tv_details(choice.id, language=language)
        title = (details.get("name") or choice.title).strip()
        date = (details.get("first_air_date") or "").strip()
        year = int(date[:4]) if date[:4].isdigit() else None
        ert = details.get("episode_run_time") or []
        runtime = int(ert[0]) if ert and isinstance(ert[0], int) else None
        genres = [g.get("name") for g in (details.get("genres") or []) if g.get("name")]
        return title, "show", choice.id, year, runtime, genres


# -------------------------
# High-level service for GUI/CLI
# -------------------------
class WatchService:
    def __init__(self, db: WatchDB, tmdb: Optional[TMDBClient] = None):
        self.db = db
        self.tmdb = tmdb

    def add_or_show_start(self, typed_title: str, language: str = "en-US") -> AddOrShowResult:
        existing = self.db.get_by_title_norm(norm_title(typed_title))
        if existing:
            return AddOrShowResult(status="exists", item=self.db._row_to_item(existing))

        if not self.tmdb:
            return AddOrShowResult(status="error", message="TMDB client not configured.")

        try:
            choices = self.tmdb.search_any(typed_title, language=language, limit=8)
        except RuntimeError as e:
            return AddOrShowResult(status="error", message=str(e))
        except requests.RequestException as e:
            return AddOrShowResult(status="error", message=f"TMDB request failed: {e}")

        if not choices:
            return AddOrShowResult(status="needs_choice", choices=[], message="No TMDB results.")
        return AddOrShowResult(status="needs_choice", choices=choices)

    def add_or_show_confirm_tmdb_choice(self, choice: TmdbChoice, language: str = "en-US") -> AddOrShowResult:
        if not self.tmdb:
            return AddOrShowResult(status="error", message="TMDB client not configured.")

        try:
            title, local_type, tmdb_id, year, runtime, genres = self.tmdb.fetch_details_as_local_fields(choice, language=language)
        except RuntimeError as e:
            return AddOrShowResult(status="error", message=str(e))
        except requests.RequestException as e:
            return AddOrShowResult(status="error", message=f"TMDB details request failed: {e}")

        item, inserted = self.db.insert_tmdb(
            title=title,
            type_=local_type,
            tmdb_id=tmdb_id,
            year=year,
            runtime_minutes=runtime,
            genres=genres,
        )

        if inserted:
            return AddOrShowResult(status="added", item=item)
        return AddOrShowResult(status="exists", item=item, message="Already in your list.")


    def delete_title(self, title_id: int) -> None:
        self.db.delete_title(title_id)

    def tmdb_search_any(self, query: str, language: str = "en-US", limit: int = 8) -> list[TmdbChoice]:
        if not self.tmdb:
            raise RuntimeError("TMDB client not configured.")
        return self.tmdb.search_any(query, language=language, limit=limit)


    def add_local(self, title: str, type_: MediaType = "movie") -> TitleItem:
        return self.db.insert_local(title, type_=type_)

    def set_seen(self, title_id: int, seen: bool) -> TitleItem:
        return self.db.set_seen(title_id, seen)

    def list_titles(
        self,
        unseen_only: bool = False,
        type_: Optional[MediaType] = None,
        genre: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 200,
    ) -> list[TitleItem]:
        return self.db.list_titles(unseen_only=unseen_only, type_=type_, genre=genre, tag=tag, limit=limit)


    def list_genres(self) -> list[tuple[str, int]]:
        return self.db.list_genres()

    def random_pick(
            self,
            unseen_only: bool = False,
            type_: Optional[MediaType] = None,
            genre: Optional[str] = None,
            tag: Optional[str] = None,
    ) -> Optional[TitleItem]:
        return self.db.random_pick_one(
            unseen_only=unseen_only,
            type_=type_,
            genre=genre,
            tag=tag,
        )


    def suggestions(self, typed: str, limit: int = 8) -> list[TitleItem]:
        return self.db.search_like_items(typed, limit=limit)
    
    def list_tags(self):
        return self.db.list_tags()

    def create_tag(self, name: str, color: str) -> int:
        return self.db.create_tag(name, color)

    def delete_tag(self, tag_id: int) -> None:
        self.db.delete_tag(tag_id)

    def set_title_tags(self, title_id: int, tag_ids: list[int]) -> None:
        self.db.set_title_tags(title_id, tag_ids)

    def update_tag(self, tag_id: int, name: str, color: str) -> None:
        self.db.update_tag(tag_id, name, color)


    def get_title_tags(self, title_id: int):
        return self.db.get_title_tags(title_id)

    def get_tags_for_title_ids(self, title_ids: list[int]):
        return self.db.get_tags_for_title_ids(title_ids)

