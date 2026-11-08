# Glyph capture checklist

Shops you open in game so the price checker can learn the letters it can't read yet.
Each capture becomes a test fixture and the glyph set is re-learned from it.

Status: ✅ added (letters learned) · 📸 captured, waiting to be added · ⬜ still wanted

## Items provided

| Status | Item | Letters it teaches | Capture / fixture | Rows in that shop (for labelling) |
|---|---|---|---|---|
| ✅ | Dexterity Potion | `xt`, `ty` (joined pairs) | `tests/fixtures/shops/shop8.png` | Emerald Earrings 22,000 · Red Cross Earrings 5,000 · (sold) · (sold) · Dexterity Potion 300 · (sold) |
| ✅ | Aquamarine Ore | `q`, `A` | `tests/fixtures/shops/shop9.png` | (sold) · Diamond Ore 300 · Aquamarine Ore 150 · Orihalcon Ore 150 · Topaz Ore 150 · Gold Ore 150 |
| ✅ | Adamantium Ore | `A` on its own | `tests/fixtures/shops/shop10.png` | Adamantium Ore 99 · (empty ×5) |
| ✅ | Elixir | `x` on its own | `tests/fixtures/shops/shop11.png` | Silver Ore 90 · Bronze Ore 90 · (sold) · Elixir 700 · Green Bennis Chainmail 3,500 · (sold) |
| ✅ | Evil Eye Tail + Jr. Necki Skin (same shop) | `y` on its own, `J` | `tests/fixtures/shops/shop12.png` | Bronze Ore 89 · Bronze Ore 89 · Jr. Necki Skin 38 · Evil Eye Tail 34 · Ligator Skin 35 · (sold: Red Malady's Candy) |
| ✅ | Yellow Square + Earring Evasion Scroll (same shop) | `Y` (Evasion has a small v, not `V`) | `tests/fixtures/shops/shop13.png` | Earring LUK Scroll: Inter... 159,999 · Earring Evasion Scroll: Gr... 7,999 · Brown Bamboo Hat 1,999 · Brown Bamboo Hat 1,999 · Yellow Square 5,999 · Dark Nightshift 1,599 |
| ✅ | Yellow Square (second shop, extra sample) | `Y` | `tests/fixtures/shops/shop14.png` | Pig Illustrated 6,499 · Red Hwarang Shirt 5,999 · (sold: Fork on a Stick) · Fork on a Stick 3,999 · Yellow Square 3,999 · Star Earrings 4,999 |
| ✅ | Red Ninja Sandals (same seller, other page) | `j`, also `J` (Jump, Jr.) | `tests/fixtures/shops/shop15.png` | Red Ninja Sandals 999 · (sold: Bronze Koif) · (sold: Green Armine Skirt) · (sold: Green Loosecap) · Pet Equip Jump Scroll: Le... 3,999 · Jr. Necki Skin 33 |
| ✅ | Blue Qi Pao | `Q` | `tests/fixtures/shops/shop16.png` | Dark Master Sergeant 4,199 · Blue Legolier 4,999 · Blue Qi Pao 1,999 · Mithril War Boots 3,999 · Fireman's Axe 6,999 · Old Wooden Staff 4,499 |
| ✅ | Zard | `Z`, also `y` (Accuracy) | `tests/fixtures/shops/shop17.png` | Work Gloves 1,200,000 · Zard 500,000 · Fish Spear 50,000 · Purple Tube 50,000 · Shield Accuracy Scroll: Le... 15,000 · White Choco Bunny 1,500,000 |
| ✅ | Vaulter 2000 + Red Viper (same shop) | `V` | `tests/fixtures/shops/shop18.png` | Vaulter 2000 2,000,000 · (sold: Vaulter 2000) · Zeco 39,999 · Gold Scaler 29,999 · Mithril Scaler 29,999 · Red Viper 29,999 |

## Still wanted

| Status | Letter | Look up | Alternatives |
|---|---|---|---|
| ✅ | — | every letter is learned | |

Known already: every capital and small letter (I and l are read as one); 0–9; `' , - . :`; joined pairs `ty`, `xt`.
Symbols `( ) ! & + / % [ ] < > #` are still unknown but rarely appear in item names.

## Inventory meso digits (small 5-px font, separate from the shop rows)

| Status | Digits | Source | Value shown |
|---|---|---|---|
| ✅ | 1 4 5 6 8 9 and `,` | `shop9.png`, `shop10.png`, `shop18.png` | 611,814 · 599,144 |
| ✅ | 0 2 3 7 | `tests/fixtures/shops/noshop_grind.png` | 602,357 |
