import os
import sqlite3
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db_utils


HEADER = b'\x01\x02\x03\x04' * 4  # 16-byte Unreal-ish prefix


def owner_blob(owner_id: int) -> bytes:
    return HEADER + struct.pack('<Q', owner_id)


def make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE characters (id INTEGER PRIMARY KEY, char_name TEXT, playerId INTEGER, guild INTEGER);
        CREATE TABLE guilds (guildId INTEGER PRIMARY KEY, name TEXT, owner INTEGER);
        CREATE TABLE buildings (object_id INTEGER PRIMARY KEY, owner_id INTEGER);
        CREATE TABLE item_inventory (
            item_id INTEGER, owner_id INTEGER, inv_type INTEGER, template_id INTEGER
        );
        CREATE TABLE item_properties (
            item_id INTEGER, owner_id INTEGER, inv_type INTEGER
        );
        CREATE TABLE actor_position (id INTEGER PRIMARY KEY, class TEXT, x REAL, y REAL, z REAL);
        CREATE TABLE properties (object_id INTEGER, name TEXT, value BLOB);
        """
    )
    # Source in clan 50, account playerId=1. Target clanless, playerId=2.
    cur.execute("INSERT INTO characters VALUES (1001, 'Source', 1, 50)")
    cur.execute("INSERT INTO characters VALUES (1002, 'Target', 2, 0)")
    cur.execute("INSERT INTO characters VALUES (1003, 'Bystander', 3, 50)")
    cur.execute("INSERT INTO guilds VALUES (50, 'Wolves', 1001)")
    # Buildings: personal, clan, unowned (0), and a collision with playerId=1
    cur.execute("INSERT INTO buildings VALUES (2001, 1001)")
    cur.execute("INSERT INTO buildings VALUES (2002, 50)")
    cur.execute("INSERT INTO buildings VALUES (2003, 0)")
    cur.execute("INSERT INTO buildings VALUES (2004, 1)")  # must NEVER match playerId
    cur.execute("INSERT INTO buildings VALUES (2005, 1003)")
    # Items: personal, chest contents, playerId collision, unowned
    cur.execute("INSERT INTO item_inventory VALUES (0, 1001, 0, 10001)")
    cur.execute("INSERT INTO item_inventory VALUES (1, 1001, 1, 10002)")
    cur.execute("INSERT INTO item_inventory VALUES (0, 9000, 4, 10003)")  # chest
    cur.execute("INSERT INTO item_inventory VALUES (0, 1, 0, 10004)")     # playerId trap
    cur.execute("INSERT INTO item_inventory VALUES (0, 0, 0, 10005)")
    cur.execute("INSERT INTO item_properties VALUES (0, 1001, 0)")
    cur.execute("INSERT INTO item_properties VALUES (1, 1001, 1)")
    cur.execute("INSERT INTO item_properties VALUES (0, 9000, 4)")
    # Thralls: personal, clan, bystander
    cur.execute(
        "INSERT INTO actor_position VALUES (3001, 'BP_NPC_Cimmerian_C', 1, 2, 3)"
    )
    cur.execute(
        "INSERT INTO actor_position VALUES (3002, 'BP_Pet_Wolf_C', 4, 5, 6)"
    )
    cur.execute(
        "INSERT INTO actor_position VALUES (3003, 'BP_NPC_Other_C', 7, 8, 9)"
    )
    cur.execute(
        "INSERT INTO properties VALUES (3001, 'BP_ThrallComponent_C.OwnerUniqueID', ?)",
        (owner_blob(1001),),
    )
    cur.execute(
        "INSERT INTO properties VALUES (3002, 'BP_ThrallComponent_C.OwnerUniqueID', ?)",
        (owner_blob(50),),
    )
    cur.execute(
        "INSERT INTO properties VALUES (3003, 'BP_ThrallComponent_C.OwnerUniqueID', ?)",
        (owner_blob(1003),),
    )
    # Unrelated owner blob must not be rewritten when transferring thralls
    cur.execute(
        "INSERT INTO properties VALUES (4001, 'BP_SomePlaceable_C.OwnerUniqueID', ?)",
        (owner_blob(1001),),
    )
    conn.commit()
    conn.close()


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'game.db')
        make_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _owners(self, table, col='owner_id'):
        conn = sqlite3.connect(self.db)
        rows = conn.execute(f'SELECT object_id, {col} FROM {table} ORDER BY object_id').fetchall() if table == 'buildings' else None
        if table == 'buildings':
            conn.close()
            return dict(rows)
        if table == 'item_inventory':
            rows = conn.execute(
                'SELECT item_id, owner_id, inv_type, template_id FROM item_inventory ORDER BY owner_id, item_id'
            ).fetchall()
            conn.close()
            return rows
        conn.close()
        return rows

    def _thrall_owner(self, object_id):
        conn = sqlite3.connect(self.db)
        blob = conn.execute(
            'SELECT value FROM properties WHERE object_id = ?', (object_id,)
        ).fetchone()[0]
        conn.close()
        return db_utils.decode_owner_unique_id(blob)

    def test_blob_roundtrip(self):
        raw = owner_blob(1657266)
        self.assertEqual(db_utils.decode_owner_unique_id(raw), 1657266)
        rewritten = db_utils.encode_owner_unique_id(raw, 1002)
        self.assertEqual(rewritten[:-8], HEADER)
        self.assertEqual(db_utils.decode_owner_unique_id(rewritten), 1002)

    def test_personal_items_and_not_chests_or_playerid_or_zero(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['items']
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed['item_inventory'], 2)
        self.assertEqual(changed['item_properties'], 2)
        rows = self._owners('item_inventory')
        # personal moved
        self.assertIn((0, 1002, 0, 10001), rows)
        self.assertIn((1, 1002, 1, 10002), rows)
        # traps untouched
        self.assertIn((0, 9000, 4, 10003), rows)
        self.assertIn((0, 1, 0, 10004), rows)
        self.assertIn((0, 0, 0, 10005), rows)

    def test_personal_buildings_not_clan_or_zero_or_playerid(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['buildings']
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed['buildings'], 1)
        own = self._owners('buildings')
        self.assertEqual(own[2001], 1002)
        self.assertEqual(own[2002], 50)
        self.assertEqual(own[2003], 0)
        self.assertEqual(own[2004], 1)
        self.assertEqual(own[2005], 1003)

    def test_clan_buildings_optional(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['buildings'], include_clan_assets=True
        )
        self.assertTrue(ok, msg)
        own = self._owners('buildings')
        self.assertEqual(own[2001], 1002)
        self.assertEqual(own[2002], 1002)  # clan -> target personal (target has no guild)
        self.assertEqual(own[2003], 0)
        self.assertEqual(own[2005], 1003)

    def test_clan_buildings_to_target_guild(self):
        conn = sqlite3.connect(self.db)
        conn.execute('UPDATE characters SET guild = 77 WHERE id = 1002')
        conn.execute("INSERT INTO guilds VALUES (77, 'Tigers', 1002)")
        conn.commit()
        conn.close()
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['buildings'],
            include_clan_assets=True, clan_assets_to_target_guild=True,
        )
        self.assertTrue(ok, msg)
        own = self._owners('buildings')
        self.assertEqual(own[2001], 1002)  # personal still to character
        self.assertEqual(own[2002], 77)    # clan to target clan

    def test_thralls_personal_only_by_default(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['thralls']
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed['thralls'], 1)
        self.assertEqual(self._thrall_owner(3001), 1002)
        self.assertEqual(self._thrall_owner(3002), 50)
        self.assertEqual(self._thrall_owner(3003), 1003)
        self.assertEqual(self._thrall_owner(4001), 1001)

    def test_all_categories_personal(self):
        ok, changed, msg = db_utils.perform_transfer(self.db, 1001, 1002, ['all'])
        self.assertTrue(ok, msg)
        self.assertEqual(changed['item_inventory'], 2)
        self.assertEqual(changed['buildings'], 1)
        self.assertEqual(changed['thralls'], 1)
        self.assertEqual(self._owners('buildings')[2002], 50)
        self.assertEqual(self._thrall_owner(3002), 50)
        self.assertEqual(self._thrall_owner(4001), 1001)

    def test_buildings_without_properties_table(self):
        conn = sqlite3.connect(self.db)
        conn.execute('DROP TABLE properties')
        conn.commit()
        conn.close()
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['buildings', 'thralls']
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed['buildings'], 1)
        self.assertEqual(changed.get('thralls', 0), 0)

    def test_thralls_include_clan(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['thralls'], include_clan_assets=True
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed['thralls'], 2)
        self.assertEqual(self._thrall_owner(3001), 1002)
        self.assertEqual(self._thrall_owner(3002), 1002)
        self.assertEqual(self._thrall_owner(3003), 1003)

    def test_empty_selection_transfers_nothing(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['items', 'buildings', 'thralls'],
            item_keys=[], building_object_ids=[], thrall_ids=[],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed.get('item_inventory', 0), 0)
        self.assertEqual(changed.get('buildings', 0), 0)
        self.assertEqual(changed.get('thralls', 0), 0)
        self.assertEqual(self._owners('buildings')[2001], 1001)
        self.assertEqual(self._thrall_owner(3001), 1001)

    def test_subset_keys(self):
        ok, changed, msg = db_utils.perform_transfer(
            self.db, 1001, 1002, ['items', 'buildings', 'thralls'],
            item_keys=[(1, 1)], building_object_ids=[2001], thrall_ids=[3001],
        )
        self.assertTrue(ok, msg)
        rows = self._owners('item_inventory')
        self.assertIn((0, 1001, 0, 10001), rows)
        self.assertIn((1, 1002, 1, 10002), rows)
        self.assertEqual(self._owners('buildings')[2001], 1002)
        self.assertEqual(self._thrall_owner(3001), 1002)

    def test_same_character_rejected(self):
        ok, changed, msg = db_utils.perform_transfer(self.db, 1001, 1001, ['all'])
        self.assertFalse(ok)
        self.assertIn('different', msg.lower())

    def test_simulate_counts_ignore_playerid(self):
        counts = db_utils.simulate_update_counts(self.db, 1001, ['all'], include_clan_assets=False)
        self.assertEqual(counts['item_inventory'], 2)
        self.assertEqual(counts['buildings'], 1)
        self.assertEqual(counts['thralls'], 1)
        clan = db_utils.simulate_update_counts(self.db, 1001, ['all'], include_clan_assets=True)
        self.assertEqual(clan['buildings'], 2)
        self.assertEqual(clan['thralls'], 2)

    def test_list_characters_drops_guild_zero(self):
        chars = {c['id']: c for c in db_utils.list_characters(self.db)}
        self.assertEqual(chars[1001]['guild'], 50)
        self.assertEqual(chars[1001]['guild_name'], 'Wolves')
        self.assertIsNone(chars[1002]['guild'])

    def test_revert_from_pre(self):
        pre = db_utils.create_pre_backup(self.db)
        self.assertTrue(os.path.exists(pre))
        db_utils.perform_transfer(self.db, 1001, 1002, ['buildings'])
        self.assertEqual(self._owners('buildings')[2001], 1002)
        ok, msg = db_utils.revert_transfer(self.db)
        self.assertTrue(ok, msg)
        self.assertEqual(self._owners('buildings')[2001], 1001)

    def test_counts_for_owner_does_not_scan_every_table(self):
        c = db_utils.counts_for_owner(self.db, 1001)
        self.assertEqual(c['items'], 2)
        self.assertEqual(c['buildings'], 1)
        self.assertEqual(c['thralls'], 1)

    def test_clan_owned_blobs_counted_with_include_clan(self):
        personal = db_utils.simulate_update_counts(self.db, 1001, ['thralls'], False)
        self.assertEqual(personal['thralls'], 1)
        self.assertEqual(personal['thralls_clan'], 0)
        clan = db_utils.simulate_update_counts(self.db, 1001, ['thralls'], True)
        self.assertEqual(clan['thralls'], 2)
        self.assertEqual(clan['thralls_clan'], 1)
        rows = db_utils.list_thralls_for_owner(self.db, 1001, include_clan_assets=True)
        self.assertEqual({r['follower_id'] for r in rows}, {3001, 3002})

    def test_follower_markers_listed_and_transferred(self):
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE follower_markers (owner_id INTEGER, follower_id INTEGER)')
        conn.execute('INSERT INTO follower_markers VALUES (1001, 3002)')
        conn.execute('INSERT INTO follower_markers VALUES (1001, 3999)')
        conn.commit()
        conn.close()
        counts = db_utils.simulate_update_counts(self.db, 1001, ['thralls'], False)
        self.assertEqual(counts['thralls'], 3)
        self.assertEqual(counts['thralls_following'], 2)
        ids = {r['follower_id'] for r in db_utils.list_thralls_for_owner(self.db, 1001)}
        self.assertEqual(ids, {3001, 3002, 3999})

        ok, changed, msg = db_utils.perform_transfer(self.db, 1001, 1002, ['thralls'])
        self.assertTrue(ok, msg)
        self.assertGreaterEqual(changed.get('thralls_following', 0), 2)
        conn = sqlite3.connect(self.db)
        owners = list(conn.execute(
            'SELECT owner_id, follower_id FROM follower_markers ORDER BY follower_id'
        ))
        conn.close()
        self.assertEqual(owners, [(1002, 3002), (1002, 3999)])
        self.assertEqual(self._thrall_owner(3002), 1002)


if __name__ == '__main__':
    unittest.main()
