# Day plan

This is the bot's job while the computer is left alone. The code that
chooses the next action is `src/wiz101_auto/dayplan.py`. If this file and
that module disagree, this file is the intent and the module is what runs,
so change them together.

Energy is the green globe. It is not mana. Pets, gardening, and fishing
spend it. It comes back slowly, and a level-up fills it. A full bar that
sits there is wasted, so a full bar interrupts whatever else is going on.

## Order

1. **Ghulture.** Dance while the project pet is below the stage it needs.
   Leave 10 energy untouched if a plant is showing a need.
2. **Garden.** Boom shrooms until gardening rank 3, then couch potatoes.
3. **Fishing.** Only while a fishing quest is tracked or `fish` was asked.
4. **Hatch.** No energy. When the body is ready and gold is at least 100,000.
5. **Red Barn Farm.** 125,000 gold, and only with the hatch reserve still in
   the bank, once couch potatoes can actually be planted.
6. **Reagents.** Only a tracked "Collect ..." quest, or `reagent "scrap iron"`.
7. **Gold.** Whatever time is left.

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

Train to **adult** (two talents), then hatch that adult with a perfect
ghulture from the kiosk. That is how the pool and the stats climb. A body
that manifests anything outside the five is a miss. Stop at that stage,
including teen. Do not train a miss up to adult, and do not hatch the miss.
Hatch the last clean adult (the base) with the perfect ghulture again.

When an adult is clean and its stats are already at the max, hatch once
more and take **that baby** to mega. If the mega attempt misses, trash it
and hatch the adult base again. The adult base is never trained past adult.

That baby is marked with `"confirm_mega": true` in `state/pet.json`. The
bot does not set the flag, because it does not click the hatch. After the
hatch, put the flag on the new baby. While it is set and the talents stay
inside the five, that body is trained to mega instead of hatched.

Only a ghulture is trained. Another pet equipped means energy is not spent
on it. The first dance reads the kind off the pet window. Snacks are the
first one the reward screen offers; couch potatoes are how those snacks
get replaced with rank 9s (Captain Canteloupe, Fancy Yogurt, Golden Wheat
Bread). Ghultures love death desserts and death cereals. Fancy Yogurt is
the dessert. The other two are still the right tier.

The hatch window is not mapped. The bot will say when to hatch and will
not click through it, because a wrong click spends the gold.

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
`fish --clear` drops the request. School fishing spells spend energy, and
the reel minigame is not mapped. Casting one would spend the energy and
fail the catch, so the bot does not cast. It records that the quest is
waiting. Pet and garden still take the energy first.

## 5. Gold

Gold pays for hatches, the barn, and boom shroom seeds. Farm it only when
energy cannot pay for the pet, the garden, or a fishing quest.

- Need couch potato seeds (under 14), or the wizard is under level 40:
  Splithoof Barbarians, as above. Their junk also vendors.
- Seeds stocked and level 40 or more: Halfang Bristlecrown in Vestrilund,
  Wintertusk. His amulets sell at the Bazaar. That is the gold farm.
- Under level 20: Grizzleheim is closed. There is no seed run yet.

Keep 100,000 gold back while the ghulture is unfinished. The reserve is
for the next hatch, whose price climbs with pedigree.

Combat is not in this tree. The bot names the farm and does not start the
fight. Pulling a mob and then standing in the duel would get the wizard
killed. A fighter gets added when it can clear that one mob and sell, and
it has to notice a full energy bar between pulls and leave.

## What actually runs

| Action | Runs |
|---|---|
| Dance | Yes. Stops at adult, at mega when `confirm_mega` is set, at the first bad talent, or when energy hits the garden reserve. |
| Reagent pickup | Yes, for the one reagent that was asked for. |
| Go home | Yes, when the garden needs care. |
| Hatch, barn purchase, garden spells, fishing casts, gold fights | Decided and logged. Not clicked. |

`state/pet.json` remembers the kind, stage, talents, and `confirm_mega`.
`state/` is not committed. A missing `state/garden.json` is read as rank 1
with no seeds and no barn, so the garden work is boom shrooms.
