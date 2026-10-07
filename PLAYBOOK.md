# Day plan

This is the bot's job while the computer is left alone. The code that
chooses the next action is `src/wiz101_auto/dayplan.py`. If this file and
that module disagree, this file is the intent and the module is what runs,
so change them together.

Energy is the green globe. It is not mana. A dance game spends it. It
comes back slowly, and a level-up fills it. While it can pay for a game,
that game is what runs.

## What runs until stopped

Gardening, fishing, and hatching are paused. The modules are still here.
The day plan does not choose them.

1. **Dance.** While the globe can pay for one more game, play the dance
   game. The price is the one the dance window last showed: an adult game
   is 6, and 4 energy leaves that window closed and farms. Feed the first
   snack after each win. An empty snack offer does not
   end the trip. A talent outside the ghulture set does not end it either.
   This keeps going past adult and past mega.
2. **Couch potato seeds.** When the globe cannot pay, fight Splithoof
   Barbarians in Boar Camp, Savarstaad Pass, Grizzleheim. One fight, then
   look again. A locked zone is tried again. It is not replaced with a
   street mob.
3. **Gold.** More than 20 couch potatoes in the bag (seeds and harvested
   potatoes both count) switches that farm to Halfang Bristlecrown in
   Vestrilund. Level does not matter. A locked Wintertusk is tried again.

Street gates stay inside one world. The spiral door changes worlds in
either direction: the Pet Pavilion to Savarstaad or Vestrilund, Grizzleheim
back to the pavilion, and one farm to the other. The dorm is left on foot.
A house door opens onto any world. A zone with no gate path goes Home once,
then takes that same door.

`mode: pet` only dances. `mode: reagents` only gathers the reagent that was
asked for. `mode: seeds` does not dance. It fights Troubled Warriors and
Splithoof Barbarians in Savarstaad Pass until it is stopped. Each round
casts Deathblade, then Empower, then Scarecrow, and passes while the next
of those is not ready. A death drain is used only when health is low and
that next card cannot be cast. If neither creature is loaded where the
wizard is standing, it moves through the Savarstaad compass spots until
one is. It walks onto the creature. Across the zone, one teleport lands
a short walk away and that same walk finishes it. A creature that does not
fight is skipped in place, and the next pull walks to another. It holds W
with a posted key, because the game ignores the other key path. The log
names each teleport and each walk before it runs. Mana under about a third walks to
Northguard, takes a mana wisp, and comes back. A bag with two slots left
goes to Elik at the Wizard City Bazaar, presses X, and opens Backpack Buddy.
It sells only names that were not in the bag when the run started. Couch
potato seeds stay. A name shared with an older item, and anything equipped,
is not clicked. `chores` is the list above. Deaths, low health, a missed zone,
and an empty snack pile do not stop the session. Stop is the menu bar, or
`state/stop.request`.

Health under about 60% in a fight casts a death drain on an enemy: Ghoul,
Wraith, Vampire, Skeletal Dragon, or any other card that steals health.
Sacrifice is not used. Those cards have to be in the equipped deck. Out of
a fight, low health uses a potion if one is charged, then stands on a
health wisp, then goes back to the same farm. A defeat does the same.

## 1. The ghulture

The pet is a ghulture with max stats and these five talents:

- death-dealer
- death-giver
- pain-giver
- mighty
- armor breaker

Max stats, the ones a perfect (2.0) pet shows: **255 strength, 250 intellect,
260 agility, 260 will, 250 power.** Within 5 of each still gives the full
talent value. Mighty adds strength on top of the 255; the 255 is the base.

Talents manifest one per stage: teen, adult, ancient, epic, mega. Ultra
adds no talent. Dance games cost 2 energy at baby and 2 more at each stage.

Two pets matter, every hatch.

- **The kiosk pet** does not change. It is a perfect ghulture from the hatch
  making kiosk: the five talents above, and 255/250/260/260/250. Every hatch
  is with that pet. It is not one we own, and it is not trained.
- **The base** is the ghulture we own that is closest to that perfect pet.
  Closer means less missing from the max stats, then more of the five
  talents already manifested, then further trained. A manifested talent
  outside the five is a miss and is never the base.

Leveling does not raise max stats. A body that is short of the caps will
not become perfect by dancing, so it is not trained past adult, and it is
not trained at all unless it is the base (or it should replace the base).
Adult is the line, because that is when a pet can be hatched. A miss stops
where it failed, including teen. Do not hatch the miss. Hatch the previous
base with the kiosk pet again.

A body whose stats are already at the caps can still be the perfect pet, so
that one is trained to mega. `"confirm_mega": true` in `state/pet.json` is
the same instruction when the stats were not read. If that attempt misses,
it is dropped and the previous base is the parent again.

The base is remembered in `state/pet.json` under `base`, and the one before
it under `prior_base`. `state/` is not committed.

The rules above are the pet we are building. They are not what the live
loop does with energy right now. Dance games run whenever the globe can
pay, on whatever pet is equipped, and a snack is fed after each win.
Snacks are the first one the reward screen offers. Couch potatoes replace
those with rank 9s (Captain Canteloupe, Fancy Yogurt, Golden Wheat Bread).
Ghultures love death desserts and death cereals. Fancy Yogurt is the dessert.

Hatching is paused, so none of that gold is spent right now. When it is
turned back on, the hatch click happens only when the open kiosk page shows
a ghulture and all five talents. A page that does not is left alone, because
a wrong click spends the gold. Nothing is paid to speed the egg.

## 2. Couch potatoes

Paused with the rest of the garden. Seeds are still farmed, as above.
The plant itself is not tended, and the barn is not bought.

Couch potatoes are the right plant. They are free to farm, they plant at
gardening rank 3 (Apprentice), they use a large plot, and an elder harvest
returns one seed plus one rank 9 snack, gold, and a treasure card. Evil
magma peas drop a wider set of rank 9 snacks and are not worth crowns or
a rank 4 gate for this account.

Until rank 3, plant **boom shrooms** (Farley sells a seed for 1,200 gold,
small plot, rank 1). They are slow experience. That is the rank path in
progress. Do not plant couch potatoes early. The game will not allow it.

**Seeds.** Splithoof Barbarians in Boar Camp, Savarstaad Pass, Grizzleheim
(level 20). The drop rate is low. Stock 14 before worrying about a bigger
plot. Higher worlds drop them faster. That farm is for later, not instead
of Grizzleheim while the wizard is still here. A 69-plot is the spell-radius
cap and is not the first garden.

**Red Barn Farm** costs 125,000 gold or 12,500 crowns. Buy it for gold.
The house itself is a like (+15% growth) for couch potatoes. Buy it once
rank is 3 or a seed is already in the bag, and only when 100,000 gold would
remain for the next hatch. Boom shrooms do not need the barn. They grow in
a small plot at home.

**Likes, placed in the garden, not cast:** Litter, King Parsley, a pixie,
Sandwich Station, Tropical Garden Gnome, and the Red Barn house. Stinkweed
nearby slows them. Likes stack. Each one is a growth bonus, and the barn
plus a gnome plus a pixie is the set worth building toward.

**Care.** Cast a gardening spell only when the plant is showing that need.
These plants get rank 1 pests (Bug Bolt, or a bug spray). A plant with no
icon gets nothing. Do not spend energy on a guess. The spell buttons are
not mapped yet, so the bot goes home when care is due and does not cast.

Write the real numbers into `state/garden.json` when they change:

```json
{"rank": 1, "red_barn": false, "couch_potato_seeds": 0, "boom_shroom_seeds": 0, "plants_need_care": false}
```

## 3. Reagents

Not an all-day sweep. The tracked quest is read off the quest helper. A
goal like "Collect Scrap Iron in Chelsea Court (0 of 5)" starts a scrap-iron
run. `reagent "scrap iron"` forces one. `reagent --clear` goes back to the
quest. Anything else lying on the ground is left there.

Scrap iron is a ground node, not a mob drop worth fighting. The nodes that
matter are in Marleybone, around the teleporters in Chelsea Court, Knight's
Court, and Newgate Prison. The bot walks those streets and picks up scrap
iron only. It does not realm-hop.

## 4. Fishing

Paused. A fishing quest does not take energy away from the dance game.

Fish when the tracked quest is a fishing quest, or when `fish` was asked.
`fish --clear` drops the request. Pet and garden still take the energy first.

The cast is the first lure on the fishing bar, which is a spell the wizard
already owns. The reel is one press of space when a fish in the pond takes
the lure. Nothing is written into the fish or the game clock. A cast is
skipped when the pond is empty or the globe is under 2. A few catches, then
the day plan looks again.

## 5. Gold

Gold is what runs when a dance game does not. The fight is one duel, then
the plan looks again, so a refilled globe leaves for the Pet Pavilion.

- 20 couch potatoes or fewer: Splithoof Barbarians. Their junk also vendors.
- More than 20: Halfang Bristlecrown in Vestrilund, Wintertusk. His amulets
  sell at the Bazaar. That is the faster gold farm. It is chosen even under
  level 40. If the zone is locked, the same trip is tried again.

A healthy round uses an area hit from the deck already equipped, or one
single hit, or it passes. Under about 60% health it casts a death drain
first. Health under about a third, or a defeat, takes a potion or a health
wisp and then comes back. The session does not stop for either.

Keep 100,000 gold back while the ghulture is unfinished, once hatching is
turned back on. That is one hatch. The barn is 125,000 on top of it. The
housing shop is not mapped, so the barn gold is not spent. Hatching is
paused, so that gold is not spent either.

## What actually runs

| Action | Runs |
|---|---|
| Dance | Yes, while energy can pay for another game. A snack is fed after each win. A miss, an empty snack offer, and a finished goal do not end the trip. |
| Couch potato seeds | Yes. Splithoof Barbarians, over and over, until more than 20 couch potatoes are in the bag. |
| Gold fight | Yes, after that. Halfang, over and over. A death uses a potion or a health wisp and comes back. |
| Reagent pickup | Only in `mode: reagents`. A chores run does not leave the farm for scrap iron. |
| Garden | Paused. Rank and seeds are still read, so the potato count is real. No spell is cast and Farley is not visited. |
| Fishing | Paused. |
| Hatch | Paused. Nothing is paid to speed an egg. |
| Red Barn purchase | Paused. The housing shop is not clicked. |

`state/pet.json` remembers the equipped pet, the base, the previous base,
and `confirm_mega`. `state/` is not committed. A missing `state/garden.json`
is read as no couch potatoes, so the farm starts on Splithoof. The potato
count is the backpack total, and a reward line that names a couch potato
seed adds one when a stack does not show up as its own slot.

## Stopping

The bot has to unhook before the game is played. Closing its window leaves
the hook in, and the next start fails until Wizard101 is restarted.

On this Mac the stop is the menu bar item **Wiz on**. Choose **Stop and
unhook**. The same thing is `Stop Wizard Bot.command` in the repo, or
`python -m wiz101_auto stop` from the repo directory. Ctrl+Shift+Q is the
stop key while the bot is a Windows process inside the bottle. The menu bar
also sends it, once macOS Accessibility is allowed for that item.

A stop is a clean stop. The supervisor does not start it again.

## This Mac

Wizard101.app is the Windows client inside a private Wine bottle (CrossOver
23, bundled with the game). The graphical client is a 64-bit Windows
program. macOS Python cannot open that process. CrossOver.app 26 is a
different Wine, and pointing it at the game's bottle can upgrade the bottle
and break the official launcher. Do not do that.

The bot runs with Wizard101.app's own Wine, in that same bottle, so it and
the game share a wineserver. `mac/run.sh` does that. `mac/run.sh seeds` and
`mac/run.sh pet` pick the mode. It also opens the menu
bar stop. The hook itself is unproven until the game is logged in and
standing in the world: Wine on Apple Silicon is translating the game, and a
memory hook can fail there even though a Windows process starts.

Python 3.13 copies files with CopyFile2. This Wine does not implement it,
and the process aborts. The dialog can name Wizard101; the crashing process
is python.exe, and closing the dialog leaves the game running.
`mac/sitecustomize.py` is placed next to that Python so the copy is a normal
read and write. `mac/run.sh` refreshes that file on each start.
