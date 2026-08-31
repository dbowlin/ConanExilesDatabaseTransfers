import os
import sqlite3
import struct
import sys
import tempfile
import unittest
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db_utils


HEADER = b'\x01\x02\x03\x04' * 4


def owner_blob(owner_id: int) -> bytes:
    return HEADER + struct.pack('<Q', owner_id)


PERSON_A = 'AAAAAAAAAAAAAAAA'
PERSON_B = 'BBBBBBBBBBBBBBBB'


def make_handoff_db(path: str, *, with_throwaway: bool = False) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE account (id INTEGER PRIMARY KEY, user TEXT);
        CREATE TABLE characters (id INTEGER PRIMARY KEY, char_name TEXT, playerId INTEGER, guild INTEGER);
        CREATE TABLE guilds (guildId INTEGER PRIMARY KEY, name TEXT, owner INTEGER);
        CREATE TABLE buildings (object_id INTEGER PRIMARY KEY, owner_id INTEGER);
        CREATE TABLE item_inventory (
            item_id INTEGER, owner_id INTEGER, inv_type INTEGER, template_id INTEGER
        );
        CREATE TABLE item_properties (item_id INTEGER, owner_id INTEGER, inv_type INTEGER);
        CREATE TABLE actor_position (id INTEGER PRIMARY KEY, class TEXT, x REAL, y REAL, z REAL);
        CREATE TABLE properties (object_id INTEGER, name TEXT, value BLOB);
        """
    )
    cur.execute('INSERT INTO account VALUES (1, ?)', (PERSON_A,))
    cur.execute("INSERT INTO characters VALUES (1001, 'Hero', 1, 0)")
    cur.execute("INSERT INTO guilds VALUES (50, 'SoloClan', 1001)")
    cur.execute('UPDATE characters SET guild = 50 WHERE id = 1001')
    cur.execute('INSERT INTO buildings VALUES (2001, 1001)')
    cur.execute('INSERT INTO buildings VALUES (2002, 50)')
    cur.execute('INSERT INTO item_inventory VALUES (0, 1001, 0, 10001)')
    cur.execute('INSERT INTO item_properties VALUES (0, 1001, 0)')
    cur.execute(
        "INSERT INTO actor_position VALUES (3001, 'BP_NPC_Cimmerian_C', 1, 2, 3)"
    )
    cur.execute(
        "INSERT INTO properties VALUES (3001, 'BP_ThrallComponent_C.OwnerUniqueID', ?)",
        (owner_blob(1001),),
    )
    if with_throwaway:
        cur.execute('INSERT INTO account VALUES (2, ?)', (PERSON_B,))
        cur.execute("INSERT INTO characters VALUES (2001, 'Throwaway', 2, 0)")
    conn.commit()
    conn.close()


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, 'game.db')
        make_handoff_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _account_user(self, account_id: int) -> str:
        conn = sqlite3.connect(self.db)
        row = conn.execute('SELECT user FROM account WHERE id = ?', (account_id,)).fetchone()
        conn.close()
        return row[0]

    def _player_id(self, char_id: int, db_path: Optional[str] = None) -> int:
        db = db_path or self.db
        conn = sqlite3.connect(db)
        row = conn.execute('SELECT playerId FROM characters WHERE id = ?', (char_id,)).fetchone()
        conn.close()
        return row[0]

    def _building_owner(self, object_id: int) -> int:
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            'SELECT owner_id FROM buildings WHERE object_id = ?', (object_id,)
        ).fetchone()[0]
        conn.close()
        return row

    def test_parse_game_ini_master_account_id(self):
        ini = os.path.join(self.tmp.name, 'Game.ini')
        with open(ini, 'w', encoding='utf-8') as f:
            f.write('[FuncomLiveServices]\n')
            f.write(
                'CachedUsers=(MasterAccountId="1234567890ABCDEF",'
                'TitlePlayerId="02468ACE13579BDF")\n'
            )
        self.assertEqual(
            db_utils.parse_game_ini_master_account_id(ini),
            '1234567890ABCDEF',
        )
        self.assertIsNone(db_utils.parse_game_ini_master_account_id('/no/such/file.ini'))

    def test_list_accounts(self):
        accounts = db_utils.list_accounts(self.db)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]['id'], 1)
        self.assertEqual(accounts[0]['user'], PERSON_A)

    def test_simulate_rebind_solo(self):
        sim = db_utils.simulate_save_handoff(self.db, 1001, PERSON_B)
        self.assertFalse(sim['errors'])
        self.assertTrue(sim['will_rebind_account_user'])
        self.assertFalse(sim['will_repoint_player_id'])
        self.assertEqual(sim['source_account_user_before'], PERSON_A)
        self.assertEqual(sim['target_account_user_after'], PERSON_B)
        self.assertEqual(sim['asset_counts']['buildings'], 2)

    def test_rebind_preserves_assets(self):
        before = db_utils.counts_for_owner(self.db, 1001, include_clan_assets=True)
        ok, changed, msg = db_utils.perform_save_handoff(self.db, 1001, PERSON_B)
        self.assertTrue(ok, msg)
        self.assertEqual(changed['account_user_updated'], 1)
        self.assertEqual(self._account_user(1), PERSON_B)
        self.assertEqual(self._player_id(1001), 1)
        after = db_utils.counts_for_owner(self.db, 1001, include_clan_assets=True)
        self.assertEqual(before, after)
        self.assertEqual(self._building_owner(2001), 1001)
        conn = sqlite3.connect(self.db)
        blob = conn.execute(
            'SELECT value FROM properties WHERE object_id = 3001'
        ).fetchone()[0]
        conn.close()
        self.assertEqual(db_utils.decode_owner_unique_id(blob), 1001)

    def test_dry_run_leaves_db_unchanged(self):
        ok, _, msg = db_utils.perform_save_handoff(
            self.db, 1001, PERSON_B, dry_run=True,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self._account_user(1), PERSON_A)

    def test_bootstrap_repoints_player_id(self):
        make_handoff_db(os.path.join(self.tmp.name, 'boot.db'), with_throwaway=True)
        boot = os.path.join(self.tmp.name, 'boot.db')
        sim = db_utils.simulate_save_handoff(boot, 1001, PERSON_B)
        self.assertFalse(sim['errors'])
        self.assertTrue(sim['will_repoint_player_id'])
        self.assertIn(2001, sim['characters_to_remove'])

        ok, changed, msg = db_utils.perform_save_handoff(boot, 1001, PERSON_B)
        self.assertTrue(ok, msg)
        self.assertEqual(changed['player_id_repointed'], 1)
        self.assertEqual(changed['characters_removed'], 1)
        self.assertEqual(self._player_id(1001, boot), 2)
        conn = sqlite3.connect(boot)
        chars = conn.execute('SELECT id FROM characters').fetchall()
        accounts = conn.execute('SELECT id, user FROM account ORDER BY id').fetchall()
        conn.close()
        self.assertEqual(chars, [(1001,)])
        self.assertEqual(accounts, [(2, PERSON_B)])

    def test_remove_throwaway_explicit(self):
        boot = os.path.join(self.tmp.name, 'explicit.db')
        make_handoff_db(boot, with_throwaway=True)

        ok, changed, msg = db_utils.perform_save_handoff(
            boot, 1001, PERSON_B, remove_character_ids=[2001],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed['characters_removed'], 1)
        conn = sqlite3.connect(boot)
        self.assertIsNone(conn.execute(
            'SELECT 1 FROM characters WHERE id = 2001'
        ).fetchone())
        conn.close()

    def test_missing_target_user_rejected(self):
        ok, _, msg = db_utils.perform_save_handoff(self.db, 1001, '')
        self.assertFalse(ok)
        self.assertIn('required', msg.lower())

    def test_missing_character_rejected(self):
        ok, _, msg = db_utils.perform_save_handoff(self.db, 9999, PERSON_B)
        self.assertFalse(ok)

    def test_no_account_table_rejected(self):
        conn = sqlite3.connect(self.db)
        conn.execute('DROP TABLE account')
        conn.commit()
        conn.close()
        ok, _, msg = db_utils.perform_save_handoff(self.db, 1001, PERSON_B)
        self.assertFalse(ok)
        self.assertIn('account', msg.lower())

    def test_rebind_idempotent(self):
        db_utils.perform_save_handoff(self.db, 1001, PERSON_B)
        ok, changed, msg = db_utils.perform_save_handoff(self.db, 1001, PERSON_B)
        self.assertTrue(ok, msg)
        self.assertEqual(changed.get('account_user_updated', 0), 0)
        self.assertEqual(changed.get('player_id_repointed', 0), 0)

    def test_multi_db_handoff(self):
        siptah = os.path.join(self.tmp.name, 'dlc_siptah.db')
        make_handoff_db(siptah)
        ok, result, msg = db_utils.perform_save_handoff_multi(
            [self.db, siptah], 1001, PERSON_B,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self._account_user(1), PERSON_B)
        conn = sqlite3.connect(siptah)
        user = conn.execute('SELECT user FROM account WHERE id = 1').fetchone()[0]
        conn.close()
        self.assertEqual(user, PERSON_B)

    def test_multi_db_dry_run(self):
        siptah = os.path.join(self.tmp.name, 'dlc_siptah.db')
        make_handoff_db(siptah)
        ok, result, msg = db_utils.perform_save_handoff_multi(
            [self.db, siptah], 1001, PERSON_B, dry_run=True,
        )
        self.assertTrue(ok, msg)
        self.assertEqual(self._account_user(1), PERSON_A)

    def test_revert_after_handoff(self):
        pre = db_utils.create_pre_backup(self.db)
        db_utils.perform_save_handoff(self.db, 1001, PERSON_B)
        self.assertEqual(self._account_user(1), PERSON_B)
        ok, msg = db_utils.revert_transfer(self.db, pre)
        self.assertTrue(ok, msg)
        self.assertEqual(self._account_user(1), PERSON_A)

    def test_same_user_no_op(self):
        ok, changed, msg = db_utils.perform_save_handoff(self.db, 1001, PERSON_A)
        self.assertTrue(ok, msg)
        self.assertEqual(changed.get('account_user_updated', 0), 0)


    def test_bootstrap_keeps_throwaway_when_disabled(self):
        boot = os.path.join(self.tmp.name, 'keep.db')
        make_handoff_db(boot, with_throwaway=True)
        ok, changed, msg = db_utils.perform_save_handoff(
            boot, 1001, PERSON_B, remove_character_ids=[],
        )
        self.assertTrue(ok, msg)
        self.assertEqual(changed.get('characters_removed', 0), 0)
        self.assertEqual(self._player_id(1001, boot), 2)
        conn = sqlite3.connect(boot)
        self.assertIsNotNone(conn.execute(
            'SELECT 1 FROM characters WHERE id = 2001'
        ).fetchone())
        conn.close()


if __name__ == '__main__':
    unittest.main()
