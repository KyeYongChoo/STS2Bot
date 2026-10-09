# STS2MCP patches

`vendor/STS2MCP` is a gitignored clone of the STS2MCP mod. These patches
record this project's changes to it, for rebuilding the mod elsewhere.
They apply on top of branch `local/v0111-plus-deck` at `cb156dc` ("Expose
win/loss on the game_over state"):

```powershell
git -C vendor/STS2MCP am ../../patches/sts2mcp/*.patch
vendor\STS2MCP\build.ps1 -GameDir "<game dir>"
scripts\install_mod.ps1 -FromBuild    # with the game closed
```

- `0001` — `dev_set_deck` action (set the master deck exactly, incl.
  upgrades/enchantments) and `run.act_id`.
- `0002` — `upgrades` / `enchantment` / `enchantment_amount` on card info.
