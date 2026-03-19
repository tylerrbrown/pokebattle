"""Player account persistence for PokeBattle Journey Mode.

Simple username-based accounts stored in SQLite.
No passwords — just a username + auto-generated token for session persistence.
"""

import json
import math
import random
import secrets
import sqlite3
import time

# XP curve: medium-fast growth rate — total XP at level N = (4/5) * N^3
def xp_for_level(level):
    """Total XP required to reach a given level."""
    if level <= 1:
        return 0
    return int((4 / 5) * level ** 3)


def xp_to_next_level(level, current_xp):
    """XP remaining to reach the next level."""
    if level >= 100:
        return 0
    return max(0, xp_for_level(level + 1) - current_xp)


def xp_progress_info(level, current_xp):
    """Compute XP progress data for UI display."""
    if level >= 100:
        return {"xp_progress": 1.0, "xp_to_next": 0,
                "xp_for_current_level": current_xp, "xp_for_next_level": current_xp}
    cur_level_xp = xp_for_level(level)
    next_level_xp = xp_for_level(level + 1)
    span = next_level_xp - cur_level_xp
    progress_in_level = current_xp - cur_level_xp
    return {
        "xp_progress": max(0.0, min(1.0, progress_in_level / span)) if span > 0 else 1.0,
        "xp_to_next": max(0, next_level_xp - current_xp),
        "xp_for_current_level": cur_level_xp,
        "xp_for_next_level": next_level_xp,
    }


def calc_xp_yield(opponent_level, base_exp, is_wild=True):
    """Calculate XP earned from defeating an opponent.
    Simplified Gen 1: (base_exp * opponent_level) / 7, boosted 1.5x for faster progression.
    Trainer battles yield an additional 1.5x, gym leaders yield 2x.
    """
    xp = int((base_exp * opponent_level) / 7)
    # 1.5x base XP boost for faster, more fun progression
    xp = int(xp * 1.5)
    if not is_wild:
        xp = int(xp * 1.5)
    return max(1, xp)


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
                currency INTEGER DEFAULT 500,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS player_pokemon (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                dex_id INTEGER NOT NULL,
                nickname TEXT,
                level INTEGER DEFAULT 5,
                xp INTEGER DEFAULT 0,
                moves TEXT,
                is_in_team INTEGER DEFAULT 0,
                team_slot INTEGER,
                caught_at INTEGER NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id)
            );

            CREATE TABLE IF NOT EXISTS player_badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                gym_id INTEGER NOT NULL,
                earned_at INTEGER NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id),
                UNIQUE(player_id, gym_id)
            );

            CREATE TABLE IF NOT EXISTS player_progression (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                milestone TEXT NOT NULL,
                completed_at INTEGER NOT NULL,
                FOREIGN KEY (player_id) REFERENCES players(id),
                UNIQUE(player_id, milestone)
            );

            CREATE INDEX IF NOT EXISTS idx_pp_player ON player_pokemon(player_id);
            CREATE INDEX IF NOT EXISTS idx_pp_team ON player_pokemon(player_id, is_in_team);

            CREATE TABLE IF NOT EXISTS player_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                quantity INTEGER DEFAULT 0,
                FOREIGN KEY (player_id) REFERENCES players(id),
                UNIQUE(player_id, item_type)
            );
            CREATE INDEX IF NOT EXISTS idx_inv_player ON player_inventory(player_id);
        """)
        # Schema migrations for existing databases
        self._migrate(conn)
        conn.commit()
        conn.close()

    def _migrate(self, conn):
        """Add columns/tables that may be missing from older databases."""
        # Check existing columns in players
        cols = {row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
        if "currency" not in cols:
            conn.execute("ALTER TABLE players ADD COLUMN currency INTEGER DEFAULT 500")
        # Check existing columns in player_pokemon
        cols = {row[1] for row in conn.execute("PRAGMA table_info(player_pokemon)").fetchall()}
        if "moves" not in cols:
            conn.execute("ALTER TABLE player_pokemon ADD COLUMN moves TEXT")

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
        if dex_id not in (1, 4, 7, 152, 155, 158):  # Gen 1 & 2 starters
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

    @staticmethod
    def _enrich_pokemon_xp(pokemon_dict):
        """Add XP progress fields to a pokemon dict."""
        info = xp_progress_info(pokemon_dict.get("level", 1), pokemon_dict.get("xp", 0))
        pokemon_dict.update(info)
        return pokemon_dict

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
        return [self._enrich_pokemon_xp(dict(r)) for r in rows]

    def get_all_pokemon(self, player_id):
        """Get all Pokemon owned by player."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM player_pokemon WHERE player_id = ? ORDER BY caught_at",
            (player_id,)
        ).fetchall()
        conn.close()
        return [self._enrich_pokemon_xp(dict(r)) for r in rows]

    def get_storage(self, player_id):
        """Get Pokemon NOT in the active team (storage/backpack)."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT * FROM player_pokemon
               WHERE player_id = ? AND is_in_team = 0
               ORDER BY caught_at""",
            (player_id,)
        ).fetchall()
        conn.close()
        return [self._enrich_pokemon_xp(dict(r)) for r in rows]

    def swap_team_member(self, player_id, team_pokemon_id, storage_pokemon_id):
        """Swap a team member with a storage Pokemon."""
        conn = self._conn()
        team_row = conn.execute(
            "SELECT team_slot FROM player_pokemon WHERE id = ? AND player_id = ? AND is_in_team = 1",
            (team_pokemon_id, player_id)
        ).fetchone()
        if not team_row:
            conn.close()
            return False
        slot = team_row["team_slot"]
        storage_row = conn.execute(
            "SELECT id FROM player_pokemon WHERE id = ? AND player_id = ? AND is_in_team = 0",
            (storage_pokemon_id, player_id)
        ).fetchone()
        if not storage_row:
            conn.close()
            return False
        conn.execute(
            "UPDATE player_pokemon SET is_in_team = 0, team_slot = NULL WHERE id = ?",
            (team_pokemon_id,)
        )
        conn.execute(
            "UPDATE player_pokemon SET is_in_team = 1, team_slot = ? WHERE id = ?",
            (slot, storage_pokemon_id)
        )
        conn.commit()
        conn.close()
        return True

    def move_to_team(self, player_id, pokemon_id):
        """Move a storage Pokemon to team (if team < 6)."""
        conn = self._conn()
        team_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ? AND is_in_team = 1",
            (player_id,)
        ).fetchone()["cnt"]
        if team_count >= 6:
            conn.close()
            return False
        row = conn.execute(
            "SELECT id FROM player_pokemon WHERE id = ? AND player_id = ? AND is_in_team = 0",
            (pokemon_id, player_id)
        ).fetchone()
        if not row:
            conn.close()
            return False
        conn.execute(
            "UPDATE player_pokemon SET is_in_team = 1, team_slot = ? WHERE id = ?",
            (team_count, pokemon_id)
        )
        conn.commit()
        conn.close()
        return True

    def move_to_storage(self, player_id, pokemon_id):
        """Move a team Pokemon to storage (must keep at least 1 on team)."""
        conn = self._conn()
        team_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ? AND is_in_team = 1",
            (player_id,)
        ).fetchone()["cnt"]
        if team_count <= 1:
            conn.close()
            return False
        row = conn.execute(
            "SELECT team_slot FROM player_pokemon WHERE id = ? AND player_id = ? AND is_in_team = 1",
            (pokemon_id, player_id)
        ).fetchone()
        if not row:
            conn.close()
            return False
        removed_slot = row["team_slot"]
        conn.execute(
            "UPDATE player_pokemon SET is_in_team = 0, team_slot = NULL WHERE id = ?",
            (pokemon_id,)
        )
        # Compact team slots: shift higher slots down
        conn.execute(
            """UPDATE player_pokemon SET team_slot = team_slot - 1
               WHERE player_id = ? AND is_in_team = 1 AND team_slot > ?""",
            (player_id, removed_slot)
        )
        conn.commit()
        conn.close()
        return True

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

        badges = conn.execute(
            "SELECT gym_id FROM player_badges WHERE player_id = ? ORDER BY gym_id",
            (player_id,)
        ).fetchall()

        milestones = conn.execute(
            "SELECT milestone FROM player_progression WHERE player_id = ?",
            (player_id,)
        ).fetchall()

        inventory = conn.execute(
            "SELECT item_type, quantity FROM player_inventory WHERE player_id = ? AND quantity > 0",
            (player_id,)
        ).fetchall()

        conn.close()

        return {
            "id": row["id"],
            "username": row["username"],
            "token": row["token"],
            "starter_dex_id": row["starter_dex_id"],
            "pokeballs": row["pokeballs"],
            "currency": dict(row).get("currency", 500),
            "team": [self._enrich_pokemon_xp(dict(r)) for r in team],
            "total_pokemon": total_pokemon,
            "badges": [r["gym_id"] for r in badges],
            "milestones": [r["milestone"] for r in milestones],
            "inventory": {r["item_type"]: r["quantity"] for r in inventory},
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

    # ─── XP & Leveling ──────────────────────────────────

    def award_xp(self, pokemon_row_id, xp_gained):
        """Add XP to a Pokemon. Returns level-up info dict."""
        conn = self._conn()
        row = conn.execute(
            "SELECT level, xp, dex_id FROM player_pokemon WHERE id = ?",
            (pokemon_row_id,)
        ).fetchone()
        if not row:
            conn.close()
            return None

        old_level = row["level"]
        total_xp = row["xp"] + xp_gained
        new_level = old_level

        # Check for level-ups (can gain multiple levels at once)
        while new_level < 100 and total_xp >= xp_for_level(new_level + 1):
            new_level += 1

        conn.execute(
            "UPDATE player_pokemon SET xp = ?, level = ? WHERE id = ?",
            (total_xp, new_level, pokemon_row_id)
        )
        conn.commit()
        conn.close()

        xp_info = xp_progress_info(new_level, total_xp)
        return {
            "pokemon_id": pokemon_row_id,
            "dex_id": row["dex_id"],
            "old_level": old_level,
            "new_level": new_level,
            "leveled_up": new_level > old_level,
            "xp_gained": xp_gained,
            "total_xp": total_xp,
            "xp_to_next": xp_to_next_level(new_level, total_xp),
            "xp_progress": xp_info["xp_progress"],
            "xp_for_current_level": xp_info["xp_for_current_level"],
            "xp_for_next_level": xp_info["xp_for_next_level"],
        }

    def update_pokemon_moves(self, pokemon_row_id, moves_list):
        """Update the moves list for a specific owned Pokemon."""
        conn = self._conn()
        conn.execute(
            "UPDATE player_pokemon SET moves = ? WHERE id = ?",
            (json.dumps(moves_list), pokemon_row_id)
        )
        conn.commit()
        conn.close()

    def update_pokemon_species(self, pokemon_row_id, new_dex_id):
        """Update Pokemon species (for evolution)."""
        conn = self._conn()
        conn.execute(
            "UPDATE player_pokemon SET dex_id = ? WHERE id = ?",
            (new_dex_id, pokemon_row_id)
        )
        conn.commit()
        conn.close()

    # ─── Currency ─────────────────────────────────────

    def get_currency(self, player_id):
        conn = self._conn()
        row = conn.execute("SELECT currency FROM players WHERE id = ?", (player_id,)).fetchone()
        conn.close()
        return row["currency"] if row else 0

    def add_currency(self, player_id, amount):
        conn = self._conn()
        conn.execute("UPDATE players SET currency = currency + ? WHERE id = ?", (amount, player_id))
        conn.commit()
        conn.close()

    def spend_currency(self, player_id, amount):
        """Spend currency. Returns True if player had enough."""
        conn = self._conn()
        row = conn.execute("SELECT currency FROM players WHERE id = ?", (player_id,)).fetchone()
        if not row or row["currency"] < amount:
            conn.close()
            return False
        conn.execute("UPDATE players SET currency = currency - ? WHERE id = ?", (amount, player_id))
        conn.commit()
        conn.close()
        return True

    # ─── Inventory ─────────────────────────────────────

    def get_inventory(self, player_id):
        """Get all items in player's inventory. Returns dict {item_type: quantity}."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT item_type, quantity FROM player_inventory WHERE player_id = ? AND quantity > 0",
            (player_id,)
        ).fetchall()
        conn.close()
        return {r["item_type"]: r["quantity"] for r in rows}

    def add_item(self, player_id, item_type, count=1):
        """Add items to inventory."""
        conn = self._conn()
        conn.execute(
            """INSERT INTO player_inventory (player_id, item_type, quantity)
               VALUES (?, ?, ?)
               ON CONFLICT(player_id, item_type) DO UPDATE SET quantity = quantity + ?""",
            (player_id, item_type, count, count)
        )
        conn.commit()
        conn.close()

    def use_item(self, player_id, item_type):
        """Use one item from inventory. Returns True if player had one to use."""
        conn = self._conn()
        row = conn.execute(
            "SELECT quantity FROM player_inventory WHERE player_id = ? AND item_type = ?",
            (player_id, item_type)
        ).fetchone()
        if not row or row["quantity"] <= 0:
            conn.close()
            return False
        conn.execute(
            "UPDATE player_inventory SET quantity = quantity - 1 WHERE player_id = ? AND item_type = ?",
            (player_id, item_type)
        )
        conn.commit()
        conn.close()
        return True

    # ─── Badges & Progression ─────────────────────────

    def earn_badge(self, player_id, gym_id):
        """Record badge earned. Returns True on success (False if duplicate)."""
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO player_badges (player_id, gym_id, earned_at) VALUES (?, ?, ?)",
                (player_id, gym_id, int(time.time()))
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            conn.close()
            return False

    def get_badges(self, player_id):
        conn = self._conn()
        rows = conn.execute(
            "SELECT gym_id FROM player_badges WHERE player_id = ? ORDER BY gym_id",
            (player_id,)
        ).fetchall()
        conn.close()
        return [r["gym_id"] for r in rows]

    def record_milestone(self, player_id, milestone):
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO player_progression (player_id, milestone, completed_at) VALUES (?, ?, ?)",
                (player_id, milestone, int(time.time()))
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        conn.close()

    def get_milestones(self, player_id):
        conn = self._conn()
        rows = conn.execute(
            "SELECT milestone FROM player_progression WHERE player_id = ?",
            (player_id,)
        ).fetchall()
        conn.close()
        return [r["milestone"] for r in rows]

    # ─── Trading ─────────────────────────────────────

    def trade_pokemon(self, from_player_id, from_pokemon_id, to_player_id, to_pokemon_id):
        """Swap ownership of two Pokemon between two players.
        Both Pokemon move to storage after trade to avoid team slot issues.
        Returns True on success."""
        conn = self._conn()
        try:
            # Verify ownership
            row_a = conn.execute(
                "SELECT id, player_id FROM player_pokemon WHERE id = ? AND player_id = ?",
                (from_pokemon_id, from_player_id)
            ).fetchone()
            row_b = conn.execute(
                "SELECT id, player_id FROM player_pokemon WHERE id = ? AND player_id = ?",
                (to_pokemon_id, to_player_id)
            ).fetchone()
            if not row_a or not row_b:
                conn.close()
                return False

            # Ensure neither player would be left with 0 team Pokemon
            for pid, traded_id in [(from_player_id, from_pokemon_id), (to_player_id, to_pokemon_id)]:
                team_count = conn.execute(
                    "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ? AND is_in_team = 1",
                    (pid,)
                ).fetchone()["cnt"]
                in_team = conn.execute(
                    "SELECT is_in_team FROM player_pokemon WHERE id = ?",
                    (traded_id,)
                ).fetchone()["is_in_team"]
                if in_team and team_count <= 1:
                    # This is their only team Pokemon; the incoming one will go to storage
                    # so we can't remove their last team member.
                    # Instead, put the incoming Pokemon into team slot 0.
                    pass  # handled below

            # Swap ownership — both go to storage first
            conn.execute(
                "UPDATE player_pokemon SET player_id = ?, is_in_team = 0, team_slot = NULL WHERE id = ?",
                (to_player_id, from_pokemon_id)
            )
            conn.execute(
                "UPDATE player_pokemon SET player_id = ?, is_in_team = 0, team_slot = NULL WHERE id = ?",
                (from_player_id, to_pokemon_id)
            )

            # Compact team slots for both players
            for pid in (from_player_id, to_player_id):
                team_rows = conn.execute(
                    "SELECT id FROM player_pokemon WHERE player_id = ? AND is_in_team = 1 ORDER BY team_slot",
                    (pid,)
                ).fetchall()
                for slot, r in enumerate(team_rows):
                    conn.execute("UPDATE player_pokemon SET team_slot = ? WHERE id = ?", (slot, r["id"]))

                # If player has no team Pokemon left, move the received one to team
                remaining = conn.execute(
                    "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ? AND is_in_team = 1",
                    (pid,)
                ).fetchone()["cnt"]
                if remaining == 0:
                    # Find the Pokemon they just received (which is in storage)
                    received_id = to_pokemon_id if pid == from_player_id else from_pokemon_id
                    conn.execute(
                        "UPDATE player_pokemon SET is_in_team = 1, team_slot = 0 WHERE id = ?",
                        (received_id,)
                    )

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Trade error: {e}")
            conn.close()
            return False

    def get_pokemon_by_id(self, pokemon_row_id, player_id):
        """Get a specific Pokemon row, verified against player_id."""
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM player_pokemon WHERE id = ? AND player_id = ?",
            (pokemon_row_id, player_id)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return self._enrich_pokemon_xp(dict(row))

    def _row_to_dict(self, row):
        return {
            "id": row["id"],
            "username": row["username"],
            "token": row["token"],
            "starter_dex_id": row["starter_dex_id"],
            "pokeballs": row["pokeballs"],
            "currency": dict(row).get("currency", 500),
        }
