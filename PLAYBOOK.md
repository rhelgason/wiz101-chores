# Day plan

This is the bot's job while the computer is left alone. The code that
chooses the next action is `src/wiz101_auto/dayplan.py`. If this file and
that module disagree, this file is the intent and the module is what runs,
so change them together.

Energy is the green globe. It is not mana. Pets, gardening, and fishing
spend it. It comes back slowly, and a level-up fills it. A full bar that
sits there is wasted, so a full bar interrupts whatever else is going on.

## Order

1. **Ghulture.** Dance a body that can still be the perfect pet, or the base
   up to adult. Leave 10 energy untouched if a plant is showing a need.
2. **Garden.** Boom shrooms until gardening rank 3, then couch potatoes.
3. **Fishing.** Only while a fishing quest is tracked or `fish` was asked.
4. **A full bar.** If nothing above can spend it, one dance, so a level-up
   refill does not sit there.
5. **Hatch.** The base with the kiosk perfect ghulture. No energy. Gold at
   least 100,000. A hatch that is ready is paid before the barn.
6. **Red Barn Farm.** 125,000 gold, and only when that hatch would still be
   affordable afterwards, once couch potatoes can actually be planted.
7. **Reagents.** Only a tracked "Collect ..." quest, or `reagent "scrap iron"`.
8. **Gold.** Whatever time is left.

`mode: pet` does only the ghulture. `mode: reagents` does only the reagent.
`chores` is this list. An explicit `pet` command dances immediately.

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

Only a ghulture is trained on purpose. Another pet equipped is left alone
unless the energy bar is full and the garden and fishing have nothing to
spend it on. Then one dance spends the refill. Snacks are the first one the
reward screen offers. Couch potatoes replace those with rank 9s (Captain
Canteloupe, Fancy Yogurt, Golden Wheat Bread). Ghultures love death
desserts and death cereals. Fancy Yogurt is the dessert.

The hatch click happens only when the open kiosk page shows a ghulture and
all five talents. A page that does not is left alone, because a wrong click
spends the gold. Nothing is paid to speed the egg.

## 2. Couch potatoes

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

Fish when the tracked quest is a fishing quest, or when `fish` was asked.
`fish --clear` drops the request. Pet and garden still take the energy first.

The cast is the first lure on the fishing bar, which is a spell the wizard
already owns. The reel is one press of space when a fish in the pond takes
the lure. Nothing is written into the fish or the game clock. A cast is
skipped when the pond is empty or the globe is under 2. A few catches, then
the day plan looks again.

## 5. Gold

Gold pays for hatches, the barn, and boom shroom seeds. Farm it only when
energy cannot pay for the pet, the garden, or a fishing quest.

- Need couch potato seeds (under 14), or the wizard is under level 40:
  Splithoof Barbarians, as above. Their junk also vendors.
- Seeds stocked and level 40 or more: Halfang Bristlecrown in Vestrilund,
  Wintertusk. His amulets sell at the Bazaar. That is the gold farm.
- Under level 20: Grizzleheim is closed. There is no seed run yet.

Keep 100,000 gold back while the ghulture is unfinished. That is one hatch.
The barn is 125,000 on top of it. When a hatch is ready and the gold covers
the hatch but not both, the hatch is paid. The barn is bought on the next
stretch of training, once the bank holds the barn and still leaves the
hatch. The housing shop is not mapped, so the barn gold is not spent until that
window has been read on a live client. The decision is made either way.

One fight, then the day plan looks again. The pull is the named mob
(Splithoof Barbarians, Halfang, or a Wizard City street mob under level 20).
The round uses an area hit from the deck already equipped, or one single
hit on an enemy, or it passes. Health under about a third skips the pull.
A full globe is noticed between fights because the fight is only one duel.

## What actually runs

| Action | Runs |
|---|---|
| Dance | Yes. The base to adult, a perfect-stat body to mega, a miss not at all. One extra game when the bar is full and nothing else can spend it. |
| Reagent pickup | Yes, for the one reagent that was asked for. |
| Garden | Yes. Rank and seeds are read from the wizard. A spell is cast only when the plant's text names that need. One boom shroom seed from Farley when that is the next buy. |
| Fishing | Yes, a few casts, when a quest or `fish` asked. Space on the bite. No clock change. |
| Hatch | Yes, one hatch, and only when the open kiosk page shows a ghulture with all five talents. No crowns and no faster egg. |
| Gold fight | Yes, one duel against the named mob, then back to the plan. |
| Red Barn purchase | Decided and logged. The housing shop is not clicked. |

`state/pet.json` remembers the equipped pet, the base, the previous base,
and `confirm_mega`. `state/` is not committed. A missing `state/garden.json`
is read as rank 1 with no seeds and no barn, so the garden work is boom shrooms.

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
the game share a wineserver. `mac/run.sh` does that. It also opens the menu
bar stop. The hook itself is unproven until the game is logged in and
standing in the world: Wine on Apple Silicon is translating the game, and a
memory hook can fail there even though a Windows process starts.

Python 3.13 copies files with CopyFile2. This Wine does not implement it,
and the process aborts. The dialog can name Wizard101; the crashing process
is python.exe, and closing the dialog leaves the game running.
`mac/sitecustomize.py` is placed next to that Python so the copy is a normal
read and write. `mac/run.sh` refreshes that file on each start.
