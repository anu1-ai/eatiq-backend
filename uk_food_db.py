"""
EatIQ — Local UK food composition database.
~430 staple UK/AU supermarket grocery items with per-100g nutrition.

Values derived from CoFID / McCance & Widdowson's 'Composition of Foods'
(Public Health England, Open Government Licence v3.0) — the reference
dataset behind NHS UK dietary guidance. Category-level values are used;
brand-specific variance for staples is small.

Lookup order in main.py: cache -> LOCAL (this file) -> Open Food Facts -> USDA.
Local hits are instant, deterministic, and cost zero network calls / tokens.

Entry format (tuple):
  (aliases, display_name, category, kcal, carbs_g, protein_g, fat_g,
   sat_fat_g, sugar_g, fibre_g, salt_g)          # all per 100g (or 100ml)

Aliases are lowercase. Matching (see match()) requires ALL words of at least
one alias to appear in the receipt line — as whole words or as prefixes of
the alias words (handles receipt truncation: 'CHICK BRST' -> 'chicken breast').
Categories use the same keys as the frontend longevity grouping.
"""

import re
from typing import Dict, Optional

V = "vegetables"; F = "fruit"; M = "meat"; FI = "fish"; D = "dairy"
B = "bread"; C = "cereals"; S = "snacks"; DS = "desserts"; A = "alcohol"
SD = "soft_drinks"; BV = "beverages"; CO = "condiments"; FR = "frozen"
RM = "ready_meals"; O = "other"

FOOD_DB = [
    # ── VEGETABLES ────────────────────────────────────────────────────────────
    (("broccoli", "brocolli", "tenderstem"), "Broccoli", V, 34, 7.0, 2.8, 0.4, 0.1, 1.7, 2.6, 0.03),
    (("carrot", "carrots"), "Carrots", V, 41, 9.6, 0.9, 0.2, 0.0, 4.7, 2.8, 0.07),
    (("onion", "onions", "brown onion", "red onion"), "Onions", V, 40, 9.3, 1.1, 0.1, 0.0, 4.2, 1.7, 0.01),
    (("spring onion", "salad onion", "scallion"), "Spring Onions", V, 32, 7.3, 1.8, 0.2, 0.0, 2.3, 2.6, 0.02),
    (("shallot", "shallots", "echalion"), "Shallots", V, 72, 16.8, 2.5, 0.1, 0.0, 7.9, 3.2, 0.03),
    (("tomato", "tomatoes", "toms", "cherry tom", "vine tom", "plum tom", "baby plum"), "Tomatoes", V, 18, 3.9, 0.9, 0.2, 0.0, 2.6, 1.2, 0.01),
    (("pepper", "peppers", "bell pepper", "mixed pepper", "sweet pointed"), "Peppers", V, 26, 6.0, 1.0, 0.3, 0.1, 4.2, 2.1, 0.01),
    (("cucumber", "cucumbr"), "Cucumber", V, 15, 3.6, 0.7, 0.1, 0.0, 1.7, 0.5, 0.01),
    (("courgette", "zucchini"), "Courgette", V, 17, 3.1, 1.2, 0.3, 0.1, 2.5, 1.0, 0.01),
    (("aubergine", "eggplant"), "Aubergine", V, 25, 5.9, 1.0, 0.2, 0.0, 3.5, 3.0, 0.01),
    (("mushroom", "mushrooms", "mush", "chestnut mush", "button mush", "portobello"), "Mushrooms", V, 22, 3.3, 3.1, 0.3, 0.1, 2.0, 1.0, 0.01),
    (("celery",), "Celery", V, 14, 3.0, 0.7, 0.2, 0.0, 1.3, 1.6, 0.20),
    (("leek", "leeks"), "Leeks", V, 61, 14.2, 1.5, 0.3, 0.0, 3.9, 1.8, 0.02),
    (("cabbage", "savoy", "red cabbage", "white cabbage"), "Cabbage", V, 25, 5.8, 1.3, 0.1, 0.0, 3.2, 2.5, 0.05),
    (("cauliflower", "cauli"), "Cauliflower", V, 25, 5.0, 1.9, 0.3, 0.1, 1.9, 2.0, 0.08),
    (("brussels sprout", "sprouts", "brussel"), "Brussels Sprouts", V, 43, 9.0, 3.4, 0.3, 0.1, 2.2, 3.8, 0.06),
    (("asparagus",), "Asparagus", V, 20, 3.9, 2.2, 0.1, 0.0, 1.9, 2.1, 0.01),
    (("sweetcorn", "corn on the cob", "corn cob", "baby corn"), "Sweetcorn", V, 86, 19.0, 3.3, 1.4, 0.2, 3.2, 2.7, 0.04),
    (("parsnip", "parsnips"), "Parsnips", V, 75, 18.0, 1.2, 0.3, 0.1, 4.8, 4.9, 0.03),
    (("beetroot", "beets"), "Beetroot", V, 43, 9.6, 1.6, 0.2, 0.0, 6.8, 2.8, 0.20),
    (("radish", "radishes"), "Radishes", V, 16, 3.4, 0.7, 0.1, 0.0, 1.9, 1.6, 0.10),
    (("spinach", "baby spinach"), "Spinach", V, 23, 3.6, 2.9, 0.4, 0.1, 0.4, 2.2, 0.20),
    (("kale", "cavolo nero"), "Kale", V, 49, 9.0, 4.3, 0.9, 0.1, 2.3, 3.6, 0.10),
    (("lettuce", "iceberg", "romaine", "gem lettuce", "little gem"), "Lettuce", V, 15, 2.9, 1.4, 0.2, 0.0, 0.8, 1.3, 0.03),
    (("salad leaves", "mixed leaves", "mix leaf", "salad bag", "leaf salad", "baby leaf"), "Salad Leaves", V, 17, 2.7, 1.5, 0.3, 0.0, 1.2, 1.5, 0.05),
    (("rocket", "arugula", "wild rocket"), "Rocket", V, 25, 3.7, 2.6, 0.7, 0.1, 2.1, 1.6, 0.07),
    (("watercress",), "Watercress", V, 11, 1.3, 2.3, 0.1, 0.0, 0.2, 0.5, 0.10),
    (("fennel",), "Fennel", V, 31, 7.3, 1.2, 0.2, 0.0, 3.9, 3.1, 0.13),
    (("pumpkin",), "Pumpkin", V, 26, 6.5, 1.0, 0.1, 0.1, 2.8, 0.5, 0.00),
    (("butternut squash", "butternut", "squash"), "Butternut Squash", V, 45, 11.7, 1.0, 0.1, 0.0, 2.2, 2.0, 0.01),
    (("sweet potato", "swt potato", "sweet pot"), "Sweet Potato", V, 86, 20.1, 1.6, 0.1, 0.0, 4.2, 3.0, 0.14),
    (("potato", "potatoes", "pots", "baking pot", "white pot", "maris piper", "king edward", "new pot", "baby pot", "jacket pot", "salad pot", "charlotte pot"), "Potatoes", V, 77, 17.5, 2.0, 0.1, 0.0, 0.8, 2.2, 0.01),
    (("swede",), "Swede", V, 37, 8.6, 1.1, 0.2, 0.0, 4.5, 2.3, 0.02),
    (("turnip",), "Turnip", V, 28, 6.4, 0.9, 0.1, 0.0, 3.8, 1.8, 0.17),
    (("green bean", "green beans", "fine bean", "fine beans"), "Green Beans", V, 31, 7.0, 1.8, 0.2, 0.0, 3.3, 2.7, 0.02),
    (("mangetout", "sugar snap", "snap peas"), "Mangetout / Sugar Snaps", V, 42, 7.5, 2.8, 0.2, 0.0, 4.0, 2.6, 0.01),
    (("peas", "garden peas", "petit pois", "frozen peas"), "Peas", V, 81, 14.5, 5.4, 0.4, 0.1, 5.7, 5.1, 0.01),
    (("mushy peas",), "Mushy Peas", V, 81, 13.5, 5.8, 0.4, 0.1, 2.0, 4.0, 0.45),
    (("edamame", "soya beans"), "Edamame", V, 121, 8.9, 11.9, 5.2, 0.6, 2.2, 5.2, 0.02),
    (("chilli", "chillies", "red chilli", "green chilli"), "Chillies", V, 40, 8.8, 1.9, 0.4, 0.0, 5.3, 1.5, 0.02),
    (("garlic",), "Garlic", V, 149, 33.1, 6.4, 0.5, 0.1, 1.0, 2.1, 0.04),
    (("ginger", "root ginger"), "Ginger", V, 80, 17.8, 1.8, 0.8, 0.2, 1.7, 2.0, 0.03),
    (("coriander", "cilantro"), "Coriander", V, 23, 3.7, 2.1, 0.5, 0.0, 0.9, 2.8, 0.12),
    (("basil",), "Basil", V, 23, 2.7, 3.2, 0.6, 0.0, 0.3, 1.6, 0.01),
    (("parsley",), "Parsley", V, 36, 6.3, 3.0, 0.8, 0.1, 0.9, 3.3, 0.14),
    (("mint", "fresh mint"), "Mint", V, 70, 14.9, 3.8, 0.9, 0.2, 0.0, 8.0, 0.08),
    (("avocado", "avocados", "avo"), "Avocado", V, 160, 8.5, 2.0, 14.7, 2.1, 0.7, 6.7, 0.02),
    (("pak choi", "bok choy"), "Pak Choi", V, 13, 2.2, 1.5, 0.2, 0.0, 1.2, 1.0, 0.16),
    (("stir fry veg", "stirfry veg", "stir-fry veg", "veg medley", "mixed veg"), "Mixed / Stir-fry Vegetables", V, 40, 7.5, 2.2, 0.5, 0.1, 3.5, 2.6, 0.05),
    (("coleslaw mix", "slaw mix"), "Coleslaw Mix (undressed)", V, 30, 6.0, 1.2, 0.2, 0.0, 4.0, 2.4, 0.05),
    (("olives", "green olives", "black olives", "pitted olives"), "Olives", V, 145, 3.8, 1.0, 13.9, 2.0, 0.5, 3.3, 3.10),

    # ── FRUIT ─────────────────────────────────────────────────────────────────
    (("apple", "apples", "gala", "braeburn", "pink lady", "granny smith", "royal gala", "jazz apple"), "Apples", F, 52, 13.8, 0.3, 0.2, 0.0, 10.4, 2.4, 0.00),
    (("banana", "bananas", "fairtrade banana"), "Bananas", F, 89, 22.8, 1.1, 0.3, 0.1, 12.2, 2.6, 0.00),
    (("orange", "oranges", "navel"), "Oranges", F, 47, 11.8, 0.9, 0.1, 0.0, 9.4, 2.4, 0.00),
    (("clementine", "clementines", "satsuma", "mandarin", "tangerine", "easy peeler", "easy peelers"), "Clementines / Easy Peelers", F, 47, 12.0, 0.9, 0.2, 0.0, 9.2, 1.7, 0.00),
    (("grape", "grapes", "red grapes", "green grapes", "seedless grapes", "sable grapes"), "Grapes", F, 69, 18.1, 0.7, 0.2, 0.1, 15.5, 0.9, 0.01),
    (("strawberry", "strawberries", "strawb", "strawbs"), "Strawberries", F, 32, 7.7, 0.7, 0.3, 0.0, 4.9, 2.0, 0.00),
    (("raspberry", "raspberries", "rasps"), "Raspberries", F, 52, 11.9, 1.2, 0.7, 0.0, 4.4, 6.5, 0.00),
    (("blueberry", "blueberries", "blueb"), "Blueberries", F, 57, 14.5, 0.7, 0.3, 0.0, 10.0, 2.4, 0.00),
    (("blackberry", "blackberries"), "Blackberries", F, 43, 9.6, 1.4, 0.5, 0.0, 4.9, 5.3, 0.00),
    (("mango", "mangoes"), "Mango", F, 60, 15.0, 0.8, 0.4, 0.1, 13.7, 1.6, 0.00),
    (("pineapple",), "Pineapple", F, 50, 13.1, 0.5, 0.1, 0.0, 9.9, 1.4, 0.00),
    (("melon", "cantaloupe", "honeydew", "galia"), "Melon", F, 34, 8.2, 0.8, 0.2, 0.1, 7.9, 0.9, 0.02),
    (("watermelon",), "Watermelon", F, 30, 7.6, 0.6, 0.2, 0.0, 6.2, 0.4, 0.00),
    (("peach", "peaches"), "Peaches", F, 39, 9.5, 0.9, 0.3, 0.0, 8.4, 1.5, 0.00),
    (("nectarine", "nectarines"), "Nectarines", F, 44, 10.6, 1.1, 0.3, 0.0, 7.9, 1.7, 0.00),
    (("plum", "plums"), "Plums", F, 46, 11.4, 0.7, 0.3, 0.0, 9.9, 1.4, 0.00),
    (("cherry", "cherries"), "Cherries", F, 63, 16.0, 1.1, 0.2, 0.0, 12.8, 2.1, 0.00),
    (("kiwi", "kiwis", "kiwi fruit"), "Kiwi Fruit", F, 61, 14.7, 1.1, 0.5, 0.0, 9.0, 3.0, 0.00),
    (("lemon", "lemons"), "Lemons", F, 29, 9.3, 1.1, 0.3, 0.0, 2.5, 2.8, 0.00),
    (("lime", "limes"), "Limes", F, 30, 10.5, 0.7, 0.2, 0.0, 1.7, 2.8, 0.00),
    (("grapefruit",), "Grapefruit", F, 42, 10.7, 0.8, 0.1, 0.0, 6.9, 1.6, 0.00),
    (("pomegranate",), "Pomegranate", F, 83, 18.7, 1.7, 1.2, 0.1, 13.7, 4.0, 0.01),
    (("fig", "figs"), "Figs", F, 74, 19.2, 0.8, 0.3, 0.1, 16.3, 2.9, 0.00),
    (("date", "dates", "medjool"), "Dates", F, 277, 75.0, 1.8, 0.2, 0.0, 66.5, 6.7, 0.00),
    (("apricot", "apricots"), "Apricots", F, 48, 11.1, 1.4, 0.4, 0.0, 9.2, 2.0, 0.00),
    (("pear", "pears", "conference"), "Pears", F, 57, 15.2, 0.4, 0.1, 0.0, 9.8, 3.1, 0.00),
    (("papaya", "pawpaw"), "Papaya", F, 43, 10.8, 0.5, 0.3, 0.1, 7.8, 1.7, 0.00),
    (("passion fruit",), "Passion Fruit", F, 97, 23.4, 2.2, 0.7, 0.1, 11.2, 10.4, 0.07),
    (("dried apricot",), "Dried Apricots", F, 241, 62.6, 3.4, 0.5, 0.0, 53.4, 7.3, 0.01),
    (("prunes", "rte prunes", "dried plums", "pitted prunes"), "Prunes", F, 240, 57.0, 2.2, 0.4, 0.1, 38.0, 7.1, 0.01),
    (("raisin", "raisins", "sultana", "sultanas", "currants"), "Raisins / Sultanas", F, 299, 79.2, 3.1, 0.5, 0.1, 59.2, 3.7, 0.03),
    (("mixed berries", "berry mix", "summer fruits", "berries"), "Mixed Berries", F, 45, 10.2, 1.0, 0.4, 0.0, 7.0, 4.0, 0.00),
    (("fruit salad", "fruit medley", "melon medley"), "Fruit Salad", F, 50, 12.0, 0.7, 0.2, 0.0, 10.5, 1.5, 0.01),
    (("coconut", "coconut chunks"), "Coconut (fresh)", F, 354, 15.2, 3.3, 33.5, 29.7, 6.2, 9.0, 0.02),
    (("rhubarb",), "Rhubarb", F, 21, 4.5, 0.9, 0.2, 0.1, 1.1, 1.8, 0.00),
]

FOOD_DB += [
    # ── MEAT & POULTRY ────────────────────────────────────────────────────────
    (("chicken breast", "chick brst", "chkn brst", "chicken fillet", "chicken fillets", "brst fillet", "breast fillet", "chicken brst"), "Chicken Breast", M, 106, 0.0, 24.0, 1.1, 0.3, 0.0, 0.0, 0.10),
    (("chicken thigh", "chicken thighs", "chkn thigh", "thigh fillet"), "Chicken Thighs", M, 176, 0.0, 18.3, 11.5, 3.2, 0.0, 0.0, 0.15),
    (("whole chicken", "chicken whole", "roast chicken", "medium chicken", "large chicken"), "Whole Chicken", M, 213, 0.0, 17.6, 15.8, 4.5, 0.0, 0.0, 0.15),
    (("chicken drumstick", "drumsticks", "chicken leg", "chicken legs"), "Chicken Drumsticks / Legs", M, 161, 0.0, 19.0, 9.5, 2.7, 0.0, 0.0, 0.20),
    (("chicken wing", "chicken wings"), "Chicken Wings", M, 222, 0.0, 18.3, 16.5, 4.6, 0.0, 0.0, 0.25),
    (("chicken mince", "minced chicken"), "Chicken Mince", M, 143, 0.0, 19.0, 7.5, 2.1, 0.0, 0.0, 0.15),
    (("beef mince", "minced beef", "lean mince", "steak mince", "mince beef", "beef minced", "5% mince", "10% mince", "20% mince"), "Beef Mince", M, 184, 0.0, 20.0, 11.7, 5.1, 0.0, 0.0, 0.15),
    (("beef steak", "sirloin", "ribeye", "rib eye", "rump steak", "fillet steak", "frying steak"), "Beef Steak", M, 177, 0.0, 22.5, 9.7, 4.1, 0.0, 0.0, 0.13),
    (("beef roasting joint", "topside", "silverside", "brisket", "beef joint"), "Beef Roasting Joint", M, 158, 0.0, 21.5, 8.0, 3.3, 0.0, 0.0, 0.13),
    (("stewing beef", "braising steak", "casserole beef", "diced beef", "stewing steak"), "Stewing / Diced Beef", M, 146, 0.0, 21.8, 6.5, 2.7, 0.0, 0.0, 0.13),
    (("lamb chop", "lamb chops", "lamb cutlet", "lamb steaks"), "Lamb Chops", M, 260, 0.0, 18.0, 20.9, 9.6, 0.0, 0.0, 0.17),
    (("lamb mince", "minced lamb"), "Lamb Mince", M, 235, 0.0, 18.5, 17.9, 8.3, 0.0, 0.0, 0.17),
    (("lamb leg", "leg of lamb", "lamb joint", "lamb shoulder"), "Lamb Joint", M, 203, 0.0, 19.0, 14.0, 6.5, 0.0, 0.0, 0.17),
    (("pork chop", "pork chops", "pork loin steak", "pork steaks"), "Pork Chops", M, 180, 0.0, 21.8, 10.2, 3.5, 0.0, 0.0, 0.15),
    (("pork mince", "minced pork"), "Pork Mince", M, 190, 0.0, 19.0, 12.5, 4.3, 0.0, 0.0, 0.15),
    (("pork loin", "pork joint", "pork shoulder", "pork belly slices", "pork belly"), "Pork Joint / Belly", M, 260, 0.0, 17.5, 21.0, 7.5, 0.0, 0.0, 0.15),
    (("pork tenderloin", "pork fillet", "pork medallion"), "Pork Tenderloin", M, 122, 0.0, 22.0, 3.5, 1.2, 0.0, 0.0, 0.13),
    (("bacon", "back bacon", "streaky bacon", "smoked bacon", "unsmoked bacon", "bacon rashers", "bacon medallions"), "Bacon", M, 240, 0.5, 16.0, 19.0, 7.0, 0.5, 0.0, 2.90),
    (("sausage", "sausages", "pork sausage", "cumberland", "lincolnshire", "saus", "sausgs", "chipolata", "chipolatas"), "Pork Sausages", M, 285, 9.5, 12.0, 22.0, 8.0, 1.5, 1.0, 1.60),
    (("turkey breast", "turkey mince", "turkey steaks", "turkey fillet"), "Turkey", M, 105, 0.0, 24.0, 1.0, 0.3, 0.0, 0.0, 0.13),
    (("gammon", "gammon joint", "gammon steak"), "Gammon", M, 175, 0.5, 21.0, 10.0, 3.4, 0.5, 0.0, 2.80),
    (("ham", "cooked ham", "sliced ham", "wafer ham", "honey roast ham", "breaded ham"), "Ham (sliced)", M, 107, 1.5, 18.0, 3.3, 1.1, 1.4, 0.0, 2.10),
    (("salami", "pepperoni", "milano salami"), "Salami / Pepperoni", M, 425, 1.5, 22.0, 37.0, 14.0, 1.0, 0.0, 4.00),
    (("chorizo",), "Chorizo", M, 348, 2.0, 24.0, 27.0, 10.5, 1.5, 0.0, 3.30),
    (("pancetta", "lardons", "bacon lardons"), "Pancetta / Lardons", M, 330, 0.5, 15.0, 30.0, 11.0, 0.5, 0.0, 2.70),
    (("prosciutto", "parma ham", "serrano"), "Prosciutto / Cured Ham", M, 220, 0.5, 27.0, 12.0, 4.2, 0.5, 0.0, 4.50),
    (("duck breast", "duck legs", "whole duck"), "Duck", M, 240, 0.0, 18.0, 18.5, 5.2, 0.0, 0.0, 0.15),
    (("burger", "beef burger", "burgers", "quarter pounder", "steak burger"), "Beef Burgers", M, 250, 2.5, 17.5, 19.0, 8.5, 0.5, 0.5, 0.90),
    (("meatball", "meatballs", "beef meatballs", "pork meatballs"), "Meatballs", M, 230, 5.0, 15.5, 16.5, 6.5, 1.0, 0.8, 1.10),
    (("chicken kiev", "chicken kyiv", "kiev"), "Chicken Kiev", M, 250, 12.5, 15.0, 15.5, 6.5, 1.0, 1.0, 0.90),
    (("chicken nugget", "chicken nuggets", "chicken dipper", "chicken goujons"), "Chicken Nuggets / Goujons", M, 265, 15.5, 14.0, 16.0, 2.5, 0.8, 1.0, 0.90),
    (("black pudding",), "Black Pudding", M, 297, 16.5, 10.5, 21.0, 8.5, 1.5, 0.5, 1.80),
    (("liver", "chicken liver", "lambs liver"), "Liver", M, 137, 2.0, 20.5, 5.0, 1.6, 0.0, 0.0, 0.20),
    (("corned beef",), "Corned Beef", M, 205, 1.0, 25.0, 11.5, 5.5, 0.5, 0.0, 2.10),
    (("hot dog", "hot dogs", "frankfurter", "frankfurters"), "Hot Dogs / Frankfurters", M, 270, 3.5, 11.5, 23.5, 8.5, 1.0, 0.5, 2.00),
    (("pigs in blankets",), "Pigs in Blankets", M, 310, 5.0, 14.0, 25.5, 9.5, 1.0, 0.7, 2.00),
    (("venison",), "Venison", M, 103, 0.0, 22.0, 1.6, 0.6, 0.0, 0.0, 0.13),
    (("kebab meat", "doner"), "Kebab Meat", M, 280, 3.5, 16.0, 22.5, 9.5, 1.0, 0.5, 1.80),
    (("scotch egg", "scotch eggs"), "Scotch Egg", M, 260, 13.0, 11.5, 18.0, 4.5, 1.0, 1.0, 1.00),
    (("pork pie",), "Pork Pie", M, 380, 25.0, 10.0, 26.5, 10.0, 1.0, 1.0, 1.30),
    (("sausage roll", "sausage rolls", "saus roll"), "Sausage Roll", M, 340, 24.0, 9.0, 23.0, 10.5, 1.5, 1.5, 1.20),

    # ── FISH & SEAFOOD ────────────────────────────────────────────────────────
    (("salmon fillet", "salmon fillets", "salmon", "salmon portion", "salmon side"), "Salmon", FI, 197, 0.0, 20.4, 12.8, 2.5, 0.0, 0.0, 0.13),
    (("smoked salmon",), "Smoked Salmon", FI, 142, 0.5, 22.5, 5.5, 1.1, 0.0, 0.0, 3.00),
    (("cod fillet", "cod fillets", "cod loin", "cod portion", "cod"), "Cod", FI, 80, 0.0, 18.3, 0.7, 0.1, 0.0, 0.0, 0.20),
    (("haddock", "haddock fillet", "smoked haddock"), "Haddock", FI, 81, 0.0, 19.0, 0.6, 0.1, 0.0, 0.0, 0.30),
    (("tuna steak", "fresh tuna", "tuna"), "Tuna (fresh)", FI, 136, 0.0, 25.0, 4.0, 1.1, 0.0, 0.0, 0.10),
    (("tinned tuna", "tuna chunks", "tuna in brine", "tuna in spring water", "tuna in oil", "tuna can"), "Tinned Tuna", FI, 110, 0.0, 25.0, 1.0, 0.3, 0.0, 0.0, 0.90),
    (("prawn", "prawns", "king prawn", "king prawns", "cooked prawns", "raw prawns", "tiger prawns"), "Prawns", FI, 76, 0.0, 17.6, 0.6, 0.1, 0.0, 0.0, 1.10),
    (("mackerel", "smoked mackerel", "mackerel fillets"), "Mackerel", FI, 268, 0.0, 18.7, 21.5, 4.4, 0.0, 0.0, 0.60),
    (("sardine", "sardines", "tinned sardines"), "Sardines", FI, 200, 0.0, 23.0, 12.0, 3.3, 0.0, 0.0, 0.75),
    (("sea bass", "seabass", "sea bass fillet"), "Sea Bass", FI, 100, 0.0, 19.5, 2.5, 0.6, 0.0, 0.0, 0.15),
    (("sea bream", "bream"), "Sea Bream", FI, 96, 0.0, 19.0, 2.3, 0.6, 0.0, 0.0, 0.15),
    (("trout", "rainbow trout"), "Trout", FI, 127, 0.0, 21.0, 4.8, 1.0, 0.0, 0.0, 0.10),
    (("crab", "crab meat", "crabsticks", "crab sticks", "seafood sticks"), "Crab / Seafood Sticks", FI, 90, 5.0, 12.0, 1.5, 0.3, 1.5, 0.0, 1.20),
    (("mussel", "mussels"), "Mussels", FI, 92, 3.5, 12.0, 2.7, 0.5, 0.0, 0.0, 0.75),
    (("squid", "calamari"), "Squid", FI, 81, 1.2, 15.4, 1.7, 0.4, 0.0, 0.0, 0.30),
    (("scallop", "scallops"), "Scallops", FI, 83, 3.4, 15.0, 1.0, 0.2, 0.0, 0.0, 0.60),
    (("fish finger", "fish fingers", "fish fngrs"), "Fish Fingers", FI, 214, 18.5, 12.5, 9.5, 0.9, 0.8, 1.0, 0.70),
    (("battered fish", "battered cod", "battered haddock", "fish in batter"), "Battered Fish", FI, 230, 15.0, 13.0, 13.0, 1.4, 0.5, 0.8, 0.80),
    (("breaded fish", "breaded cod", "fish cakes", "fishcakes", "fish cake"), "Fish Cakes / Breaded Fish", FI, 200, 18.0, 10.5, 9.5, 1.0, 1.0, 1.2, 0.85),
    (("anchovies", "anchovy"), "Anchovies", FI, 210, 0.0, 25.0, 12.0, 2.7, 0.0, 0.0, 9.30),
    (("kipper", "kippers"), "Kippers", FI, 229, 0.0, 20.0, 16.5, 3.2, 0.0, 0.0, 2.50),
    (("tinned salmon", "pink salmon can", "red salmon can"), "Tinned Salmon", FI, 153, 0.0, 21.5, 7.5, 1.6, 0.0, 0.0, 0.85),

    # ── DAIRY & EGGS ──────────────────────────────────────────────────────────
    (("semi skimmed milk", "s/skim milk", "sskim milk", "semi-skimmed", "semi skim", "ss milk", "semi milk", "milk"), "Semi-Skimmed Milk", D, 50, 4.8, 3.6, 1.8, 1.1, 4.8, 0.0, 0.10),
    (("whole milk", "whl milk", "full fat milk", "blue milk"), "Whole Milk", D, 64, 4.7, 3.4, 3.6, 2.3, 4.7, 0.0, 0.10),
    (("skimmed milk", "skim milk", "skmd milk", "red milk"), "Skimmed Milk", D, 35, 5.0, 3.5, 0.1, 0.1, 5.0, 0.0, 0.10),
    (("goats milk", "goat milk"), "Goats Milk", D, 62, 4.4, 3.1, 3.7, 2.4, 4.4, 0.0, 0.12),
    (("cheddar", "mature cheddar", "mild cheddar", "extra mature", "cheddar cheese", "grated cheddar", "cathedral city"), "Cheddar Cheese", D, 416, 0.1, 25.4, 34.9, 21.7, 0.1, 0.0, 1.80),
    (("mozzarella", "grated mozzarella", "mozz"), "Mozzarella", D, 280, 2.0, 18.5, 22.0, 14.0, 1.0, 0.0, 0.50),
    (("parmesan", "grana padano", "parmigiano"), "Parmesan", D, 415, 0.5, 36.0, 30.0, 19.5, 0.5, 0.0, 1.60),
    (("brie",), "Brie", D, 340, 0.5, 20.0, 29.0, 18.0, 0.5, 0.0, 1.50),
    (("camembert",), "Camembert", D, 300, 0.5, 21.0, 24.0, 15.0, 0.5, 0.0, 1.50),
    (("feta", "greek salad cheese"), "Feta", D, 270, 1.5, 15.5, 22.5, 15.0, 1.5, 0.0, 2.70),
    (("halloumi",), "Halloumi", D, 330, 2.0, 22.0, 26.0, 17.0, 1.5, 0.0, 2.80),
    (("goats cheese", "goat cheese", "chevre"), "Goats Cheese", D, 320, 1.0, 18.5, 27.0, 18.5, 1.0, 0.0, 1.50),
    (("stilton", "blue cheese", "gorgonzola", "danish blue"), "Blue Cheese / Stilton", D, 410, 0.5, 23.5, 35.0, 22.5, 0.5, 0.0, 2.00),
    (("red leicester",), "Red Leicester", D, 400, 0.1, 24.0, 33.5, 21.0, 0.1, 0.0, 1.70),
    (("edam", "gouda"), "Edam / Gouda", D, 340, 0.1, 25.0, 26.0, 16.5, 0.1, 0.0, 2.00),
    (("cottage cheese",), "Cottage Cheese", D, 100, 4.5, 12.0, 4.0, 2.5, 4.0, 0.0, 0.55),
    (("cream cheese", "soft cheese", "philadelphia"), "Cream Cheese", D, 240, 4.0, 6.0, 22.0, 14.5, 4.0, 0.0, 0.75),
    (("babybel", "cheese snack", "cheestrings", "cheese strings"), "Cheese Snacks", D, 300, 1.0, 21.0, 23.5, 15.5, 1.0, 0.0, 1.80),
    (("greek yogurt", "greek yoghurt", "grk yog", "greek style yog", "greek style yoghurt", "0% greek"), "Greek Yogurt", D, 97, 4.0, 9.0, 5.0, 3.3, 4.0, 0.0, 0.13),
    (("natural yogurt", "natural yoghurt", "nat yog", "plain yogurt"), "Natural Yogurt", D, 61, 4.7, 4.8, 3.0, 1.9, 4.7, 0.0, 0.13),
    (("fruit yogurt", "fruit yoghurt", "strawberry yog", "yogurt", "yoghurt", "yog"), "Fruit Yogurt", D, 94, 13.5, 3.8, 2.8, 1.8, 12.5, 0.2, 0.15),
    (("skyr",), "Skyr", D, 63, 4.0, 11.0, 0.2, 0.1, 4.0, 0.0, 0.10),
    (("kefir",), "Kefir", D, 60, 4.5, 3.5, 3.0, 2.0, 4.5, 0.0, 0.10),
    (("fromage frais", "petits filous", "kids yogurt"), "Fromage Frais", D, 105, 13.0, 5.5, 3.5, 2.3, 12.0, 0.0, 0.10),
    (("butter", "salted butter", "unsalted butter", "lurpak", "anchor butter"), "Butter", D, 744, 0.6, 0.6, 82.2, 52.1, 0.6, 0.0, 1.70),
    (("spreadable butter", "butter spreadable", "lurpak spreadable"), "Spreadable Butter", D, 700, 0.6, 0.5, 77.0, 40.0, 0.6, 0.0, 1.30),
    (("margarine", "flora", "vitalite", "spread", "sunflower spread", "olive spread"), "Margarine / Spread", D, 540, 1.0, 0.2, 59.0, 14.0, 0.5, 0.0, 1.10),
    (("double cream", "dbl cream"), "Double Cream", D, 445, 2.7, 1.6, 47.5, 29.7, 2.7, 0.0, 0.05),
    (("single cream", "sgl cream"), "Single Cream", D, 193, 4.1, 3.3, 18.0, 11.2, 4.1, 0.0, 0.08),
    (("whipping cream",), "Whipping Cream", D, 370, 3.0, 2.0, 38.5, 24.0, 3.0, 0.0, 0.06),
    (("creme fraiche", "crème fraîche"), "Crème Fraîche", D, 300, 2.5, 2.3, 30.0, 20.5, 2.5, 0.0, 0.08),
    (("soured cream", "sour cream"), "Soured Cream", D, 190, 3.5, 2.7, 18.0, 11.5, 3.5, 0.0, 0.10),
    (("clotted cream",), "Clotted Cream", D, 570, 2.3, 1.6, 63.5, 40.0, 2.3, 0.0, 0.05),
    (("squirty cream", "aerosol cream"), "Squirty Cream", D, 300, 12.0, 2.0, 26.0, 17.0, 11.0, 0.0, 0.10),
    (("eggs", "egg", "free range eggs", "large eggs", "medium eggs", "mixed eggs", "6 eggs", "12 eggs", "eggs 6", "eggs 12", "f/r eggs", "fr eggs"), "Eggs", D, 131, 0.0, 12.6, 9.0, 2.5, 0.0, 0.0, 0.35),
    (("custard", "fresh custard", "custard pot"), "Custard", D, 100, 15.5, 2.9, 3.0, 1.9, 11.0, 0.1, 0.13),
    (("quark",), "Quark", D, 65, 4.0, 11.0, 0.3, 0.2, 4.0, 0.0, 0.08),
    (("rice pudding",), "Rice Pudding", D, 95, 15.5, 3.2, 2.3, 1.5, 8.5, 0.1, 0.12),
]

FOOD_DB += [
    # ── BREAD & BAKERY ────────────────────────────────────────────────────────
    (("white bread", "white loaf", "wht bread", "medium white", "thick white", "toastie white", "soft white"), "White Bread", B, 240, 45.5, 8.5, 1.8, 0.4, 3.5, 2.5, 0.90),
    (("wholemeal bread", "whlemeal", "wholemeal loaf", "wm bread", "brown bread", "brown loaf", "wholewheat bread"), "Wholemeal Bread", B, 220, 38.0, 9.5, 2.5, 0.5, 2.8, 6.5, 0.90),
    (("seeded bread", "seeded loaf", "granary", "multiseed", "multigrain bread", "50/50 bread"), "Seeded / Granary Bread", B, 260, 40.0, 10.5, 5.5, 0.8, 3.0, 6.0, 0.90),
    (("sourdough",), "Sourdough", B, 245, 47.5, 8.5, 1.5, 0.3, 2.0, 2.7, 1.10),
    (("baguette", "french stick", "petit pain"), "Baguette", B, 265, 53.0, 9.5, 1.5, 0.3, 2.5, 2.8, 1.20),
    (("ciabatta",), "Ciabatta", B, 270, 50.0, 9.0, 3.5, 0.6, 1.5, 2.8, 1.20),
    (("bread rolls", "white rolls", "bread roll", "batch rolls", "finger rolls", "brioche buns", "burger buns", "hot dog rolls"), "Bread Rolls / Buns", B, 280, 50.0, 9.0, 4.5, 1.2, 6.0, 2.5, 0.85),
    (("bagel", "bagels"), "Bagels", B, 270, 51.5, 10.5, 1.8, 0.4, 5.5, 2.7, 0.95),
    (("pitta", "pitta bread", "pita"), "Pitta Bread", B, 255, 51.0, 9.0, 1.3, 0.2, 2.0, 2.8, 0.95),
    (("naan", "naan bread", "garlic naan", "plain naan"), "Naan Bread", B, 290, 47.0, 8.5, 7.5, 1.2, 4.0, 2.5, 0.95),
    (("tortilla wrap", "wraps", "tortillas", "wholemeal wraps", "plain wraps", "wrap"), "Tortilla Wraps", B, 300, 51.0, 8.5, 6.5, 2.8, 3.0, 2.8, 1.10),
    (("flatbread", "fbread", "folded flatbread"), "Flatbread", B, 280, 50.0, 9.0, 4.5, 1.5, 3.0, 2.5, 1.00),
    (("croissant", "croissants", "all butter croissant"), "Croissants", B, 400, 42.0, 8.0, 22.0, 13.5, 8.0, 2.5, 0.90),
    (("pain au chocolat", "chocolate croissant"), "Pain au Chocolat", B, 420, 45.0, 7.5, 23.0, 14.0, 13.0, 2.5, 0.75),
    (("english muffin", "muffins white", "breakfast muffins"), "English Muffins", B, 225, 43.0, 9.5, 1.8, 0.3, 3.5, 2.6, 0.95),
    (("crumpet", "crumpets"), "Crumpets", B, 180, 36.5, 5.5, 0.8, 0.2, 2.5, 2.3, 1.30),
    (("scone", "scones", "fruit scone"), "Scones", B, 360, 55.0, 7.0, 12.5, 6.5, 15.0, 2.2, 1.00),
    (("teacake", "teacakes", "hot cross bun", "hot cross buns"), "Teacakes / Hot Cross Buns", B, 300, 55.0, 8.0, 5.5, 1.8, 18.0, 2.8, 0.60),
    (("garlic bread", "garlic baguette", "garlic slices"), "Garlic Bread", B, 350, 42.0, 8.0, 16.5, 7.5, 2.5, 2.5, 1.10),
    (("brioche", "brioche loaf"), "Brioche", B, 375, 51.0, 8.5, 15.0, 9.5, 12.5, 2.0, 0.85),
    (("malt loaf",), "Malt Loaf", B, 300, 64.0, 7.5, 1.5, 0.4, 30.0, 3.5, 0.45),
    (("banana bread", "banana loaf"), "Banana Bread", B, 350, 52.0, 5.5, 13.5, 3.5, 30.0, 1.8, 0.55),
    (("chapati", "chapatti", "roti"), "Chapati / Roti", B, 280, 48.0, 8.0, 6.5, 1.0, 2.0, 5.5, 0.65),
    (("part baked", "bake at home"), "Part-Baked Bread", B, 255, 50.0, 8.5, 2.0, 0.4, 2.5, 2.7, 1.05),

    # ── CEREALS & GRAINS ──────────────────────────────────────────────────────
    (("porridge oats", "rolled oats", "oats", "jumbo oats", "scottish oats", "quaker oats"), "Porridge Oats", C, 375, 60.0, 11.0, 8.0, 1.5, 1.0, 9.0, 0.01),
    (("instant porridge", "oat so simple", "porridge pot", "oats so simple"), "Instant Porridge", C, 380, 62.0, 10.0, 8.0, 1.5, 8.5, 8.0, 0.25),
    (("weetabix", "wheat biscuits", "wheat bisks"), "Weetabix / Wheat Biscuits", C, 360, 69.0, 12.0, 2.0, 0.6, 4.4, 10.0, 0.28),
    (("cornflakes", "corn flakes"), "Cornflakes", C, 380, 84.0, 7.0, 0.9, 0.2, 8.0, 3.0, 1.13),
    (("rice krispies", "rice pops", "rice snaps"), "Rice Krispies", C, 385, 87.0, 6.0, 1.0, 0.3, 10.0, 1.5, 1.00),
    (("branflakes", "bran flakes"), "Bran Flakes", C, 355, 67.0, 10.0, 2.0, 0.4, 18.0, 15.0, 0.90),
    (("muesli", "fruit muesli", "swiss muesli"), "Muesli", C, 365, 62.0, 9.5, 6.0, 1.0, 16.0, 8.0, 0.10),
    (("granola", "oat granola", "honey granola"), "Granola", C, 450, 60.0, 9.0, 17.0, 4.0, 18.5, 7.0, 0.10),
    (("shreddies",), "Shreddies", C, 370, 73.0, 10.0, 1.9, 0.4, 12.5, 9.5, 0.65),
    (("cheerios",), "Cheerios", C, 380, 74.0, 8.0, 3.9, 0.9, 17.5, 7.0, 0.85),
    (("coco pops", "choco pops", "choc cereal"), "Coco Pops", C, 385, 84.0, 5.5, 2.5, 1.2, 17.0, 2.5, 0.65),
    (("frosties", "frosted flakes"), "Frosties", C, 375, 87.0, 4.5, 0.6, 0.2, 37.0, 2.0, 0.75),
    (("special k",), "Special K", C, 375, 76.0, 14.0, 1.5, 0.4, 15.0, 6.0, 0.75),
    (("shredded wheat",), "Shredded Wheat", C, 360, 68.0, 12.0, 2.2, 0.5, 0.7, 12.0, 0.05),
    (("basmati rice", "basmati", "white rice", "long grain rice", "rice", "jasmine rice"), "White Rice (dry)", C, 355, 78.0, 7.5, 0.9, 0.2, 0.1, 1.5, 0.01),
    (("brown rice", "wholegrain rice"), "Brown Rice (dry)", C, 350, 72.0, 7.8, 2.8, 0.6, 1.0, 3.8, 0.01),
    (("microwave rice", "micro rice", "pouch rice", "golden vegetable rice", "egg fried rice pouch", "pilau rice pouch"), "Microwave Rice Pouch", C, 145, 28.5, 3.2, 1.8, 0.4, 0.5, 1.2, 0.25),
    (("pasta", "penne", "fusilli", "spaghetti", "rigatoni", "tagliatelle", "linguine", "macaroni", "conchiglie", "orzo"), "Pasta (dry)", C, 355, 71.0, 12.5, 1.8, 0.4, 3.5, 3.0, 0.01),
    (("wholewheat pasta", "wholemeal pasta", "whole wheat penne"), "Wholewheat Pasta (dry)", C, 340, 62.0, 13.5, 2.5, 0.5, 3.0, 8.5, 0.01),
    (("fresh pasta", "fresh tagliatelle", "fresh ravioli", "tortellini", "ravioli", "fresh gnocchi", "gnocchi"), "Fresh Pasta / Gnocchi", C, 180, 32.0, 7.0, 2.5, 0.8, 1.5, 2.0, 0.45),
    (("lasagne sheets",), "Lasagne Sheets (dry)", C, 350, 70.0, 12.0, 2.0, 0.5, 3.0, 3.0, 0.02),
    (("noodles", "egg noodles", "rice noodles", "instant noodles", "ramen noodles"), "Noodles (dry)", C, 350, 70.0, 11.0, 2.0, 0.5, 1.5, 3.0, 0.40),
    (("couscous", "cous cous"), "Couscous (dry)", C, 360, 73.0, 12.5, 1.5, 0.3, 1.5, 5.0, 0.02),
    (("quinoa",), "Quinoa (dry)", C, 368, 64.0, 14.0, 6.0, 0.7, 2.0, 7.0, 0.01),
    (("bulgur wheat", "bulgar"), "Bulgur Wheat (dry)", C, 340, 70.0, 12.0, 1.5, 0.3, 1.0, 8.5, 0.02),
    (("pearl barley",), "Pearl Barley (dry)", C, 350, 74.0, 10.0, 1.2, 0.3, 0.8, 8.5, 0.01),
    (("polenta",), "Polenta (dry)", C, 355, 76.0, 8.0, 1.5, 0.3, 0.6, 2.0, 0.01),
    (("plain flour", "self raising flour", "self-raising", "sr flour", "strong flour", "bread flour", "flour"), "Flour", C, 350, 72.0, 10.5, 1.4, 0.2, 1.0, 3.5, 0.01),
    (("wholemeal flour",), "Wholemeal Flour", C, 330, 62.0, 12.5, 2.2, 0.4, 1.5, 10.0, 0.01),
    (("cornflour", "corn starch"), "Cornflour", C, 355, 88.0, 0.5, 0.1, 0.0, 0.0, 0.5, 0.01),

    # ── SNACKS ────────────────────────────────────────────────────────────────
    (("crisps", "crsp", "ready salted", "cheese and onion crisps", "salt vinegar", "walkers", "kettle chips", "pringles", "doritos", "tortilla chips", "multipack crisps"), "Crisps", S, 520, 52.0, 6.0, 31.0, 3.0, 1.5, 4.0, 1.30),
    (("popcorn", "sweet popcorn", "salted popcorn", "propercorn"), "Popcorn", S, 480, 55.0, 8.0, 23.0, 2.5, 15.0, 9.5, 0.90),
    (("pretzels",), "Pretzels", S, 380, 78.0, 10.0, 3.5, 0.5, 3.0, 3.5, 3.00),
    (("rice cakes", "rice cake"), "Rice Cakes", S, 380, 81.0, 8.0, 3.0, 0.6, 0.7, 3.0, 0.30),
    (("oatcakes", "oat cakes"), "Oatcakes", S, 440, 60.0, 10.5, 17.0, 3.5, 1.5, 7.5, 1.10),
    (("crackers", "cream crackers", "ritz", "jacobs", "water biscuits"), "Crackers", S, 440, 66.0, 9.5, 15.0, 2.5, 3.5, 3.5, 1.20),
    (("crackerbread", "crispbread", "ryvita", "crisp bread"), "Crispbread / Crackerbread", S, 380, 70.0, 10.0, 4.5, 0.7, 2.5, 12.0, 0.90),
    (("cheese crackers", "mini cheddars", "cheese biscuits"), "Cheese Crackers", S, 500, 53.0, 11.0, 26.5, 12.0, 3.0, 3.0, 1.90),
    (("breadsticks", "grissini"), "Breadsticks", S, 400, 70.0, 12.0, 8.0, 1.5, 3.0, 4.0, 1.40),
    (("nuts", "mixed nuts", "peanuts", "salted peanuts", "roasted nuts", "kp nuts", "dry roasted"), "Nuts (mixed / peanuts)", S, 600, 8.0, 25.0, 50.0, 7.5, 4.5, 7.0, 0.60),
    (("cashews", "cashew nuts"), "Cashew Nuts", S, 585, 20.0, 20.5, 47.5, 9.5, 5.5, 3.0, 0.30),
    (("almonds",), "Almonds", S, 610, 6.5, 21.5, 53.5, 4.2, 4.0, 10.5, 0.01),
    (("walnuts",), "Walnuts", S, 690, 3.3, 15.0, 68.5, 6.0, 2.6, 6.5, 0.01),
    (("pistachios",), "Pistachios", S, 585, 17.0, 20.0, 46.5, 5.7, 7.5, 10.0, 0.55),
    (("trail mix", "fruit and nut mix", "fruit nut"), "Trail Mix / Fruit & Nut", S, 480, 35.0, 14.0, 32.0, 5.5, 27.0, 6.0, 0.10),
    (("seeds", "pumpkin seeds", "sunflower seeds", "mixed seeds", "chia seeds"), "Seeds (mixed)", S, 560, 14.0, 24.0, 45.0, 6.0, 1.5, 8.5, 0.02),
    (("cereal bar", "cereal bars", "nutrigrain", "tracker bar", "flapjack bar"), "Cereal Bars", S, 420, 62.0, 6.0, 15.5, 4.5, 27.0, 4.5, 0.35),
    (("protein bar", "grenade bar", "protein snack"), "Protein Bar", S, 370, 25.0, 30.0, 14.0, 6.0, 4.0, 8.5, 0.50),
    (("nakd bar", "fruit bar", "date bar", "energy ball"), "Fruit & Nut Bar", S, 400, 50.0, 8.0, 17.5, 3.0, 42.0, 6.0, 0.05),
    (("hummus", "houmous", "hummous", "red pepper hummus"), "Hummus", S, 300, 11.0, 7.5, 25.0, 2.6, 1.0, 5.5, 0.75),
    (("guacamole", "guac"), "Guacamole", S, 175, 5.5, 1.8, 16.0, 3.2, 1.5, 4.0, 0.65),
    (("salsa", "tomato salsa"), "Salsa", S, 45, 8.0, 1.3, 0.5, 0.1, 6.0, 1.5, 1.00),
    (("tzatziki",), "Tzatziki", S, 120, 4.5, 4.5, 9.5, 5.5, 3.5, 0.5, 0.75),
    (("sour cream dip", "onion dip", "sour cream and chive"), "Sour Cream & Chive Dip", S, 210, 6.5, 3.0, 19.0, 10.0, 4.0, 0.5, 0.85),
    (("pork scratchings",), "Pork Scratchings", S, 600, 0.5, 47.5, 45.5, 16.5, 0.5, 0.3, 2.50),
    (("bombay mix",), "Bombay Mix", S, 500, 33.0, 18.5, 33.0, 4.0, 5.0, 8.0, 1.10),

    # ── DESSERTS & CONFECTIONERY ──────────────────────────────────────────────
    (("chocolate bar", "milk chocolate", "dairy milk", "galaxy", "choc bar", "twirl", "wispa", "kitkat", "kit kat", "mars bar", "snickers", "bounty", "twix", "aero"), "Chocolate Bar", DS, 530, 57.0, 7.0, 30.0, 18.0, 55.5, 2.0, 0.24),
    (("dark chocolate", "70% chocolate", "85% chocolate"), "Dark Chocolate", DS, 560, 35.0, 8.0, 42.0, 25.0, 27.0, 10.5, 0.02),
    (("white chocolate", "milkybar"), "White Chocolate", DS, 550, 58.0, 7.5, 31.5, 19.5, 58.0, 0.5, 0.25),
    (("chocolate buttons", "giant buttons", "minstrels", "m&ms", "smarties", "maltesers"), "Chocolate Sweets", DS, 500, 65.0, 6.0, 23.5, 14.5, 62.0, 1.5, 0.25),
    (("sweets", "haribo", "gummies", "jelly babies", "wine gums", "fruit pastilles", "skittles", "pick n mix"), "Sweets / Gummies", DS, 340, 79.0, 5.0, 0.2, 0.1, 55.0, 0.1, 0.07),
    (("biscuits", "digestives", "rich tea", "hobnobs", "bourbon", "custard cream", "shortbread", "biscuit"), "Biscuits", DS, 480, 65.0, 6.5, 21.5, 10.5, 25.0, 3.0, 0.85),
    (("chocolate digestives", "chocolate biscuits", "choc digestive", "chocolate hobnobs"), "Chocolate Biscuits", DS, 495, 62.0, 6.5, 24.0, 12.5, 30.0, 3.0, 0.75),
    (("cookies", "chocolate chip cookies", "cookie"), "Cookies", DS, 490, 63.5, 5.5, 24.0, 12.0, 33.0, 2.5, 0.65),
    (("ice cream", "vanilla ice cream", "ben and jerry", "haagen", "carte dor", "icecream"), "Ice Cream", DS, 210, 24.0, 3.5, 11.0, 7.0, 21.0, 0.5, 0.15),
    (("magnum", "cornetto", "solero", "ice lolly", "ice lollies", "lollies", "feast", "twister"), "Ice Cream Bars / Lollies", DS, 280, 28.0, 3.5, 17.0, 12.0, 25.5, 0.7, 0.10),
    (("sorbet",), "Sorbet", DS, 125, 31.0, 0.3, 0.1, 0.0, 26.0, 0.3, 0.02),
    (("cake", "victoria sponge", "chocolate cake", "birthday cake", "sponge cake", "madeira cake"), "Cake", DS, 400, 52.0, 5.0, 19.0, 6.5, 36.0, 1.3, 0.55),
    (("mr kipling", "mr kip", "french fancies", "bakewell", "battenberg", "angel slices", "mini rolls"), "Cake Slices / Fancies", DS, 415, 60.0, 4.0, 17.5, 8.0, 45.0, 1.2, 0.40),
    (("brownie", "brownies", "chocolate brownie"), "Brownies", DS, 450, 52.0, 5.5, 24.5, 9.5, 40.0, 2.5, 0.45),
    (("doughnut", "donut", "doughnuts", "jam doughnut", "glazed doughnut"), "Doughnuts", DS, 380, 45.0, 6.0, 19.5, 7.0, 17.5, 2.0, 0.65),
    (("muffin choc", "blueberry muffin", "chocolate muffin", "muffin"), "Muffins (sweet)", DS, 400, 50.0, 5.5, 19.5, 4.5, 29.0, 1.5, 0.60),
    (("cheesecake",), "Cheesecake", DS, 350, 30.0, 5.0, 23.0, 13.5, 22.0, 0.8, 0.40),
    (("trifle",), "Trifle", DS, 175, 22.0, 2.5, 8.5, 5.5, 17.0, 0.5, 0.15),
    (("profiteroles", "eclair", "eclairs", "choux"), "Profiteroles / Éclairs", DS, 380, 26.5, 5.5, 28.0, 16.5, 18.0, 0.8, 0.30),
    (("apple pie", "fruit pie", "apple crumble", "crumble"), "Fruit Pie / Crumble", DS, 280, 40.0, 3.0, 12.0, 5.5, 20.0, 1.8, 0.35),
    (("tiramisu", "panna cotta"), "Tiramisu / Panna Cotta", DS, 300, 30.0, 4.5, 18.0, 11.5, 24.0, 0.5, 0.15),
    (("mousse", "chocolate mousse"), "Chocolate Mousse", DS, 190, 22.0, 4.0, 9.5, 6.0, 19.5, 0.8, 0.15),
    (("jelly", "jelly pots"), "Jelly", DS, 62, 15.0, 0.1, 0.0, 0.0, 14.5, 0.0, 0.05),
    (("meringue", "meringue nest", "pavlova"), "Meringue", DS, 380, 94.0, 4.5, 0.0, 0.0, 94.0, 0.0, 0.10),
    (("fudge", "toffee", "caramels"), "Fudge / Toffee", DS, 440, 78.0, 2.0, 14.0, 8.5, 70.0, 0.2, 0.30),
    (("popcorn toffee", "toffee popcorn", "butterkist"), "Toffee Popcorn", DS, 430, 74.0, 4.0, 13.0, 6.5, 47.0, 4.5, 0.55),
]

FOOD_DB += [
    # ── ALCOHOL ───────────────────────────────────────────────────────────────
    (("lager", "carling", "fosters", "carlsberg", "stella", "stella artois", "kronenbourg", "peroni", "heineken", "corona", "budweiser", "beck", "san miguel"), "Lager", A, 43, 3.3, 0.4, 0.0, 0.0, 0.1, 0.0, 0.01),
    (("ale", "bitter", "pale ale", "ipa", "brewdog", "punk ipa", "guinness", "stout", "porter", "london pride"), "Ale / Stout", A, 42, 3.2, 0.5, 0.0, 0.0, 0.2, 0.0, 0.01),
    (("cider", "strongbow", "kopparberg", "magners", "thatchers", "rekorderlig"), "Cider", A, 50, 3.0, 0.1, 0.0, 0.0, 2.8, 0.0, 0.01),
    (("red wine", "cabernet", "merlot", "malbec", "shiraz", "rioja", "pinot noir", "syrah", "chianti", "montepulciano"), "Red Wine", A, 85, 2.6, 0.1, 0.0, 0.0, 0.6, 0.0, 0.01),
    (("white wine", "sauvignon", "chardonnay", "pinot grigio", "riesling", "chenin"), "White Wine", A, 82, 2.6, 0.1, 0.0, 0.0, 1.0, 0.0, 0.01),
    (("rose wine", "rosé", "rose"), "Rosé Wine", A, 83, 2.5, 0.1, 0.0, 0.0, 1.4, 0.0, 0.01),
    (("prosecco", "cava", "sparkling wine"), "Prosecco / Sparkling", A, 80, 3.0, 0.1, 0.0, 0.0, 3.0, 0.0, 0.01),
    (("champagne",), "Champagne", A, 82, 1.4, 0.1, 0.0, 0.0, 1.4, 0.0, 0.01),
    (("vodka", "smirnoff", "absolut"), "Vodka", A, 231, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("gin", "gordons", "bombay sapphire", "tanqueray", "hendricks"), "Gin", A, 231, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("whisky", "whiskey", "bourbon", "jack daniels", "jameson", "famous grouse", "glenmorangie", "scotch"), "Whisky", A, 250, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("rum", "captain morgan", "bacardi", "kraken", "malibu"), "Rum", A, 231, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("tequila",), "Tequila", A, 231, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("brandy", "cognac"), "Brandy / Cognac", A, 231, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("liqueur", "baileys", "kahlua", "cointreau", "amaretto", "aperol", "campari", "pimms"), "Liqueur", A, 250, 20.0, 0.5, 0.0, 0.0, 19.0, 0.0, 0.02),
    (("premixed", "pre-mixed", "gin and tonic can", "gordons g&t", "smirnoff ice", "wkd"), "Premixed Drinks (can)", A, 55, 5.5, 0.0, 0.0, 0.0, 4.5, 0.0, 0.01),
    (("port", "sherry", "vermouth", "martini"), "Port / Sherry / Vermouth", A, 155, 12.0, 0.2, 0.0, 0.0, 10.0, 0.0, 0.01),

    # ── SOFT DRINKS ───────────────────────────────────────────────────────────
    (("coca cola", "coca-cola", "coke", "pepsi", "cola"), "Cola (regular)", SD, 42, 10.6, 0.0, 0.0, 0.0, 10.6, 0.0, 0.01),
    (("diet coke", "coke zero", "coca cola zero", "pepsi max", "diet pepsi"), "Diet Cola", SD, 1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("lemonade", "sprite", "7up", "seven up"), "Lemonade (regular)", SD, 40, 9.5, 0.0, 0.0, 0.0, 9.5, 0.0, 0.01),
    (("diet lemonade", "sprite zero", "7up free"), "Diet Lemonade", SD, 1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("fanta", "orangeade", "tango"), "Orangeade / Fanta", SD, 35, 8.5, 0.0, 0.0, 0.0, 8.5, 0.0, 0.01),
    (("irn bru", "dr pepper", "cream soda", "ginger beer"), "Flavoured Fizzy Drinks", SD, 42, 10.5, 0.0, 0.0, 0.0, 10.5, 0.0, 0.03),
    (("tonic water", "tonic", "slimline tonic"), "Tonic Water", SD, 18, 4.4, 0.0, 0.0, 0.0, 4.4, 0.0, 0.01),
    (("soda water", "sparkling water", "san pellegrino water", "perrier"), "Sparkling / Soda Water", SD, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("still water", "bottled water", "evian", "buxton", "highland spring", "volvic"), "Still Water", SD, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("orange juice", "oj", "smooth orange", "with bits orange"), "Orange Juice", SD, 45, 10.5, 0.7, 0.1, 0.0, 8.9, 0.1, 0.01),
    (("apple juice",), "Apple Juice", SD, 46, 11.2, 0.1, 0.1, 0.0, 10.6, 0.2, 0.01),
    (("cranberry juice", "ocean spray"), "Cranberry Juice", SD, 48, 12.0, 0.0, 0.1, 0.0, 11.5, 0.1, 0.01),
    (("pineapple juice",), "Pineapple Juice", SD, 53, 12.7, 0.4, 0.1, 0.0, 10.2, 0.2, 0.01),
    (("tomato juice",), "Tomato Juice", SD, 17, 3.9, 0.7, 0.1, 0.0, 2.6, 0.4, 0.30),
    (("smoothie", "innocent smoothie", "fruit smoothie"), "Fruit Smoothie", SD, 55, 12.5, 0.7, 0.2, 0.0, 11.0, 1.0, 0.02),
    (("energy drink", "red bull", "monster", "relentless"), "Energy Drink", SD, 45, 11.0, 0.4, 0.0, 0.0, 11.0, 0.0, 0.10),
    (("sugar free energy", "red bull sugar free", "monster ultra", "monster zero"), "Sugar-Free Energy Drink", SD, 3, 0.1, 0.4, 0.0, 0.0, 0.0, 0.0, 0.10),
    (("squash", "orange squash", "blackcurrant squash", "robinsons", "high juice"), "Squash / Cordial (diluted)", SD, 20, 4.8, 0.0, 0.0, 0.0, 4.7, 0.0, 0.01),
    (("no added sugar squash", "sugar free squash", "robinsons no added sugar"), "No Added Sugar Squash (diluted)", SD, 3, 0.3, 0.0, 0.0, 0.0, 0.2, 0.0, 0.01),
    (("coconut water",), "Coconut Water", SD, 18, 3.7, 0.7, 0.2, 0.2, 2.6, 1.1, 0.11),

    # ── BEVERAGES (hot / plant milks / functional) ─────────────────────────────
    (("coffee beans", "ground coffee", "instant coffee", "nescafe", "kenco", "coffee jar", "coffee bag"), "Coffee (dry)", BV, 5, 0.8, 0.3, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("coffee pods", "nespresso pods", "dolce gusto"), "Coffee Pods (each)", BV, 5, 0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("tea", "tea bags", "yorkshire tea", "pg tips", "tetley", "twinings", "typhoo"), "Tea Bags (per bag brewed)", BV, 1, 0.2, 0.1, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("green tea",), "Green Tea", BV, 1, 0.1, 0.2, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("herbal tea", "peppermint tea", "chamomile", "rooibos", "fruit tea"), "Herbal Tea", BV, 1, 0.1, 0.1, 0.0, 0.0, 0.0, 0.0, 0.01),
    (("hot chocolate", "cocoa powder", "cadbury hot choc", "options hot choc"), "Hot Chocolate Powder", BV, 400, 75.0, 8.0, 6.5, 4.0, 60.0, 5.0, 0.35),
    (("oat milk", "oatly", "alpro oat", "oat drink"), "Oat Milk", BV, 45, 6.7, 1.0, 1.5, 0.2, 4.1, 0.8, 0.10),
    (("almond milk", "alpro almond", "almond drink"), "Almond Milk", BV, 15, 0.4, 0.5, 1.3, 0.1, 0.1, 0.3, 0.10),
    (("soya milk", "soy milk", "alpro soya"), "Soya Milk", BV, 39, 2.5, 3.3, 1.8, 0.3, 2.5, 0.6, 0.10),
    (("coconut milk drink", "alpro coconut"), "Coconut Milk (drink)", BV, 23, 2.7, 0.1, 1.1, 1.0, 2.5, 0.1, 0.10),
    (("rice milk",), "Rice Milk", BV, 48, 10.0, 0.3, 1.0, 0.1, 4.0, 0.1, 0.10),
    (("cashew milk",), "Cashew Milk", BV, 22, 1.5, 0.5, 1.6, 0.2, 1.4, 0.2, 0.10),
    (("flavoured milk", "yazoo", "frijj", "milkshake", "chocolate milk"), "Flavoured Milk / Milkshake", BV, 75, 11.0, 3.2, 2.0, 1.3, 10.5, 0.0, 0.13),
    (("kombucha",), "Kombucha", BV, 25, 6.0, 0.0, 0.0, 0.0, 5.5, 0.0, 0.01),

    # ── FROZEN ────────────────────────────────────────────────────────────────
    (("frozen chips", "oven chips", "mccain chips", "steak fries", "fries frozen", "french fries frozen"), "Oven Chips", FR, 155, 26.0, 2.5, 4.5, 0.5, 0.5, 2.5, 0.35),
    (("frozen wedges", "potato wedges"), "Potato Wedges", FR, 145, 24.0, 2.3, 4.2, 0.5, 0.7, 2.7, 0.40),
    (("frozen roast potatoes", "roast potatoes frozen"), "Frozen Roast Potatoes", FR, 155, 24.0, 2.5, 5.5, 0.5, 0.5, 2.5, 0.30),
    (("hash brown", "hash browns"), "Hash Browns", FR, 200, 26.0, 2.5, 10.0, 1.0, 0.5, 2.5, 0.50),
    (("frozen pizza", "pizza margherita", "pizza pepperoni", "goodfellas", "chicago town"), "Frozen Pizza", FR, 245, 30.0, 11.0, 8.5, 4.0, 3.5, 2.5, 1.20),
    (("frozen ready meal", "birds eye", "aunt bessie", "young's", "ross"), "Frozen Ready Meal", FR, 140, 15.0, 8.0, 5.5, 2.0, 1.5, 1.5, 0.80),
    (("frozen berries", "frozen fruit", "frozen mango", "frozen mixed berries"), "Frozen Berries", FR, 45, 10.2, 1.0, 0.4, 0.0, 7.0, 4.0, 0.00),
    (("frozen prawns",), "Frozen Prawns", FR, 76, 0.0, 17.6, 0.6, 0.1, 0.0, 0.0, 1.10),
    (("frozen fish", "frozen cod", "frozen haddock", "frozen salmon"), "Frozen Fish (plain)", FR, 85, 0.0, 18.5, 0.8, 0.1, 0.0, 0.0, 0.20),
    (("yorkshire pudding", "yorkshire puds"), "Yorkshire Puddings", FR, 220, 32.0, 8.0, 6.5, 2.0, 3.0, 1.5, 0.85),
    (("ice cream tub", "vanilla tub", "chocolate tub", "neapolitan"), "Ice Cream Tub", FR, 210, 24.0, 3.5, 11.0, 7.0, 21.0, 0.5, 0.15),

    # ── TINNED / CUPBOARD / CONDIMENTS ────────────────────────────────────────
    (("baked beans", "beans", "heinz beans", "branston beans"), "Baked Beans", CO, 84, 12.5, 4.7, 0.6, 0.1, 4.7, 3.7, 0.60),
    (("chopped tomatoes", "tinned tomatoes", "plum tomatoes", "passata"), "Tinned Tomatoes / Passata", CO, 32, 4.8, 1.4, 0.2, 0.0, 4.0, 1.2, 0.02),
    (("kidney beans", "black beans", "cannellini beans", "borlotti", "butter beans", "haricot beans", "mixed beans tin"), "Tinned Beans (pulses)", CO, 105, 15.0, 8.0, 0.5, 0.1, 0.5, 6.5, 0.35),
    (("chickpeas", "garbanzo"), "Chickpeas", CO, 120, 17.5, 7.0, 2.0, 0.2, 1.0, 6.5, 0.35),
    (("lentils", "red lentils", "green lentils", "puy lentils", "tinned lentils"), "Lentils (cooked)", CO, 120, 20.0, 9.0, 0.4, 0.1, 1.8, 8.0, 0.02),
    (("tinned soup", "heinz soup", "cup a soup", "tomato soup", "chicken soup", "mushroom soup", "cream of"), "Tinned Soup", CO, 55, 6.5, 1.8, 2.2, 0.6, 3.5, 0.8, 0.60),
    (("fresh soup", "chilled soup", "covent garden soup", "new covent garden"), "Fresh Soup", CO, 55, 6.0, 2.0, 2.5, 0.7, 2.5, 1.2, 0.55),
    (("tomato ketchup", "ketchup", "heinz ketchup"), "Tomato Ketchup", CO, 105, 24.0, 1.5, 0.1, 0.0, 22.5, 0.5, 1.80),
    (("mayonnaise", "mayo", "hellmanns"), "Mayonnaise", CO, 680, 1.5, 1.0, 75.0, 6.0, 1.5, 0.0, 1.20),
    (("light mayonnaise", "light mayo"), "Light Mayonnaise", CO, 280, 8.0, 1.0, 27.0, 2.2, 6.5, 0.0, 1.40),
    (("mustard", "english mustard", "dijon", "wholegrain mustard"), "Mustard", CO, 165, 8.0, 8.0, 10.0, 0.6, 4.0, 5.5, 5.30),
    (("brown sauce", "hp sauce", "daddies sauce"), "Brown Sauce", CO, 100, 24.0, 1.0, 0.1, 0.0, 22.0, 1.5, 1.80),
    (("bbq sauce", "barbecue sauce"), "BBQ Sauce", CO, 165, 38.0, 1.0, 0.3, 0.0, 34.0, 1.0, 2.20),
    (("hot sauce", "sriracha", "tabasco", "chilli sauce", "cholula"), "Hot Sauce", CO, 60, 12.0, 1.5, 0.5, 0.0, 8.0, 1.0, 5.00),
    (("soy sauce", "kikkoman", "dark soy"), "Soy Sauce", CO, 55, 5.5, 8.0, 0.1, 0.0, 0.5, 0.5, 15.00),
    (("worcestershire", "worcester sauce", "lea perrins"), "Worcestershire Sauce", CO, 90, 20.0, 1.0, 0.0, 0.0, 18.0, 0.0, 4.50),
    (("pasta sauce", "bolognese sauce", "dolmio", "loyd grossman", "arrabbiata", "tomato pasta sauce"), "Pasta Sauce (tomato)", CO, 55, 8.0, 1.5, 1.8, 0.3, 6.5, 1.2, 0.75),
    (("pesto", "green pesto", "red pesto"), "Pesto", CO, 460, 5.0, 6.0, 46.0, 6.5, 4.5, 2.0, 2.30),
    (("curry paste", "korma paste", "tikka paste", "patak"), "Curry Paste", CO, 220, 12.0, 3.5, 17.5, 2.0, 8.0, 3.5, 6.50),
    (("curry sauce", "korma sauce", "tikka masala sauce", "sharwoods", "loyd curry", "jar of curry", "madras sauce", "madras", "jalfrezi", "bhuna", "rogan josh", "balti sauce"), "Curry Sauce (jar)", CO, 100, 10.0, 1.5, 6.0, 2.0, 7.0, 1.5, 0.85),
    (("stir fry sauce", "sweet sour sauce", "chow mein sauce", "black bean sauce"), "Stir Fry Sauce", CO, 100, 20.0, 1.0, 1.0, 0.1, 17.0, 0.5, 2.20),
    (("vinegar", "white vinegar", "malt vinegar", "balsamic", "cider vinegar", "red wine vinegar"), "Vinegar", CO, 20, 4.0, 0.1, 0.0, 0.0, 3.5, 0.0, 0.05),
    (("olive oil", "extra virgin olive oil", "evoo"), "Olive Oil", CO, 824, 0.0, 0.0, 91.5, 13.5, 0.0, 0.0, 0.00),
    (("vegetable oil", "sunflower oil", "rapeseed oil", "cooking oil"), "Vegetable Oil", CO, 828, 0.0, 0.0, 92.0, 8.0, 0.0, 0.0, 0.00),
    (("coconut oil",), "Coconut Oil", CO, 862, 0.0, 0.0, 95.5, 86.5, 0.0, 0.0, 0.00),
    (("honey", "clear honey", "manuka honey", "runny honey"), "Honey", CO, 322, 79.5, 0.3, 0.0, 0.0, 79.5, 0.0, 0.00),
    (("maple syrup", "syrup", "golden syrup"), "Maple / Golden Syrup", CO, 300, 74.0, 0.1, 0.1, 0.0, 68.0, 0.0, 0.03),
    (("jam", "strawberry jam", "raspberry jam", "marmalade", "conserve"), "Jam / Marmalade", CO, 260, 65.0, 0.5, 0.1, 0.0, 62.0, 1.0, 0.05),
    (("peanut butter", "pb", "smooth pb", "crunchy pb", "sun-pat", "whole earth"), "Peanut Butter", CO, 590, 12.0, 25.0, 50.0, 10.0, 6.0, 6.5, 0.80),
    (("nutella", "chocolate spread"), "Chocolate Spread", CO, 540, 57.0, 6.0, 31.0, 10.5, 55.0, 3.0, 0.10),
    (("marmite", "vegemite"), "Yeast Extract", CO, 265, 30.0, 33.0, 0.5, 0.1, 1.0, 5.5, 10.80),
    (("stock cube", "stock cubes", "oxo", "knorr stock", "bouillon"), "Stock Cubes", CO, 260, 15.0, 12.0, 15.0, 6.0, 5.0, 0.5, 45.00),
    (("gravy granules", "bisto"), "Gravy Granules", CO, 400, 62.0, 5.0, 14.0, 6.5, 1.5, 3.0, 12.00),
    (("sugar", "granulated sugar", "caster sugar", "white sugar", "brown sugar", "demerara"), "Sugar", CO, 400, 100.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.00),
    (("salt", "sea salt", "table salt"), "Salt", CO, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 100.00),
    (("spices", "cumin", "turmeric", "paprika", "cinnamon", "cloves", "cardamom", "coriander seeds", "black pepper", "peppercorns", "chilli powder", "curry powder", "garam masala"), "Spices (dry)", CO, 300, 45.0, 12.0, 10.0, 2.0, 8.0, 25.0, 0.10),
    (("olives jar", "green olives jar", "black olives jar", "kalamata"), "Olives (jar)", CO, 145, 3.8, 1.0, 13.9, 2.0, 0.5, 3.3, 3.10),
    (("gherkins", "pickles", "cornichons"), "Gherkins / Pickles", CO, 20, 3.0, 0.5, 0.1, 0.0, 2.5, 1.0, 2.20),
    (("sun dried tomatoes", "sundried tomatoes"), "Sun-Dried Tomatoes", CO, 250, 30.0, 6.0, 13.0, 1.8, 20.0, 8.0, 2.50),
    (("capers",), "Capers", CO, 25, 5.0, 2.4, 0.9, 0.2, 0.4, 3.2, 7.00),
    (("mint sauce",), "Mint Sauce", CO, 90, 21.0, 0.5, 0.0, 0.0, 20.0, 0.0, 2.00),
    (("horseradish",), "Horseradish", CO, 175, 22.0, 3.0, 8.5, 1.0, 15.0, 5.0, 1.20),
    (("tartare sauce",), "Tartare Sauce", CO, 400, 8.0, 1.0, 40.0, 3.5, 6.0, 0.5, 1.30),

    # ── READY MEALS ───────────────────────────────────────────────────────────
    (("lasagne", "lasagne ready meal", "beef lasagne"), "Beef Lasagne (chilled)", RM, 135, 12.0, 7.5, 6.0, 3.0, 3.0, 1.5, 0.60),
    (("chicken tikka masala", "tikka masala", "chicken tikka meal", "chicken curry ready"), "Chicken Tikka Masala", RM, 130, 10.5, 8.5, 5.5, 2.0, 4.0, 1.3, 0.70),
    (("chicken korma", "korma"), "Chicken Korma", RM, 155, 10.5, 8.0, 8.5, 4.5, 3.5, 1.3, 0.60),
    (("cottage pie", "shepherds pie", "shepherd's pie"), "Cottage / Shepherd's Pie", RM, 115, 11.0, 6.5, 5.0, 2.5, 2.0, 1.2, 0.55),
    (("fish pie",), "Fish Pie", RM, 105, 10.5, 6.5, 4.0, 2.2, 1.5, 1.0, 0.65),
    (("macaroni cheese", "mac cheese", "mac and cheese"), "Macaroni Cheese", RM, 155, 15.0, 6.5, 7.5, 4.5, 2.0, 1.0, 0.60),
    (("spag bol", "spaghetti bolognese", "bolognese ready meal", "bolognese"), "Spaghetti Bolognese", RM, 120, 14.0, 6.5, 4.0, 1.5, 3.0, 1.5, 0.55),
    (("stir fry meal", "chow mein", "singapore noodles"), "Stir Fry (chilled)", RM, 130, 18.0, 6.5, 3.5, 0.7, 3.5, 2.0, 0.85),
    (("chicken pie", "steak pie", "steak and ale pie", "chicken and mushroom pie"), "Chicken / Steak Pie", RM, 260, 22.0, 9.5, 15.0, 6.5, 1.5, 1.5, 0.85),
    (("quiche",), "Quiche", RM, 260, 18.0, 9.5, 16.5, 7.5, 1.5, 1.0, 0.85),
    (("meal deal sandwich", "sandwich", "sarnie", "chicken sandwich", "ham sandwich", "cheese sandwich", "blt", "ploughman", "tuna sandwich", "egg mayo sandwich", "pret sandwich"), "Sandwich (avg)", RM, 235, 25.0, 11.0, 10.5, 3.0, 3.0, 2.0, 1.10),
    (("wrap meal", "chicken wrap", "falafel wrap", "tuna wrap"), "Wrap (filled)", RM, 235, 27.0, 10.0, 9.5, 3.0, 3.0, 2.5, 1.20),
    (("sushi", "sushi pack", "california roll", "sushi selection"), "Sushi", RM, 145, 25.0, 5.5, 2.0, 0.4, 2.5, 1.0, 0.75),
    (("salad bowl", "chicken caesar salad", "pasta salad", "quinoa salad"), "Salad Bowl (chilled)", RM, 130, 12.0, 6.0, 6.5, 1.5, 3.0, 2.5, 0.55),
    (("pizza slice", "pizza fresh", "wood fired pizza", "sourdough pizza"), "Fresh Pizza", RM, 245, 30.0, 11.0, 8.5, 4.0, 3.5, 2.5, 1.20),
    (("indian meal for two", "indian takeaway meal", "indian ready meal"), "Indian Ready Meal (avg)", RM, 145, 12.0, 8.0, 7.0, 2.5, 3.5, 1.5, 0.75),
    (("chinese meal for two", "chinese ready meal", "sweet sour chicken", "kung pao"), "Chinese Ready Meal (avg)", RM, 145, 17.0, 7.0, 5.0, 1.0, 5.0, 1.5, 0.90),
    (("italian meal for two", "italian ready meal", "carbonara", "risotto"), "Italian Ready Meal (avg)", RM, 150, 14.5, 6.5, 7.0, 3.0, 2.0, 1.5, 0.65),

    # ── PLANT-BASED / VEGAN / MEAT-FREE / FREE-FROM ───────────────────────────
    (("tofu", "silken tofu", "firm tofu"), "Tofu", O, 76, 1.9, 8.1, 4.8, 0.7, 0.6, 0.3, 0.01),
    (("tempeh",), "Tempeh", O, 195, 8.0, 20.0, 11.0, 2.2, 0.5, 1.4, 0.05),
    (("seitan", "wheat protein"), "Seitan", O, 145, 14.0, 25.0, 2.0, 0.3, 0.5, 1.0, 1.00),
    (("quorn mince", "meat free mince", "vegan mince", "plant mince", "beyond mince"), "Meat-Free Mince", O, 105, 3.5, 14.5, 3.5, 0.5, 0.5, 5.5, 0.60),
    (("quorn pieces", "quorn chicken", "vegan chicken pieces", "plant chicken"), "Meat-Free Chicken Pieces", O, 100, 2.0, 15.0, 3.0, 0.4, 0.5, 5.5, 0.65),
    (("quorn nuggets", "vegan nuggets", "plant nuggets"), "Meat-Free Nuggets", O, 205, 18.0, 12.0, 9.5, 1.0, 1.0, 4.5, 1.10),
    (("veggie burger", "vegan burger", "beyond burger", "plant burger", "linda mccartney burger"), "Plant-Based Burger", O, 220, 8.5, 17.0, 13.5, 4.5, 0.5, 3.5, 1.20),
    (("vegan sausage", "veggie sausage", "meat free sausage", "linda mccartney sausage", "richmond meat free"), "Plant-Based Sausage", O, 210, 9.5, 15.5, 12.5, 2.0, 1.0, 4.0, 1.60),
    (("vegan bacon", "meat free bacon", "facon"), "Plant-Based Bacon", O, 180, 8.0, 20.0, 8.0, 1.0, 1.0, 3.0, 2.50),
    (("vegan cheese", "plant cheese", "violife", "sheese"), "Vegan Cheese", O, 275, 22.0, 0.3, 20.0, 17.5, 0.3, 0.0, 1.90),
    (("vegan yogurt", "coconut yogurt", "soya yogurt", "oat yogurt", "alpro yogurt"), "Plant-Based Yogurt", O, 65, 5.5, 3.0, 3.0, 0.5, 4.0, 0.5, 0.10),
    (("vegan ice cream", "oat ice cream", "coconut ice cream"), "Vegan Ice Cream", O, 200, 24.0, 1.5, 10.5, 7.5, 20.0, 1.0, 0.15),
    (("falafel",), "Falafel", O, 260, 20.0, 11.5, 15.5, 2.0, 3.0, 5.5, 1.10),
    (("gluten free bread", "gf bread", "gluten-free loaf"), "Gluten-Free Bread", B, 250, 45.0, 3.0, 5.5, 0.8, 4.5, 3.0, 1.00),
    (("gluten free pasta", "gf pasta"), "Gluten-Free Pasta (dry)", C, 360, 78.0, 6.5, 1.5, 0.3, 1.5, 2.0, 0.02),
    (("gluten free crackers", "gf crackers"), "Gluten-Free Crackers", S, 430, 68.0, 4.5, 14.0, 2.5, 3.5, 3.5, 1.10),
    (("free from biscuits", "gluten free biscuits"), "Gluten-Free Biscuits", DS, 470, 65.0, 3.5, 21.0, 10.0, 26.0, 3.0, 0.65),
    (("lactose free milk", "lactofree milk"), "Lactose-Free Milk", D, 50, 4.8, 3.6, 1.8, 1.1, 4.8, 0.0, 0.10),
    (("plant butter", "vegan butter", "flora plant", "naturli butter"), "Plant-Based Butter", O, 700, 0.5, 0.2, 77.0, 24.0, 0.5, 0.0, 1.10),
]

# ══════════════════════════════════════════════════════════════════════════════
# INDEX + MATCHING
# ══════════════════════════════════════════════════════════════════════════════

# Build an index at import time: alias -> entry.
# Aliases are stored as tuples of tokens for prefix-aware matching.
_INDEX = []  # list of (alias_tokens, entry)
_STOPWORDS = {"the", "and", "with", "a", "of", "in", "on", "each", "pack",
              "value", "essential", "essentials", "basics", "own", "brand",
              "signature", "finest", "extra", "select", "fresh", "chilled"}

def _tokenise(text: str):
    """Lowercase, strip non-alnum, drop stopwords and pure numerics/units."""
    text = text.lower()
    tokens = re.findall(r"[a-z]+", text)
    out = []
    for t in tokens:
        if t in _STOPWORDS:
            continue
        if t in ("kg", "g", "ml", "l", "ltr", "oz", "lb", "pk", "x"):
            continue
        out.append(t)
    return out

for _entry in FOOD_DB:
    for _alias in _entry[0]:
        _INDEX.append((tuple(_tokenise(_alias)), _entry))

# Sort so longer, more distinctive aliases are checked first.
# Primary: more tokens (multi-word aliases are more specific).
# Secondary: longer avg token length (rarer/more distinctive words win).
_INDEX.sort(key=lambda x: (-len(x[0]),
                           -sum(len(t) for t in x[0]) / max(1, len(x[0]))))


def _make_result(entry) -> Dict:
    """Convert a FOOD_DB tuple into the same schema as OFF/USDA results."""
    _, name, cat, kcal, carbs, prot, fat, satfat, sugar, fibre, salt = entry
    return {
        "source":             "EatIQ UK DB",
        "full_database_name": name,
        "brand":              "",
        "calories_100g":      kcal,
        "macronutrients_per_100g": {
            "carbohydrates_g": carbs,
            "proteins_g":      prot,
            "fats_g":          fat,
        },
        "sugar_100g":     sugar,
        "sat_fat_100g":   satfat,
        "fibre_100g":     fibre,
        "salt_100g":      salt,
        "category_hint":  cat,
    }


def match(term: str) -> Optional[Dict]:
    """
    Return a nutrition dict if term matches an entry in the local UK DB.
    A token matches if it appears in the query as a full word, or if the
    alias token is a >=4-char prefix of a query token (handles truncated
    receipt words like 'chick' -> 'chicken'), or a query token is a >=4-char
    prefix of the alias token (handles 'yog' -> 'yogurt' via 3-char alias
    only when alias is itself short and standalone).
    All alias tokens must match. Returns None if no entry matches.
    """
    if not term:
        return None
    q_tokens = _tokenise(term)
    if not q_tokens:
        return None
    q_set = set(q_tokens)

    for alias_tokens, entry in _INDEX:
        if not alias_tokens:
            continue
        ok = True
        for atok in alias_tokens:
            if atok in q_set:
                continue
            hit = False
            for qt in q_tokens:
                # alias-is-prefix-of-query: 'chick' -> 'chicken'  (alias >=4)
                if len(atok) >= 4 and qt.startswith(atok):
                    hit = True; break
                # query-is-prefix-of-alias: 'yog' -> 'yogurt' — only when alias
                # extends the query by <=3 chars, so 'milk' does NOT match 'milkshake'.
                if len(qt) >= 3 and len(atok) >= 5 and atok.startswith(qt)                         and (len(atok) - len(qt)) <= 3:
                    hit = True; break
            if not hit:
                ok = False
                break
        if ok:
            return _make_result(entry)
    return None


def sanity_check_off(term: str, off_name: str) -> bool:
    """
    Reject an OFF top-hit whose product name shares no meaningful word with
    the receipt term. Prevents the 'LIME -> lime pickle' class of bug.
    Returns True if the OFF result looks plausible, False to reject it.
    """
    if not off_name:
        return False
    q = set(_tokenise(term))
    o = set(_tokenise(off_name))
    if not q or not o:
        return True  # can't judge — don't reject
    # Require at least one shared meaningful token >=3 chars
    shared = {t for t in q & o if len(t) >= 3}
    return bool(shared)


def stats() -> Dict:
    """For the /health endpoint."""
    return {
        "entries":   len(FOOD_DB),
        "aliases":   len(_INDEX),
    }
