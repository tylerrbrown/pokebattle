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

            CREATE TABLE IF NOT EXISTS player_pokedex (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                dex_id INTEGER NOT NULL,
                seen INTEGER DEFAULT 0,
                caught INTEGER DEFAULT 0,
                first_seen_at INTEGER,
                first_caught_at INTEGER,
                FOREIGN KEY (player_id) REFERENCES players(id),
                UNIQUE(player_id, dex_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pokedex_player ON player_pokedex(player_id);
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
        if "pin" not in cols:
            conn.execute("ALTER TABLE players ADD COLUMN pin TEXT DEFAULT NULL")
        if "encounters_since_legendary" not in cols:
            conn.execute("ALTER TABLE players ADD COLUMN encounters_since_legendary INTEGER DEFAULT 0")
        if "current_region" not in cols:
            conn.execute("ALTER TABLE players ADD COLUMN current_region TEXT DEFAULT 'kanto'")
        # Check existing columns in player_pokemon
        cols = {row[1] for row in conn.execute("PRAGMA table_info(player_pokemon)").fetchall()}
        if "moves" not in cols:
            conn.execute("ALTER TABLE player_pokemon ADD COLUMN moves TEXT")
        if "is_shiny" not in cols:
            conn.execute("ALTER TABLE player_pokemon ADD COLUMN is_shiny INTEGER DEFAULT 0")
        # Add region column to player_badges
        badge_cols = {row[1] for row in conn.execute("PRAGMA table_info(player_badges)").fetchall()}
        if "region" not in badge_cols:
            conn.execute("ALTER TABLE player_badges ADD COLUMN region TEXT DEFAULT 'kanto'")
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_badge_region ON player_badges(player_id, gym_id, region)")
            except Exception:
                pass

    def register(self, username, pin=None):
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
                "INSERT INTO players (username, token, pin, created_at) VALUES (?, ?, ?, ?)",
                (username, token, pin, int(time.time()))
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

    def choose_starter(self, player_id, dex_id, default_moves=None):
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
        moves_json = json.dumps(default_moves) if default_moves else None
        # Add starter to collection as team slot 0
        conn.execute(
            """INSERT INTO player_pokemon
               (player_id, dex_id, level, xp, moves, is_in_team, team_slot, caught_at)
               VALUES (?, ?, 5, 0, ?, 1, 0, ?)""",
            (player_id, dex_id, moves_json, int(time.time()))
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
            "SELECT gym_id, region FROM player_badges WHERE player_id = ? ORDER BY gym_id",
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

        pokedex_seen = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokedex WHERE player_id = ? AND seen = 1",
            (player_id,)
        ).fetchone()["cnt"]
        pokedex_caught = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokedex WHERE player_id = ? AND caught = 1",
            (player_id,)
        ).fetchone()["cnt"]

        conn.close()

        # Build badges_by_region dict
        badges_by_region = {}
        for b in badges:
            region = b["region"] or "kanto"
            badges_by_region.setdefault(region, []).append(b["gym_id"])

        current_region = dict(row).get("current_region", "kanto")

        return {
            "id": row["id"],
            "username": row["username"],
            "token": row["token"],
            "starter_dex_id": row["starter_dex_id"],
            "pokeballs": row["pokeballs"],
            "currency": dict(row).get("currency", 500),
            "current_region": current_region,
            "team": [self._enrich_pokemon_xp(dict(r)) for r in team],
            "total_pokemon": total_pokemon,
            "pokedex_seen": pokedex_seen,
            "pokedex_caught": pokedex_caught,
            "badges": badges_by_region.get(current_region, []),
            "badges_by_region": badges_by_region,
            "milestones": [r["milestone"] for r in milestones],
            "inventory": {r["item_type"]: r["quantity"] for r in inventory},
        }

    def get_bug_report_context(self, player_id):
        """Gather player context for a bug report snapshot."""
        conn = self._conn()
        player = conn.execute(
            "SELECT username, created_at, currency, pokeballs FROM players WHERE id = ?",
            (player_id,)
        ).fetchone()
        if not player:
            conn.close()
            return None
        team = conn.execute(
            "SELECT dex_id, level FROM player_pokemon WHERE player_id = ? AND is_in_team = 1 ORDER BY team_slot",
            (player_id,)
        ).fetchall()
        badges = conn.execute(
            "SELECT gym_id FROM player_badges WHERE player_id = ? ORDER BY gym_id",
            (player_id,)
        ).fetchall()
        milestones = conn.execute(
            "SELECT milestone FROM player_progression WHERE player_id = ?",
            (player_id,)
        ).fetchall()
        total_pokemon = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ?",
            (player_id,)
        ).fetchone()["cnt"]
        conn.close()
        return {
            "username": player["username"],
            "account_created_at": player["created_at"],
            "currency": dict(player).get("currency", 500),
            "team": [{"dex_id": r["dex_id"], "level": r["level"]} for r in team],
            "badges": [r["gym_id"] for r in badges],
            "milestones": [r["milestone"] for r in milestones],
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

    def catch_pokemon(self, player_id, dex_id, level, default_moves=None, is_shiny=False):
        """Add a caught Pokemon to player's collection."""
        conn = self._conn()
        # Check how many are in team
        team_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokemon WHERE player_id = ? AND is_in_team = 1",
            (player_id,)
        ).fetchone()["cnt"]

        in_team = 1 if team_count < 6 else 0
        team_slot = team_count if team_count < 6 else None
        moves_json = json.dumps(default_moves) if default_moves else None

        conn.execute(
            """INSERT INTO player_pokemon
               (player_id, dex_id, level, xp, moves, is_in_team, team_slot, caught_at, is_shiny)
               VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (player_id, dex_id, level, moves_json, in_team, team_slot, int(time.time()), int(is_shiny))
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

    # ─── Legendary Pity Counter ─────────────────────────

    def get_encounter_counter(self, player_id):
        """Returns current encounters_since_legendary counter value."""
        conn = self._conn()
        row = conn.execute(
            "SELECT encounters_since_legendary FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        conn.close()
        return row["encounters_since_legendary"] if row else 0

    def increment_encounter_counter(self, player_id):
        """Increment encounters_since_legendary by 1. Returns new value."""
        conn = self._conn()
        conn.execute(
            "UPDATE players SET encounters_since_legendary = encounters_since_legendary + 1 WHERE id = ?",
            (player_id,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT encounters_since_legendary FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        conn.close()
        return row["encounters_since_legendary"] if row else 0

    def reset_encounter_counter(self, player_id):
        """Reset encounters_since_legendary to 0."""
        conn = self._conn()
        conn.execute(
            "UPDATE players SET encounters_since_legendary = 0 WHERE id = ?",
            (player_id,)
        )
        conn.commit()
        conn.close()

    # ─── Badges & Progression ─────────────────────────

    def earn_badge(self, player_id, gym_id, region="kanto"):
        """Record badge earned for a region. Returns True on success (False if duplicate)."""
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO player_badges (player_id, gym_id, region, earned_at) VALUES (?, ?, ?, ?)",
                (player_id, gym_id, region, int(time.time()))
            )
            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            conn.close()
            return False

    def get_badges(self, player_id, region=None):
        """Get badges. If region is specified, filter by region."""
        conn = self._conn()
        if region:
            rows = conn.execute(
                "SELECT gym_id FROM player_badges WHERE player_id = ? AND region = ? ORDER BY gym_id",
                (player_id, region)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT gym_id FROM player_badges WHERE player_id = ? ORDER BY gym_id",
                (player_id,)
            ).fetchall()
        conn.close()
        return [r["gym_id"] for r in rows]

    def get_badges_by_region(self, player_id):
        """Get all badges grouped by region. Returns {region: [gym_ids]}."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT gym_id, region FROM player_badges WHERE player_id = ? ORDER BY region, gym_id",
            (player_id,)
        ).fetchall()
        conn.close()
        result = {}
        for r in rows:
            region = r["region"] or "kanto"
            result.setdefault(region, []).append(r["gym_id"])
        return result

    # ─── Region ──────────────────────────────────────

    def get_current_region(self, player_id):
        """Get the player's current region."""
        conn = self._conn()
        row = conn.execute(
            "SELECT current_region FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        conn.close()
        return row["current_region"] if row else "kanto"

    def set_current_region(self, player_id, region_id):
        """Update the player's current region."""
        conn = self._conn()
        conn.execute(
            "UPDATE players SET current_region = ? WHERE id = ?",
            (region_id, player_id)
        )
        conn.commit()
        conn.close()

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

    # ─── Pokedex ─────────────────────────────────────

    def mark_seen(self, player_id, dex_id):
        """Mark a Pokemon as seen in the Pokedex. UPSERT — sets seen=1."""
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            """INSERT INTO player_pokedex (player_id, dex_id, seen, first_seen_at)
               VALUES (?, ?, 1, ?)
               ON CONFLICT(player_id, dex_id) DO UPDATE SET seen = 1""",
            (player_id, dex_id, now)
        )
        conn.commit()
        conn.close()

    def mark_seen_batch(self, player_id, dex_ids):
        """Mark multiple Pokemon as seen in a single transaction."""
        if not dex_ids:
            return
        conn = self._conn()
        now = int(time.time())
        for dex_id in dex_ids:
            conn.execute(
                """INSERT INTO player_pokedex (player_id, dex_id, seen, first_seen_at)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(player_id, dex_id) DO UPDATE SET seen = 1""",
                (player_id, dex_id, now)
            )
        conn.commit()
        conn.close()

    def mark_caught(self, player_id, dex_id):
        """Mark a Pokemon as caught (and seen) in the Pokedex. UPSERT."""
        conn = self._conn()
        now = int(time.time())
        conn.execute(
            """INSERT INTO player_pokedex (player_id, dex_id, seen, caught, first_seen_at, first_caught_at)
               VALUES (?, ?, 1, 1, ?, ?)
               ON CONFLICT(player_id, dex_id) DO UPDATE SET seen = 1, caught = 1,
               first_caught_at = COALESCE(first_caught_at, ?)""",
            (player_id, dex_id, now, now, now)
        )
        conn.commit()
        conn.close()

    def get_pokedex(self, player_id):
        """Get player's Pokedex data. Returns dict {dex_id: {"seen": bool, "caught": bool}}."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT dex_id, seen, caught FROM player_pokedex WHERE player_id = ?",
            (player_id,)
        ).fetchall()
        conn.close()
        return {r["dex_id"]: {"seen": bool(r["seen"]), "caught": bool(r["caught"])} for r in rows}

    def get_pokedex_counts(self, player_id):
        """Get Pokedex summary counts."""
        conn = self._conn()
        seen = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokedex WHERE player_id = ? AND seen = 1",
            (player_id,)
        ).fetchone()["cnt"]
        caught = conn.execute(
            "SELECT COUNT(*) as cnt FROM player_pokedex WHERE player_id = ? AND caught = 1",
            (player_id,)
        ).fetchone()["cnt"]
        conn.close()
        return {"seen": seen, "caught": caught}

    def backfill_pokedex(self, player_id):
        """Backfill Pokedex for existing players: mark all owned Pokemon as caught."""
        conn = self._conn()
        # Get distinct dex_ids the player owns
        rows = conn.execute(
            "SELECT DISTINCT dex_id FROM player_pokemon WHERE player_id = ?",
            (player_id,)
        ).fetchall()
        now = int(time.time())
        for r in rows:
            conn.execute(
                """INSERT INTO player_pokedex (player_id, dex_id, seen, caught, first_seen_at, first_caught_at)
                   VALUES (?, ?, 1, 1, ?, ?)
                   ON CONFLICT(player_id, dex_id) DO UPDATE SET seen = 1, caught = 1,
                   first_caught_at = COALESCE(first_caught_at, ?)""",
                (player_id, r["dex_id"], now, now, now)
            )
        conn.commit()
        conn.close()
        return len(rows)

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

    # ─── Move Migrations ─────────────────────────────────

    def fix_null_moves(self, pokemon_data_module):
        """Fix Pokemon with NULL moves column by initializing from learnset + defaults."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, dex_id, level FROM player_pokemon WHERE moves IS NULL"
        ).fetchall()
        count = 0
        for row in rows:
            moves = pokemon_data_module.get_initial_moves(row["dex_id"], row["level"])
            if moves:
                conn.execute(
                    "UPDATE player_pokemon SET moves = ? WHERE id = ?",
                    (json.dumps(moves), row["id"])
                )
                count += 1
        conn.commit()
        conn.close()
        print(f"[migration] Fixed {count} Pokemon with NULL moves")

    def fix_sparse_moves(self, pokemon_data_module):
        """Supplement Pokemon with fewer than 4 moves from learnset + defaults."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, dex_id, level, moves FROM player_pokemon WHERE moves IS NOT NULL"
        ).fetchall()
        count = 0
        for row in rows:
            current = json.loads(row["moves"])
            if len(current) >= 4:
                continue
            full = pokemon_data_module.get_initial_moves(row["dex_id"], row["level"])
            for mid in full:
                if mid not in current:
                    current.append(mid)
                    if len(current) >= 4:
                        break
            if len(current) > len(json.loads(row["moves"])):
                conn.execute(
                    "UPDATE player_pokemon SET moves = ? WHERE id = ?",
                    (json.dumps(current), row["id"])
                )
                count += 1
        conn.commit()
        conn.close()
        print(f"[migration] Supplemented {count} Pokemon with sparse moves")

    def _row_to_dict(self, row):
        d = dict(row)
        return {
            "id": d["id"],
            "username": d["username"],
            "token": d["token"],
            "starter_dex_id": d["starter_dex_id"],
            "pokeballs": d["pokeballs"],
            "currency": d.get("currency", 500),
            "pin": d.get("pin"),
            "has_pin": d.get("pin") is not None,
        }

    def set_pin(self, player_id, pin):
        """Set the 4-digit PIN for a player account."""
        conn = self._conn()
        conn.execute("UPDATE players SET pin = ? WHERE id = ?", (pin, player_id))
        conn.commit()
        conn.close()
