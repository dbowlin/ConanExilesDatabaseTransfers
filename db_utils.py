"""Conan Exiles game.db ownership transfer.

Funcom stores ownership in a few specific ways — not as a generic owner_id scan:

* characters.id          personal owner of inventory, solo buildings, and followers
* guilds.guildId         clan owner of buildings and followers (never 0)
* item_inventory.owner_id is the *container*: character id for carried loot,
                         chest/station/thrall object_id for structure inventories
* buildings.owner_id     character id or guildId
* properties.value       BP_ThrallComponent_C.OwnerUniqueID — last 8 bytes are
                         little-endian uint64 character or guild id

characters.playerId is an account-table FK, not an owner id. Matching it (or
guild=0) will corrupt unrelated rows.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _runtime_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


LOG_PATH = os.path.join(_runtime_dir(), 'transfer.log')

SQLITE_MAX_VARS = 400
OWNER_UNIQUE_ID_WIDTH = 8  # little-endian uint64 at end of properties blob

# Display-only. Actual item transfer keys off character id as container owner,
# which is the correct rule for carried inventory.
INV_TYPE_LABELS = {
    0: 'inventory',
    1: 'equipped',
    2: 'hotbar',
    3: 'recipes',
    4: 'container',
    5: 'unknown',
    6: 'player-6',
    7: 'player-7',
    8: 'player-8',
}


def _log(msg: str) -> None:
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")


def _connect(db_path: str, write: bool = False) -> sqlite3.Connection:
    timeout = 30
    if write:
        conn = sqlite3.connect(db_path, timeout=timeout)
    else:
        uri = f'file:{os.path.abspath(db_path)}?mode=ro'
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        except sqlite3.OperationalError:
            conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _chunks(seq: Sequence, size: int = SQLITE_MAX_VARS):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _as_bytes(value: Any) -> bytes:
    if value is None:
        return b''
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode('latin-1')
    return bytes(value)


def decode_owner_unique_id(blob: Any) -> Optional[int]:
    raw = _as_bytes(blob)
    if len(raw) < OWNER_UNIQUE_ID_WIDTH:
        return None
    return struct.unpack_from('<Q', raw, len(raw) - OWNER_UNIQUE_ID_WIDTH)[0]


def encode_owner_unique_id(blob: Any, new_id: int) -> bytes:
    raw = _as_bytes(blob)
    if len(raw) < OWNER_UNIQUE_ID_WIDTH:
        raise ValueError('OwnerUniqueID blob is shorter than 8 bytes')
    return raw[:-OWNER_UNIQUE_ID_WIDTH] + struct.pack('<Q', int(new_id) & 0xFFFFFFFFFFFFFFFF)


def valid_owner_id(value: Any) -> Optional[int]:
    """Reject NULL/0 — Funcom uses 0 for unowned / world objects."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def sqlite_copy(src_path: str, dst_path: str) -> None:
    """Copy a SQLite DB including WAL contents via the backup API."""
    dst_dir = os.path.dirname(os.path.abspath(dst_path))
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    src = sqlite3.connect(f'file:{os.path.abspath(src_path)}?mode=ro', uri=True, timeout=30)
    try:
        dst = sqlite3.connect(dst_path, timeout=30)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    except sqlite3.OperationalError:
        src.close()
        import shutil
        shutil.copy2(src_path, dst_path)
        return
    finally:
        try:
            src.close()
        except Exception:
            pass
    _log(f'Copied DB from {src_path} to {dst_path}')


def copy_db(src: str, dst: str) -> None:
    sqlite_copy(src, dst)


def wal_sidecars(db_path: str) -> List[str]:
    found = []
    for suffix in ('-wal', '-shm'):
        p = db_path + suffix
        if os.path.exists(p):
            found.append(p)
    return found


def db_appears_in_use(db_path: str) -> bool:
    """True if WAL/SHM exist or an exclusive lock cannot be taken."""
    if wal_sidecars(db_path):
        return True
    try:
        conn = sqlite3.connect(db_path, timeout=1)
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return True
    return False


def _guild_id_column(conn: sqlite3.Connection) -> Optional[str]:
    if not _table_exists(conn, 'guilds'):
        return None
    cols = _columns(conn, 'guilds')
    for name in ('guildId', 'guild_id', 'id'):
        if name in cols:
            return name
    return None


def _guild_name_column(conn: sqlite3.Connection) -> Optional[str]:
    if not _table_exists(conn, 'guilds'):
        return None
    cols = _columns(conn, 'guilds')
    for name in ('name', 'guild_name', 'guildName'):
        if name in cols:
            return name
    return None


def fetch_character(conn: sqlite3.Connection, char_id: int) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, 'characters'):
        return None
    cols = _columns(conn, 'characters')
    select_cols = ['id']
    for c in ('char_name', 'playerId', 'guild'):
        if c in cols:
            select_cols.append(c)
    quoted = ', '.join(select_cols)
    row = conn.execute(f'SELECT {quoted} FROM characters WHERE id = ?', (char_id,)).fetchone()
    if not row:
        return None
    data = {k: row[k] for k in row.keys()}
    data['id'] = int(data['id'])
    data['char_name'] = data.get('char_name') or str(data['id'])
    data['guild'] = valid_owner_id(data.get('guild'))
    data['playerId'] = data.get('playerId')
    data['guild_name'] = None
    gid_col = _guild_id_column(conn)
    name_col = _guild_name_column(conn)
    if data['guild'] and gid_col and name_col:
        grow = conn.execute(
            f'SELECT "{name_col}" AS n FROM guilds WHERE "{gid_col}" = ?',
            (data['guild'],),
        ).fetchone()
        if grow and grow['n']:
            data['guild_name'] = str(grow['n'])
    return data


def list_characters(db_path: str) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if not _table_exists(conn, 'characters'):
            return []
        cols = _columns(conn, 'characters')
        select_cols = ['id']
        for c in ('char_name', 'playerId', 'guild'):
            if c in cols:
                select_cols.append(c)
        order = 'char_name COLLATE NOCASE' if 'char_name' in cols else 'id'
        rows = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM characters ORDER BY {order}"
        ).fetchall()
        out = []
        gid_col = _guild_id_column(conn)
        name_col = _guild_name_column(conn)
        guild_names: Dict[int, str] = {}
        if gid_col and name_col:
            for g in conn.execute(f'SELECT "{gid_col}" AS gid, "{name_col}" AS n FROM guilds'):
                vg = valid_owner_id(g['gid'])
                if vg and g['n']:
                    guild_names[vg] = str(g['n'])
        for r in rows:
            cid = valid_owner_id(r['id'])
            if cid is None:
                continue
            guild = valid_owner_id(r['guild'] if 'guild' in r.keys() else None)
            out.append({
                'id': cid,
                'char_name': r['char_name'] if 'char_name' in r.keys() else str(cid),
                'playerId': r['playerId'] if 'playerId' in r.keys() else None,
                'guild': guild,
                'guild_name': guild_names.get(guild) if guild else None,
            })
        return out
    finally:
        conn.close()


def schema_report(db_path: str) -> Dict[str, Any]:
    conn = _connect(db_path)
    try:
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        expected = ['characters', 'buildings', 'item_inventory', 'item_properties',
                    'properties', 'actor_position', 'guilds']
        return {
            'tables': tables,
            'missing_expected': [t for t in expected if t not in tables],
            'has_properties': 'properties' in tables,
            'has_buildings': 'buildings' in tables,
            'has_item_inventory': 'item_inventory' in tables,
        }
    finally:
        conn.close()


def source_owner_ids(char: Dict[str, Any], include_clan: bool) -> List[int]:
    ids = [char['id']]
    if include_clan and char.get('guild'):
        ids.append(int(char['guild']))
    return ids


def _is_thrall_owner_property(name: Optional[str]) -> bool:
    if not name:
        return False
    lname = name.lower()
    compact = lname.replace('_', '')
    if 'owneruniqueid' not in compact:
        return False
    return any(key in lname for key in ('thrall', 'pet', 'follower'))


def _load_thrall_owner_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    if not _table_exists(conn, 'properties'):
        return []
    cols = _columns(conn, 'properties')
    if 'object_id' not in cols or 'name' not in cols or 'value' not in cols:
        return []
    return conn.execute(
        "SELECT object_id, name, value FROM properties WHERE name LIKE '%OwnerUniqueID%'"
    ).fetchall()


def _hydrate_actor(conn: sqlite3.Connection, oid: int, owner: Optional[int], property_name: str = '') -> Dict[str, Any]:
    rec = {
        'follower_id': oid,
        'object_id': oid,
        'owner_id': owner,
        'class': '',
        'coords': (None, None, None),
        'template_id': '',
        'template_name': '',
        'property_name': property_name,
        'owned_by_guild': False,
        'kind': 'placed',
    }
    if _table_exists(conn, 'actor_position'):
        ap = conn.execute('SELECT * FROM actor_position WHERE id = ?', (oid,)).fetchone()
        if ap:
            rec['class'] = str(ap['class']) if 'class' in ap.keys() and ap['class'] else ''
            rec['coords'] = (
                ap['x'] if 'x' in ap.keys() else None,
                ap['y'] if 'y' in ap.keys() else None,
                ap['z'] if 'z' in ap.keys() else None,
            )
    rec['template_name'] = rec['class'] or str(oid)
    rec['template_id'] = rec['class']
    return rec


def iter_owned_thralls(conn: sqlite3.Connection, owner_ids: Sequence[int]) -> List[Dict[str, Any]]:
    """Followers whose OwnerUniqueID blob decodes to one of owner_ids.

    On clan servers Funcom writes the guildId into that blob, not the character id.
    """
    wanted = {int(x) for x in owner_ids if valid_owner_id(x)}
    if not wanted:
        return []
    out = []
    primary = int(owner_ids[0]) if owner_ids else None
    for row in _load_thrall_owner_rows(conn):
        if not _is_thrall_owner_property(row['name']):
            continue
        owner = decode_owner_unique_id(row['value'])
        if owner not in wanted:
            continue
        rec = _hydrate_actor(conn, int(row['object_id']), owner, row['name'])
        rec['owned_by_guild'] = bool(primary is not None and owner != primary)
        rec['kind'] = 'clan' if rec['owned_by_guild'] else 'placed'
        out.append(rec)
    return out


def _followers_from_markers(conn: sqlite3.Connection, char_id: int) -> List[Dict[str, Any]]:
    """Companions on this character's follower wheel (follower_markers)."""
    if not _table_exists(conn, 'follower_markers'):
        return []
    cols = _columns(conn, 'follower_markers')
    if 'owner_id' not in cols or 'follower_id' not in cols:
        return []
    prop_by_oid = {}
    for row in _load_thrall_owner_rows(conn):
        if _is_thrall_owner_property(row['name']):
            prop_by_oid[int(row['object_id'])] = row
    out = []
    for row in conn.execute(
        'SELECT follower_id FROM follower_markers WHERE owner_id = ?', (char_id,)
    ):
        oid = int(row['follower_id'])
        prop = prop_by_oid.get(oid)
        owner = decode_owner_unique_id(prop['value']) if prop else None
        rec = _hydrate_actor(conn, oid, owner, prop['name'] if prop else '')
        rec['kind'] = 'following'
        out.append(rec)
    return out


def collect_thralls(
    conn: sqlite3.Connection,
    char: Dict[str, Any],
    include_clan: bool,
) -> List[Dict[str, Any]]:
    """Union of placed OwnerUniqueID matches and this character's follower_markers."""
    by_id: Dict[int, Dict[str, Any]] = {}
    guild = char.get('guild')
    for rec in iter_owned_thralls(conn, source_owner_ids(char, include_clan)):
        rec['owned_by_guild'] = bool(guild and rec.get('owner_id') == guild)
        rec['kind'] = 'clan' if rec['owned_by_guild'] else 'placed'
        by_id[rec['object_id']] = rec
    for rec in _followers_from_markers(conn, char['id']):
        existing = by_id.get(rec['object_id'])
        if existing:
            existing['kind'] = 'following'
        else:
            rec['owned_by_guild'] = bool(guild and rec.get('owner_id') == guild)
            by_id[rec['object_id']] = rec
    return list(by_id.values())


def _count_items_for_character(conn: sqlite3.Connection, char_id: int) -> Tuple[int, int]:
    inv = 0
    props = 0
    if _table_exists(conn, 'item_inventory') and 'owner_id' in _columns(conn, 'item_inventory'):
        inv = conn.execute(
            'SELECT COUNT(*) AS c FROM item_inventory WHERE owner_id = ?', (char_id,)
        ).fetchone()[0]
    if _table_exists(conn, 'item_properties') and 'owner_id' in _columns(conn, 'item_properties'):
        props = conn.execute(
            'SELECT COUNT(*) AS c FROM item_properties WHERE owner_id = ?', (char_id,)
        ).fetchone()[0]
    return int(inv), int(props)


def _count_buildings(conn: sqlite3.Connection, owner_ids: Sequence[int]) -> int:
    if not owner_ids or not _table_exists(conn, 'buildings'):
        return 0
    if 'owner_id' not in _columns(conn, 'buildings'):
        return 0
    ph = ','.join(['?'] * len(owner_ids))
    return int(conn.execute(
        f'SELECT COUNT(*) AS c FROM buildings WHERE owner_id IN ({ph})',
        tuple(owner_ids),
    ).fetchone()[0])


def counts_for_owner(
    db_path: str,
    owner_id: int,
    include_clan_assets: bool = False,
) -> Dict[str, int]:
    conn = _connect(db_path)
    try:
        char = fetch_character(conn, owner_id)
        if not char:
            return {'items': 0, 'item_properties': 0, 'buildings': 0, 'thralls': 0}
        ids = source_owner_ids(char, include_clan_assets)
        items, props = _count_items_for_character(conn, char['id'])
        buildings = _count_buildings(conn, ids)
        thralls = len(collect_thralls(conn, char, include_clan_assets))
        return {
            'items': items,
            'item_inventory': items,
            'item_properties': props,
            'buildings': buildings,
            'thralls': thralls,
        }
    finally:
        conn.close()


def simulate_update_counts(
    db_path: str,
    source_id: int,
    categories: List[str],
    include_clan_assets: bool = False,
    source_is_guild: bool = False,  # unused; kept so old callers do not explode
) -> Dict[str, int]:
    del source_is_guild
    conn = _connect(db_path)
    res: Dict[str, int] = {}
    try:
        char = fetch_character(conn, source_id)
        if not char:
            return {
                'item_inventory': 0, 'item_properties': 0, 'items': 0,
                'buildings': 0, 'thralls': 0,
                'buildings_personal': 0, 'buildings_clan': 0,
                'thralls_personal': 0, 'thralls_clan': 0,
            }
        personal = [char['id']]
        clan = [char['guild']] if (include_clan_assets and char.get('guild')) else []
        all_ids = source_owner_ids(char, include_clan_assets)
        want_all = 'all' in categories

        if want_all or 'items' in categories:
            inv, props = _count_items_for_character(conn, char['id'])
            res['item_inventory'] = inv
            res['item_properties'] = props
        if want_all or 'buildings' in categories:
            res['buildings_personal'] = _count_buildings(conn, personal)
            res['buildings_clan'] = _count_buildings(conn, clan) if clan else 0
            res['buildings'] = _count_buildings(conn, all_ids)
        if want_all or 'thralls' in categories:
            rows = collect_thralls(conn, char, include_clan_assets)
            res['thralls_following'] = sum(1 for t in rows if t.get('kind') == 'following')
            res['thralls_clan'] = sum(1 for t in rows if t.get('kind') == 'clan')
            res['thralls_personal'] = sum(1 for t in rows if t.get('kind') == 'placed')
            res['thralls'] = len(rows)
        res['items'] = res.get('item_inventory', 0)
        return res
    finally:
        conn.close()


def load_item_xref_file(xref_path: str) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    if not os.path.exists(xref_path):
        return mapping
    try:
        with open(xref_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not (line.upper().startswith('SELECT') or line.upper().startswith('UNION ALL SELECT')):
                    continue
                parts = line.replace('UNION ALL ', '').split('SELECT', 1)[1]
                tokens = []
                cur = ''
                inq = False
                for ch in parts:
                    if ch == "'":
                        inq = not inq
                        cur += ch
                    elif ch == ',' and not inq:
                        tokens.append(cur.strip())
                        cur = ''
                    else:
                        cur += ch
                if cur:
                    tokens.append(cur.strip())
                if len(tokens) >= 2:
                    try:
                        tid = int(tokens[0].strip().strip("'"))
                        name = tokens[1].split('AS')[-1].strip().strip("' ")
                        mapping[tid] = name.strip("'")
                    except Exception:
                        continue
    except Exception:
        return {}
    return mapping


def list_items_for_owner(
    db_path: str,
    owner_id: int,
    xref: Optional[Dict[int, str]] = None,
    owner_is_guild: bool = False,
) -> List[Dict[str, Any]]:
    del owner_is_guild
    conn = _connect(db_path)
    try:
        if not _table_exists(conn, 'item_inventory'):
            return []
        cols = _columns(conn, 'item_inventory')
        if 'owner_id' not in cols:
            return []
        select = ['item_id', 'owner_id'] if 'item_id' in cols else ['owner_id']
        for extra in ('inv_type', 'template_id'):
            if extra in cols:
                select.append(extra)
        rows = conn.execute(
            f"SELECT {', '.join(select)} FROM item_inventory WHERE owner_id = ?",
            (owner_id,),
        ).fetchall()
        out = []
        for r in rows:
            tid = r['template_id'] if 'template_id' in r.keys() else None
            inv = r['inv_type'] if 'inv_type' in r.keys() else None
            name = xref.get(tid) if xref and tid is not None else None
            out.append({
                'item_id': r['item_id'] if 'item_id' in r.keys() else None,
                'inv_type': inv,
                'inv_label': INV_TYPE_LABELS.get(int(inv), str(inv)) if inv is not None else '',
                'template_id': tid,
                'template_name': name,
            })
        return out
    finally:
        conn.close()


def list_buildings_for_owner(
    db_path: str,
    owner_id: int,
    xref: Optional[Dict[int, str]] = None,
    owner_is_guild: bool = False,
    include_clan_assets: bool = False,
) -> List[Dict[str, Any]]:
    del owner_is_guild
    conn = _connect(db_path)
    try:
        char = fetch_character(conn, owner_id)
        if not char or not _table_exists(conn, 'buildings'):
            return []
        ids = source_owner_ids(char, include_clan_assets)
        ph = ','.join(['?'] * len(ids))
        has_bh = _table_exists(conn, 'buildable_health')
        has_pos = _table_exists(conn, 'actor_position')
        if has_bh:
            rows = conn.execute(
                f"""SELECT b.object_id, b.owner_id, bp.template_id
                    FROM buildings b
                    LEFT JOIN buildable_health bp ON bp.object_id = b.object_id
                    WHERE b.owner_id IN ({ph})""",
                tuple(ids),
            ).fetchall()
        else:
            rows = conn.execute(
                f'SELECT object_id, owner_id FROM buildings WHERE owner_id IN ({ph})',
                tuple(ids),
            ).fetchall()
        seen = set()
        out = []
        for r in rows:
            obj = r['object_id']
            if obj in seen:
                continue
            seen.add(obj)
            tid = r['template_id'] if 'template_id' in r.keys() else None
            cls = None
            coords = (None, None, None)
            if has_pos:
                ap = conn.execute(
                    'SELECT class, x, y, z FROM actor_position WHERE id = ?', (obj,)
                ).fetchone()
                if ap:
                    cls = ap['class']
                    coords = (ap['x'], ap['y'], ap['z'])
            name = xref.get(tid) if xref and tid is not None else None
            oid = int(r['owner_id'])
            out.append({
                'object_id': obj,
                'owner_id': oid,
                'owned_by_guild': bool(char.get('guild') and oid == char['guild']),
                'template_id': tid,
                'template_name': name or cls or str(obj),
                'class': cls,
                'coords': coords,
            })
        return out
    finally:
        conn.close()


def list_thralls_for_owner(
    db_path: str,
    owner_id: int,
    owner_is_guild: bool = False,
    include_clan_assets: bool = False,
) -> List[Dict[str, Any]]:
    del owner_is_guild
    conn = _connect(db_path)
    try:
        char = fetch_character(conn, owner_id)
        if not char:
            return []
        rows = collect_thralls(conn, char, include_clan_assets)
        for rec in rows:
            rec['owned_by_guild'] = bool(char.get('guild') and rec.get('owner_id') == char['guild'])
            rec['coords'] = str(rec.get('coords'))
        return rows
    finally:
        conn.close()


def _update_owner_column(
    cur: sqlite3.Cursor,
    table: str,
    owner_col: str,
    new_owner: int,
    old_owners: Sequence[int],
    extra_where: str = '',
    extra_params: Sequence = (),
) -> int:
    if not old_owners:
        return 0
    ph = ','.join(['?'] * len(old_owners))
    sql = f'UPDATE "{table}" SET "{owner_col}" = ? WHERE "{owner_col}" IN ({ph})'
    params: List[Any] = [new_owner, *old_owners]
    if extra_where:
        sql += ' AND ' + extra_where
        params.extend(extra_params)
    cur.execute(sql, params)
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


def _update_items(
    conn: sqlite3.Connection,
    source_char_id: int,
    target_char_id: int,
    item_keys: Optional[Sequence[Tuple[int, Optional[int]]]],
) -> Dict[str, int]:
    changed = {'item_inventory': 0, 'item_properties': 0}
    cur = conn.cursor()

    def apply(table: str) -> int:
        if not _table_exists(conn, table):
            return 0
        cols = _columns(conn, table)
        if 'owner_id' not in cols:
            return 0
        has_item = 'item_id' in cols
        has_inv = 'inv_type' in cols
        if item_keys is None:
            cur.execute(
                f'UPDATE "{table}" SET owner_id = ? WHERE owner_id = ?',
                (target_char_id, source_char_id),
            )
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        if item_keys is not None and len(item_keys) == 0:
            return 0
        total = 0
        for key in item_keys:
            item_id, inv_type = key[0], key[1] if len(key) > 1 else None
            sql = f'UPDATE "{table}" SET owner_id = ? WHERE owner_id = ?'
            params: List[Any] = [target_char_id, source_char_id]
            if has_item:
                sql += ' AND item_id = ?'
                params.append(item_id)
            if has_inv and inv_type is not None:
                sql += ' AND inv_type = ?'
                params.append(inv_type)
            cur.execute(sql, params)
            total += cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        return total

    changed['item_inventory'] = apply('item_inventory')
    changed['item_properties'] = apply('item_properties')
    _log(
        f'Item transfer: {source_char_id} -> {target_char_id} '
        f'keys={None if item_keys is None else len(item_keys)} changed={changed}'
    )
    return changed


def _update_buildings(
    conn: sqlite3.Connection,
    old_owners: Sequence[int],
    new_owner: int,
    object_ids: Optional[Sequence[int]],
) -> int:
    if not _table_exists(conn, 'buildings') or not old_owners:
        return 0
    cur = conn.cursor()
    if object_ids is not None and len(object_ids) == 0:
        return 0
    if object_ids is None:
        n = _update_owner_column(cur, 'buildings', 'owner_id', new_owner, old_owners)
        _log(f'Building transfer: owners {list(old_owners)} -> {new_owner}, rows={n}')
        return n
    total = 0
    for chunk in _chunks(list(object_ids)):
        ph = ','.join(['?'] * len(chunk))
        n = _update_owner_column(
            cur, 'buildings', 'owner_id', new_owner, old_owners,
            extra_where=f'object_id IN ({ph})', extra_params=chunk,
        )
        total += n
    _log(f'Building transfer subset: owners {list(old_owners)} -> {new_owner}, rows={total}')
    return total


def _rewrite_owner_unique_ids(
    conn: sqlite3.Connection,
    new_owner: int,
    *,
    match_old_owners: Optional[Sequence[int]] = None,
    object_ids: Optional[Sequence[int]] = None,
) -> int:
    """Rewrite OwnerUniqueID blobs. Match decoded owner and/or explicit object ids."""
    if not _table_exists(conn, 'properties'):
        return 0
    allow = None if object_ids is None else {int(x) for x in object_ids}
    if allow is not None and not allow:
        return 0
    wanted = {int(x) for x in (match_old_owners or []) if valid_owner_id(x)}
    if not wanted and allow is None:
        return 0
    changed = 0
    cur = conn.cursor()
    for row in _load_thrall_owner_rows(conn):
        if not _is_thrall_owner_property(row['name']):
            continue
        oid = int(row['object_id'])
        if allow is not None and oid not in allow:
            continue
        current = decode_owner_unique_id(row['value'])
        if wanted and current not in wanted:
            continue
        if current == new_owner:
            continue
        new_blob = encode_owner_unique_id(row['value'], new_owner)
        cur.execute(
            'UPDATE properties SET value = ? WHERE object_id = ? AND name = ?',
            (sqlite3.Binary(new_blob), oid, row['name']),
        )
        changed += cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    return changed


def _update_thralls(
    conn: sqlite3.Connection,
    old_owners: Sequence[int],
    new_owner: int,
    thrall_ids: Optional[Sequence[int]],
) -> int:
    n = _rewrite_owner_unique_ids(
        conn, new_owner, match_old_owners=old_owners, object_ids=thrall_ids,
    )
    _log(f'Thrall OwnerUniqueID rewrite: {list(old_owners)} -> {new_owner}, rows={n}')
    return n


def _marker_follower_ids(conn: sqlite3.Connection, char_id: int) -> List[int]:
    if not _table_exists(conn, 'follower_markers'):
        return []
    cols = _columns(conn, 'follower_markers')
    if 'owner_id' not in cols or 'follower_id' not in cols:
        return []
    return [
        int(r[0]) for r in conn.execute(
            'SELECT follower_id FROM follower_markers WHERE owner_id = ?', (char_id,)
        )
    ]


def _update_follower_markers(
    conn: sqlite3.Connection,
    source_char_id: int,
    target_char_id: int,
    thrall_ids: Optional[Sequence[int]],
) -> int:
    if not _table_exists(conn, 'follower_markers'):
        return 0
    cols = _columns(conn, 'follower_markers')
    if 'owner_id' not in cols or 'follower_id' not in cols:
        return 0
    if thrall_ids is not None and len(list(thrall_ids)) == 0:
        return 0
    cur = conn.cursor()
    if thrall_ids is None:
        cur.execute(
            'UPDATE follower_markers SET owner_id = ? WHERE owner_id = ?',
            (target_char_id, source_char_id),
        )
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    else:
        n = 0
        for chunk in _chunks(list(thrall_ids)):
            ph = ','.join(['?'] * len(chunk))
            cur.execute(
                f'UPDATE follower_markers SET owner_id = ? WHERE owner_id = ? AND follower_id IN ({ph})',
                (target_char_id, source_char_id, *chunk),
            )
            n += cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    _log(f'follower_markers {source_char_id} -> {target_char_id}, rows={n}')
    return n


def perform_transfer(
    db_path: str,
    source_id: int,
    target_id: int,
    categories: List[str],
    dry_run: bool = False,
    item_ids: Optional[List[int]] = None,
    building_object_ids: Optional[List[int]] = None,
    thrall_ids: Optional[List[int]] = None,
    item_keys: Optional[List[Tuple[int, Optional[int]]]] = None,
    include_clan_assets: bool = False,
    clan_assets_to_target_guild: bool = False,
    pre_backup_path: Optional[str] = None,
    # leftover kwargs from the old UI — ignored on purpose
    set_source_guild_to_target: bool = False,
    target_is_guild: bool = False,
    include_discovered_owner_columns: bool = False,
    source_is_guild: bool = False,
) -> Tuple[bool, Dict[str, Any], str]:
    """Move Funcom-recognized ownership from source character to target character."""
    del set_source_guild_to_target, target_is_guild, include_discovered_owner_columns, source_is_guild

    if source_id == target_id:
        return False, {}, 'Source and target must be different characters.'

    before = simulate_update_counts(db_path, source_id, categories, include_clan_assets)
    if dry_run:
        _log(f'Dry-run: source={source_id} target={target_id} cats={categories} clan={include_clan_assets}')
        return True, before, 'Dry-run completed, no changes applied.'

    if item_keys is None and item_ids is not None:
        item_keys = [(int(i), None) for i in item_ids]

    conn = _connect(db_path, write=True)
    changed: Dict[str, Any] = {}
    try:
        try:
            conn.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as e:
            return False, {}, (
                f'Cannot lock {db_path}: {e}. Stop the dedicated server and the game client, then try again.'
            )

        source = fetch_character(conn, source_id)
        target = fetch_character(conn, target_id)
        if not source:
            conn.rollback()
            return False, {}, f'Source character id {source_id} was not found.'
        if not target:
            conn.rollback()
            return False, {}, f'Target character id {target_id} was not found.'

        want_all = 'all' in categories
        personal_src = [source['id']]
        clan_src = [source['guild']] if (include_clan_assets and source.get('guild')) else []
        same_guild = bool(
            include_clan_assets and source.get('guild') and source.get('guild') == target.get('guild')
        )
        clan_target_id = target['guild'] if (clan_assets_to_target_guild and target.get('guild')) else target['id']
        skip_clan_noop = same_guild and clan_assets_to_target_guild and clan_target_id == source.get('guild')

        if want_all or 'items' in categories:
            item_changed = _update_items(conn, source['id'], target['id'], item_keys)
            changed.update(item_changed)

        if want_all or 'buildings' in categories:
            n_personal = _update_buildings(conn, personal_src, target['id'], building_object_ids)
            n_clan = 0
            if clan_src and not skip_clan_noop:
                n_clan = _update_buildings(conn, clan_src, clan_target_id, building_object_ids)
            elif skip_clan_noop:
                _log('Skipped clan building rewrite (source and target already share that guild).')
            changed['buildings_personal'] = n_personal
            changed['buildings_clan'] = n_clan
            changed['buildings'] = n_personal + n_clan

        if want_all or 'thralls' in categories:
            marker_ids = _marker_follower_ids(conn, source['id'])
            if thrall_ids is not None:
                allow_ids = {int(x) for x in thrall_ids}
                marker_ids = [x for x in marker_ids if x in allow_ids]

            n_clan = 0
            if clan_src and not skip_clan_noop:
                n_clan = _update_thralls(conn, clan_src, clan_target_id, thrall_ids)
            elif skip_clan_noop:
                _log('Skipped clan thrall rewrite (source and target already share that guild).')
            n_personal = _update_thralls(conn, personal_src, target['id'], thrall_ids)
            n_markers_blob = 0
            if marker_ids:
                n_markers_blob = _rewrite_owner_unique_ids(
                    conn, target['id'], object_ids=marker_ids
                )
            n_fm = _update_follower_markers(conn, source['id'], target['id'], thrall_ids)
            changed['thralls_personal'] = n_personal
            changed['thralls_clan'] = n_clan
            changed['thralls_following'] = n_fm
            changed['thralls'] = n_personal + n_clan + n_fm
            if n_markers_blob:
                changed['thralls_following_blobs'] = n_markers_blob

        conn.commit()
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error:
            pass

        ic = conn.execute('PRAGMA integrity_check').fetchone()[0]
        if ic != 'ok':
            msg = f'Integrity check failed after transfer: {ic}'
            _log(msg)
            if pre_backup_path and os.path.exists(pre_backup_path):
                conn.close()
                sqlite_copy(pre_backup_path, db_path)
                return False, changed, msg + ' Database restored from pre-transfer backup.'
            return False, changed, msg

        _log(f'Transfer ok: {source_id} -> {target_id} changed={changed}')
        return True, changed, 'Transfer completed successfully.'
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log(f'Transfer failed: {e}')
        return False, {}, str(e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def write_audit_csv(csv_path: str, record: Dict[str, Any]) -> None:
    fieldnames = [
        'timestamp', 'db_path', 'pre_transfer_backup', 'source_id', 'target_id', 'categories',
        'item_ids', 'building_object_ids', 'thrall_ids', 'changed_json', 'message',
        'before_source', 'after_source', 'before_target', 'after_target',
        'include_clan_assets', 'clan_assets_to_target_guild',
    ]
    write_header = not os.path.exists(csv_path)
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        _log(f"write_audit_csv: keys={list(record.keys())}")
        row = {k: record.get(k, '') for k in fieldnames}
        row['categories'] = json.dumps(record.get('categories', []), ensure_ascii=False)
        row['item_ids'] = json.dumps(record.get('item_ids', []), ensure_ascii=False)
        row['building_object_ids'] = json.dumps(record.get('building_object_ids', []), ensure_ascii=False)
        row['thrall_ids'] = json.dumps(record.get('thrall_ids', []), ensure_ascii=False)
        changed_val = record.get('changed') if 'changed' in record else record.get('changed_json', {})
        row['changed_json'] = json.dumps(changed_val or {}, ensure_ascii=False)
        for k in ('before_source', 'after_source', 'before_target', 'after_target'):
            row[k] = json.dumps(record.get(k, {}), ensure_ascii=False)
        row['include_clan_assets'] = json.dumps(bool(record.get('include_clan_assets', False)))
        row['clan_assets_to_target_guild'] = json.dumps(bool(record.get('clan_assets_to_target_guild', False)))
        w.writerow(row)


def find_pre_backup(transferred_db_path: str) -> Optional[str]:
    pre_path = transferred_db_path + '.pre'
    if os.path.exists(pre_path):
        return pre_path
    return None


def revert_transfer(transferred_db_path: str, backup_path: Optional[str] = None) -> Tuple[bool, str]:
    """Restore a transferred DB from `.pre` or an explicit backup file."""
    if not os.path.exists(transferred_db_path):
        return False, f'Transferred DB not found: {transferred_db_path}'
    src = backup_path or find_pre_backup(transferred_db_path)
    if not src:
        return False, (
            f'Pre-transfer backup not found next to {transferred_db_path}. '
            'Choose a .pre or .bak_* file created by this app.'
        )
    if not os.path.exists(src):
        return False, f'Backup not found: {src}'
    try:
        sqlite_copy(src, transferred_db_path)
        _log(f'Reverted {transferred_db_path} from {src}')
        return True, f'Revert successful from {src}.'
    except Exception as e:
        _log(f'Revert failed for {transferred_db_path}: {e}')
        return False, str(e)


def create_pre_backup(db_path: str) -> str:
    """Always-on revert file: <db>.pre, plus return the path."""
    pre_path = db_path + '.pre'
    sqlite_copy(db_path, pre_path)
    return pre_path


# --- Save handoff (account rebind) -------------------------------------------------

_MASTER_ACCOUNT_ID_RE = re.compile(
    r'MasterAccountId\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)


def parse_game_ini_master_account_id(ini_path: str) -> Optional[str]:
    """Extract Funcom MasterAccountId from Game.ini CachedUsers line."""
    if not ini_path or not os.path.isfile(ini_path):
        return None
    try:
        with open(ini_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                m = _MASTER_ACCOUNT_ID_RE.search(line)
                if m:
                    return m.group(1).strip()
    except OSError:
        return None
    return None


def _account_user_column(conn: sqlite3.Connection) -> Optional[str]:
    if not _table_exists(conn, 'account'):
        return None
    cols = _columns(conn, 'account')
    for name in ('user', 'User', 'masterAccountId', 'MasterAccountId'):
        if name in cols:
            return name
    return None


def _account_id_column(conn: sqlite3.Connection) -> Optional[str]:
    if not _table_exists(conn, 'account'):
        return None
    cols = _columns(conn, 'account')
    for name in ('id', 'accountId', 'account_id'):
        if name in cols:
            return name
    return 'rowid'


def list_accounts(db_path: str) -> List[Dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if not _table_exists(conn, 'account'):
            return []
        user_col = _account_user_column(conn)
        id_col = _account_id_column(conn)
        if not user_col:
            return []
        select_id = f'"{id_col}"' if id_col != 'rowid' else 'rowid'
        rows = conn.execute(
            f'SELECT {select_id} AS aid, "{user_col}" AS user FROM account ORDER BY {select_id}'
        ).fetchall()
        out = []
        for r in rows:
            aid = r['aid']
            if aid is None:
                continue
            out.append({'id': int(aid), 'user': str(r['user']) if r['user'] is not None else ''})
        return out
    finally:
        conn.close()


def fetch_account(conn: sqlite3.Connection, account_id: int) -> Optional[Dict[str, Any]]:
    if not _table_exists(conn, 'account'):
        return None
    user_col = _account_user_column(conn)
    id_col = _account_id_column(conn)
    if not user_col:
        return None
    select_id = f'"{id_col}"' if id_col != 'rowid' else 'rowid'
    row = conn.execute(
        f'SELECT {select_id} AS aid, "{user_col}" AS user FROM account WHERE {select_id} = ?',
        (account_id,),
    ).fetchone()
    if not row:
        return None
    return {'id': int(row['aid']), 'user': str(row['user']) if row['user'] is not None else ''}


def find_account_by_user(conn: sqlite3.Connection, user: str) -> Optional[Dict[str, Any]]:
    if not user or not _table_exists(conn, 'account'):
        return None
    user_col = _account_user_column(conn)
    id_col = _account_id_column(conn)
    if not user_col:
        return None
    select_id = f'"{id_col}"' if id_col != 'rowid' else 'rowid'
    row = conn.execute(
        f'SELECT {select_id} AS aid, "{user_col}" AS user FROM account WHERE "{user_col}" = ?',
        (user.strip(),),
    ).fetchone()
    if not row:
        return None
    return {'id': int(row['aid']), 'user': str(row['user']) if row['user'] is not None else ''}


def account_id_for_character(conn: sqlite3.Connection, char_id: int) -> Optional[int]:
    char = fetch_character(conn, char_id)
    if not char or char.get('playerId') is None:
        return None
    try:
        return int(char['playerId'])
    except (TypeError, ValueError):
        return None


def characters_for_account(conn: sqlite3.Connection, account_id: int) -> List[Dict[str, Any]]:
    if not _table_exists(conn, 'characters') or 'playerId' not in _columns(conn, 'characters'):
        return []
    rows = conn.execute(
        'SELECT id, char_name, playerId, guild FROM characters WHERE playerId = ? ORDER BY id',
        (account_id,),
    ).fetchall()
    out = []
    for r in rows:
        cid = valid_owner_id(r['id'])
        if cid is None:
            continue
        out.append({
            'id': cid,
            'char_name': r['char_name'] if 'char_name' in r.keys() else str(cid),
            'playerId': r['playerId'],
            'guild': valid_owner_id(r['guild'] if 'guild' in r.keys() else None),
        })
    return out


def _delete_characters(cur: sqlite3.Cursor, char_ids: Sequence[int]) -> int:
    if not char_ids:
        return 0
    total = 0
    for chunk in _chunks(list(char_ids)):
        ph = ','.join(['?'] * len(chunk))
        cur.execute(f'DELETE FROM characters WHERE id IN ({ph})', chunk)
        total += cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    return total


def _delete_orphan_accounts(cur: sqlite3.Cursor, conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, 'account') or not _table_exists(conn, 'characters'):
        return 0
    if 'playerId' not in _columns(conn, 'characters'):
        return 0
    id_col = _account_id_column(conn)
    select_id = f'"{id_col}"' if id_col != 'rowid' else 'rowid'
    used = {
        int(r[0]) for r in cur.execute(
            'SELECT DISTINCT playerId FROM characters WHERE playerId IS NOT NULL'
        )
    }
    orphan_ids = [
        int(r[0]) for r in cur.execute(f'SELECT {select_id} FROM account')
        if r[0] is not None and int(r[0]) not in used
    ]
    if not orphan_ids:
        return 0
    total = 0
    for chunk in _chunks(orphan_ids):
        ph = ','.join(['?'] * len(chunk))
        cur.execute(f'DELETE FROM account WHERE {select_id} IN ({ph})', chunk)
        total += cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    return total


def simulate_save_handoff(
    db_path: str,
    source_char_id: int,
    target_account_user: str,
    *,
    remove_character_ids: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Dry-run report for rebinding a save to a new Funcom account."""
    report: Dict[str, Any] = {
        'source_char_id': source_char_id,
        'target_account_user': target_account_user.strip() if target_account_user else '',
        'has_account_table': False,
        'source_account_id': None,
        'source_account_user_before': None,
        'target_account_user_after': None,
        'existing_target_account_id': None,
        'will_rebind_account_user': False,
        'will_repoint_player_id': False,
        'characters_to_remove': [],
        'orphan_accounts_to_remove': [],
        'asset_counts': {},
        'errors': [],
    }
    if not target_account_user or not str(target_account_user).strip():
        report['errors'].append('Target account id (MasterAccountId) is required.')
        return report

    conn = _connect(db_path)
    try:
        if not _table_exists(conn, 'account'):
            report['errors'].append('No account table in this database.')
            return report
        report['has_account_table'] = True
        user_col = _account_user_column(conn)
        if not user_col:
            report['errors'].append('Could not locate account user column.')
            return report

        source = fetch_character(conn, source_char_id)
        if not source:
            report['errors'].append(f'Source character {source_char_id} not found.')
            return report

        src_acct_id = account_id_for_character(conn, source_char_id)
        if src_acct_id is None:
            report['errors'].append(f'Source character {source_char_id} has no playerId.')
            return report

        src_acct = fetch_account(conn, src_acct_id)
        if not src_acct:
            report['errors'].append(f'Account row {src_acct_id} not found.')
            return report

        target_user = str(target_account_user).strip()
        existing = find_account_by_user(conn, target_user)

        report['source_account_id'] = src_acct_id
        report['source_account_user_before'] = src_acct['user']
        report['target_account_user_after'] = target_user
        report['asset_counts'] = counts_for_owner(db_path, source_char_id, include_clan_assets=True)

        if existing and existing['id'] != src_acct_id:
            report['existing_target_account_id'] = existing['id']
            report['will_repoint_player_id'] = True
            report['will_rebind_account_user'] = False
            if src_acct['user'] != target_user:
                report['orphan_accounts_to_remove'].append(src_acct_id)
        elif src_acct['user'] != target_user:
            report['will_rebind_account_user'] = True

        to_remove = [int(x) for x in (remove_character_ids or []) if int(x) != source_char_id]
        report['characters_to_remove'] = to_remove

        if remove_character_ids is None and existing and existing['id'] != src_acct_id:
            for c in characters_for_account(conn, existing['id']):
                if c['id'] != source_char_id and c['id'] not in to_remove:
                    report['characters_to_remove'].append(c['id'])
        return report
    finally:
        conn.close()


def perform_save_handoff(
    db_path: str,
    source_char_id: int,
    target_account_user: str,
    *,
    dry_run: bool = False,
    remove_character_ids: Optional[Sequence[int]] = None,
    pre_backup_path: Optional[str] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    """Rebind a save so Person B's Funcom account owns Person A's character and world."""
    target_user = str(target_account_user or '').strip()
    if not target_user:
        return False, {}, 'Target account id (MasterAccountId) is required.'

    sim = simulate_save_handoff(
        db_path, source_char_id, target_user, remove_character_ids=remove_character_ids,
    )
    if sim.get('errors'):
        return False, sim, '; '.join(sim['errors'])

    if dry_run:
        _log(f'Save handoff dry-run: char={source_char_id} target_user={target_user} sim={sim}')
        return True, sim, 'Dry-run completed, no changes applied.'

    changed: Dict[str, Any] = {
        'account_user_updated': 0,
        'player_id_repointed': 0,
        'characters_removed': 0,
        'orphan_accounts_removed': 0,
    }

    conn = _connect(db_path, write=True)
    try:
        try:
            conn.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as e:
            return False, {}, (
                f'Cannot lock {db_path}: {e}. Stop the dedicated server and the game client, then try again.'
            )

        user_col = _account_user_column(conn)
        id_col = _account_id_column(conn)
        if not user_col:
            conn.rollback()
            return False, {}, 'Could not locate account user column.'

        select_id = f'"{id_col}"' if id_col != 'rowid' else 'rowid'
        src_acct_id = account_id_for_character(conn, source_char_id)
        if src_acct_id is None:
            conn.rollback()
            return False, {}, f'Source character {source_char_id} has no playerId.'

        src_acct = fetch_account(conn, src_acct_id)
        if not src_acct:
            conn.rollback()
            return False, {}, f'Account row {src_acct_id} not found.'

        existing = find_account_by_user(conn, target_user)
        cur = conn.cursor()

        if existing and existing['id'] != src_acct_id:
            cur.execute(
                'UPDATE characters SET playerId = ? WHERE id = ?',
                (existing['id'], source_char_id),
            )
            changed['player_id_repointed'] = cur.rowcount if cur.rowcount is not None else 0
            _log(f'Save handoff: character {source_char_id} playerId -> {existing["id"]}')
        elif src_acct['user'] != target_user:
            cur.execute(
                f'UPDATE account SET "{user_col}" = ? WHERE {select_id} = ?',
                (target_user, src_acct_id),
            )
            changed['account_user_updated'] = cur.rowcount if cur.rowcount is not None else 0
            _log(f'Save handoff: account {src_acct_id} user -> {target_user}')

        remove_ids = {int(x) for x in (remove_character_ids or []) if int(x) != source_char_id}
        if remove_character_ids is None and existing and existing['id'] != src_acct_id:
            for c in characters_for_account(conn, existing['id']):
                if c['id'] != source_char_id:
                    remove_ids.add(c['id'])

        if remove_ids:
            changed['characters_removed'] = _delete_characters(cur, sorted(remove_ids))
            _log(f'Save handoff: removed characters {sorted(remove_ids)}')

        changed['orphan_accounts_removed'] = _delete_orphan_accounts(cur, conn)

        conn.commit()
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error:
            pass

        ic = conn.execute('PRAGMA integrity_check').fetchone()[0]
        if ic != 'ok':
            msg = f'Integrity check failed after save handoff: {ic}'
            _log(msg)
            if pre_backup_path and os.path.exists(pre_backup_path):
                conn.close()
                sqlite_copy(pre_backup_path, db_path)
                return False, changed, msg + ' Database restored from pre-handoff backup.'
            return False, changed, msg

        after = simulate_save_handoff(db_path, source_char_id, target_user)
        changed['after'] = after
        _log(f'Save handoff ok: char={source_char_id} user={target_user} changed={changed}')
        return True, changed, 'Save handoff completed successfully.'
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log(f'Save handoff failed: {e}')
        return False, {}, str(e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def perform_save_handoff_multi(
    db_paths: Sequence[str],
    source_char_id: int,
    target_account_user: str,
    *,
    dry_run: bool = False,
    remove_character_ids: Optional[Sequence[int]] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    """Apply save handoff to game.db and optional dlc_siptah.db (same character id)."""
    paths = [p for p in db_paths if p and os.path.isfile(p)]
    if not paths:
        return False, {}, 'No database files provided.'

    primary = paths[0]
    if dry_run:
        results = {}
        for p in paths:
            ok, report, msg = perform_save_handoff(
                p, source_char_id, target_account_user,
                dry_run=True, remove_character_ids=remove_character_ids,
            )
            results[p] = {'ok': ok, 'report': report, 'message': msg}
        all_ok = all(r['ok'] for r in results.values())
        return all_ok, {'databases': results}, 'Dry-run completed.' if all_ok else 'Dry-run found errors.'

    backups: Dict[str, str] = {}
    try:
        for p in paths:
            backups[p] = create_pre_backup(p)
    except Exception as e:
        return False, {}, f'Backup failed: {e}'

    results: Dict[str, Any] = {}
    all_ok = True
    last_msg = ''
    for p in paths:
        ok, changed, msg = perform_save_handoff(
            p, source_char_id, target_account_user,
            dry_run=False,
            remove_character_ids=remove_character_ids if p == primary else [],
            pre_backup_path=backups[p],
        )
        results[p] = {'ok': ok, 'changed': changed, 'message': msg, 'backup': backups[p]}
        if not ok:
            all_ok = False
            last_msg = msg
            for prev in paths:
                if prev == p:
                    break
                if prev in backups and os.path.exists(backups[prev]):
                    sqlite_copy(backups[prev], prev)
            if os.path.exists(backups[p]) and os.path.exists(p):
                sqlite_copy(backups[p], p)
            break
        last_msg = msg

    return all_ok, {'databases': results}, last_msg


def write_handoff_audit_csv(csv_path: str, record: Dict[str, Any]) -> None:
    fieldnames = [
        'timestamp', 'operation', 'db_paths', 'pre_transfer_backup', 'source_char_id',
        'target_account_user', 'remove_character_ids', 'changed_json', 'message', 'simulation_json',
    ]
    write_header = not os.path.exists(csv_path)
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        row = {k: record.get(k, '') for k in fieldnames}
        row['operation'] = record.get('operation', 'save_handoff')
        row['db_paths'] = json.dumps(record.get('db_paths', []), ensure_ascii=False)
        row['remove_character_ids'] = json.dumps(record.get('remove_character_ids', []), ensure_ascii=False)
        row['changed_json'] = json.dumps(record.get('changed', {}), ensure_ascii=False)
        row['simulation_json'] = json.dumps(record.get('simulation', {}), ensure_ascii=False)
        w.writerow(row)
