# Conan Exiles Database Transfers

PySide6 utility that reassigns Funcom ownership in a Conan Exiles SQLite `game.db` from one character to another.

This is not a generic “find every column named owner” rewriter. Funcom stores ownership in a few specific places:

- **Carried items** — `item_inventory` / `item_properties` where `owner_id` is the **character id** (hotbar, bags, equipment). Chest and crafting-station contents use the **structure’s object_id** as `owner_id` and move automatically when that building is transferred.
- **Buildings / placeables** — `buildings.owner_id` is either a character id (personal) or a `guilds.guildId` (clan). `0` is unowned and is never touched. `characters.playerId` is an account FK, not an owner id, and is never used as a match.
- **Thralls / pets** — `properties.BP_ThrallComponent_C.OwnerUniqueID` (and entertainer/pet variants). The last 8 bytes of the blob are a little-endian uint64. **On clan servers that value is the guildId, not the character id.** Companions currently on a character’s follower wheel are listed in `follower_markers`. The app unions both. Clan include turns on automatically when the source is in a clan.

## Safety

- Stop the dedicated server and the game client before transferring. A live Funcom process will overwrite your edits.
- Every transfer writes `game.db.pre` (revert file) and a timestamped `.bak_<unix>` copy using SQLite’s backup API so WAL contents are included.
- Revert restores from `.pre` (or a backup you pick).
- Integrity check runs after the write; a failed check restores the `.pre` snapshot.

## Usage

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 main.py
```

If you prefer a precompiled binary, you can download it from the Releases page.

## Save handoff (account rebind)

When Person A gives Person B an entire `game.db` (solo or dedicated server), the world and assets are already in the file but the save may still be linked to Person A's Funcom account. Use **Save Handoff** mode to rebind the save to Person B:

1. Pick Person A's `game.db` (and optional `dlc_siptah.db`).
2. Select the **character to keep** (Person A's main character).
3. Browse Person B's **Game.ini** to load their `MasterAccountId`, or paste it manually.
  - Typical path: `ConanSandbox/Saved/Config/WindowsNoEditor/Game.ini`
  - Person B should launch Conan once so `CachedUsers` is written.
4. Click **Analyze Handoff**, then **Rebind Save**.

This updates the `account` table (and `characters.playerId` when Person B already created a bootstrap character in that save). **In-game ownership is unchanged** — buildings, items, and thralls stay on the same character id.

Optional: remove throwaway/bootstrap characters Person B created when first opening the save.

## Character transfer (same save)

Pick `game.db`, source and target characters, then Items / Buildings / Thralls. Use **Details** to transfer a subset. Cancel in Details leaves “all matching”; OK with nothing checked transfers none of that category.

**Include clan-owned buildings and followers** reassigns assets owned by the source character’s guild, not just their personal id. Other clan members lose those assets. Optionally write clan assets to the **target’s guild** instead of the target character.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT License

Copyright (c) 2026 dbowlin and the entire Conan Exiles Enhanced community.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.