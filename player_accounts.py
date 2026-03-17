"""Player account persistence for PokeBattle Journey Mode.

Simple username-based accounts stored in SQLite.
No passwords — just a username + auto-generated token for session persistence.
"""

import json
import random
import secrets
import sqlite3
import time


class AccountManager:
    """Manages player accounts in SQLite."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._init_tables()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_tables(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                token TEXT UNIQUE NOT NULL,
                starter_dex_id INTEGER,
                pokeballs INTEGER DEFAULT 10,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS player_pokemon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                dex_id INTEGER NOT NULL,
                nickname TEXT,
                level INTEGER DEFAULT 5,
                xp INTEGER DEFAULT 0,
                is_in_team INTEGER DEFAULT 0,
                team_slot INTEGER,
                caught_at INTEGER NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id)
            );

            CREATE INDEX IF NOT EXISTS idx_pp_player ON player_pokemon(player_id);
            CREATE INDEX IF NOT EXISTS idx_pp_team ON player_pokemon(player_id, is_in_team);
        """)
        conn.commit()
        conn.close()

    def register(self, username):
        """Register a new player. Returns (player_dict, error_string)."""
        username = username.strip()
        if not username or len(username) < 2 or len(username) > 16:
            return None, "Username must be 2-16 characters."
        if not all(c.isalnum() or c == ' ' for c in username):
            return None, "Letters, numbers, and spaces only."

        token = secrets.token_urlsafe(24)
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO players (username, token, created_at) VALUES (?, ?, ?)",
                (username, token, int(time.time()))
            )
            conn.commit()
            player_id = conn.execute(
                "SELECT id FROM players WHERE token = ?", (token,)
            ).fetchone()["id"]
            conn.close()
            return {"id": player_id, "username": username, "token": token,
                    "starter_dex_id": None, "pokeballs": 10}, None
        except sqlite3.IntegrityError:
            conn.close()
            return None, "Username already taken."

    def login_by_token(self, token):
        """Resume session by token. Returns player_dict or None."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM players WHERE token = ?", (token,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return self._row_to_dict(row)

    def login_by_username(self, username):
        """Login by username (no password). Returns player_dict or None."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM players WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return self._row_to_dict(row)

    def choose_starter(self, player_id, dex_id):
        """Set starter Pokemon and add it to collection. Returns success bool."""
        if dex_id not in (1, 4, 7):  # Bulbasaur, Charmander, Squirtle
            return False

        conn = self._conn()
        # Check player exists and hasn't chosen yet
        row = conn.execute(
            "SELECT starter_dex_id FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        if not row or row["starter_dex_id"] is not None:
            conn.close()
            return False

        conn.execute(
            "UPDATE players SET starter_dex_id = ? WHERE id = ?",
            (dex_id, player_id)
        )
        # Add starter to collection as team slot 0
        conn.execute(
            """INSERT INTO player_pokemon
               (player_id, dex_id, level, xp, is_in_team, team_slot, caught_at)
               VALUES (?, ?, 5, 0, 1, 0, ?)""",
            (player_id, dex_id, int(time.time()))
        )
        conn.commit()
        conn.close()
        return True

    def get_team(self, player_id):
        """Get player's active team (up to 6 Pokemon in team slots)."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT * FROM player_pokemon
               WHERE player_id = ? AND is_in_team = 1
               ORDER BY team_slot""",
            (player_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_pokemon(self, player_id):
        """Get all Pokemon owned by player."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM player_pokemon WHERE player_id = ? ORDER BY caught_at",
            (player_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_profile(self, player_id):
        """Get full player profile with team."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        if not row:
            conn.close()
            return None

        team = conn.execute(
            """SELECT * FROM player_pokemon
               WHERE player_id = ? AND is_in_team = 1
               ORDER BY team_slot""",
            (player_id,)
        ).fetchall()

        total_pokemon = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ?",
            (player_id,)
        ).fetchone()["cnt"]

        conn.close()

        return {
            "id": row["id"],
            "username": row["username"],
            "token": row["token"],
            "starter_dex_id": row["starter_dex_id"],
            "pokeballs": row["pokeballs"],
            "team": [dict(r) for r in team],
            "total_pokemon": total_pokemon,
        }

    def add_pokeballs(self, player_id, count):
        """Add Poke Balls to player inventory."""
        conn = self._conn()
        conn.execute(
            "UPDATE players SET pokeballs = pokeballs + ? WHERE id = ?",
            (count, player_id)
        )
        conn.commit()
        conn.close()

    def use_pokeball(self, player_id):
        """Use one Poke Ball. Returns True if player had one to use."""
        conn = self._conn()
        row = conn.execute(
            "SELECT pokeballs FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        if not row or row["pokeballs"] <= 0:
            conn.close()
            return False
        conn.execute(
            "UPDATE players SET pokeballs = pokeballs - 1 WHERE id = ?",
            (player_id,)
        )
        conn.commit()
        conn.close()
        return True

    def catch_pokemon(self, player_id, dex_id, level):
        """Add a caught Pokemon to player's collection."""
        conn = self._conn()
        # Check how many are in team
        team_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ? AND is_in_team = 1",
            (player_id,)
        ).fetchone()["cnt"]

        in_team = 1 if team_count < 6 else 0
        team_slot = team_count if team_count < 6 else None

        conn.execute(
            """INSERT INTO player_pokemon
               (player_id, dex_id, level, xp, is_in_team, team_slot, caught_at)
               VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (player_id, dex_id, level, in_team, team_slot, int(time.time()))
        )
        conn.commit()
        conn.close()
        return in_team == 1

    def _row_to_dict(self, row):
        return {
            "id": row["id"],
            "username": row["username"],
            "token": row["token"],
            "starter_dex_id": row["starter_dex_id"],
            "pokeballs": row["pokeballs"],
        }
