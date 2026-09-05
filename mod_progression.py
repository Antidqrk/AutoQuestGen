"""Hand-checked opening/closing steps for well-known mods.

Why this exists: a mod's advancement tree gives good MILESTONES but often not
the true "how do I start" — Botania's first advancement is the Lexica Botania,
but a player actually begins by picking Mystical Flowers and building a Petal
Apothecary. These chains encode the real opening moves and the real endgame.

Sourced from each mod's official wiki / in-game guide progression pages
(FTB Wiki, Twilight Forest Wiki, mod documentation).

Format:  mod_id -> [ (item_or_entity_id, quest title, short description), ... ]
in play order.  Prefix an entity with "kill:" for a kill task.
Ids that don't exist in the user's pack are dropped automatically, so it's safe
to list ids that only appear in some versions.
"""

MOD_STARTS = {
    # ---- magic ---------------------------------------------------------
    "botania": [
        ("botania:white_petal|botania:pink_petal|botania:black_petal|botania:blue_petal|botania:white_mystical_flower|botania:pink_mystical_flower", "Gather Mystical Flowers",
         "Mystical Flowers grow wild. Everything in Botania starts here."),
        ("botania:apothecary_default", "The Petal Apothecary",
         "Fill it with water, drop in petals and a seed to craft flowers."),
        ("botania:pure_daisy", "The Pure Daisy",
         "Plant it beside logs and stone to make Livingwood and Livingrock."),
        ("botania:twig_wand", "Wand of the Forest",
         "Two livingwood twigs and two petals. You will use it on every "
         "functional flower and block in the mod - craft it the moment you "
         "have livingwood."),
        ("botania:livingwood", "Livingwood", ""),
        ("botania:livingrock", "Livingrock", ""),
        ("botania:endoflame", "Your First Generating Flower",
         "The Endoflame eats coal and makes Mana. Reliable and cheap."),
        ("botania:mana_spreader", "Mana Spreader", "Moves Mana from a flower to a pool."),
        ("botania:mana_pool", "The Mana Pool", "Stores Mana and does Mana infusion."),
        ("botania:manasteel_ingot", "Manasteel", "Iron infused with Mana in the pool."),
        ("botania:runic_altar", "The Runic Altar", ""),
        ("botania:terra_plate", "The Terrestrial Agglomeration Plate",
         "A multiblock: the plate surrounded by Lapis, Fabulous and Terra "
         "runes on the pattern in the Lexica. Building it IS the mid-game "
         "wall - Terrasteel comes after."),
        ("botania:terrasteel_ingot", "Terrasteel",
         "Made on the Terrestrial Agglomeration Plate. The mid-game wall."),
        ("botania:alfheim_portal", "Open the Elven Gateway", "Trade with the elves of Alfheim."),
        ("botania:gaia_ingot", "Slay the Gaia Guardian",
         "Summon it on a Beacon with Gaia Spirit. Botania's final boss."),
    ],
    "goety": [
        ("goety:dark_wand", "The Dark Arts", "Your focus for casting."),
        ("goety:magic_flesh", "Magic Flesh", ""),
        ("goety:altar", "Build an Altar", "Stores the soul energy your spells burn."),
        ("goety:necro_table", "The Necronomicon", ""),
        ("goety:dark_ingot", "Dark Metal", ""),
    ],
    "ars_nouveau": [
        ("ars_nouveau:worn_notebook", "The Worn Notebook", "Your guide to everything."),
        ("ars_nouveau:magebloom_crop", "Magebloom", ""),
        ("ars_nouveau:novice_spell_book", "Your First Spellbook", ""),
        ("ars_nouveau:arcane_pedestal", "Arcane Pedestals", ""),
        ("ars_nouveau:enchanting_apparatus", "The Enchanting Apparatus", ""),
        ("ars_nouveau:source_gem", "Source Gems", ""),
    ],
    "bloodmagic": [
        ("bloodmagic:blood_altar", "The Blood Altar", "Everything is paid for in blood."),
        ("bloodmagic:sacrificial_knife", "Sacrificial Knife", ""),
        ("bloodmagic:soul_gem_petty", "A Petty Tartaric Gem", ""),
        ("bloodmagic:blood_rune_blank", "Blood Runes", ""),
    ],
    "occultism": [
        ("occultism:dictionary_of_spirits", "Dictionary of Spirits", "Read this first."),
        ("occultism:chalk_white", "White Chalk", ""),
        ("occultism:candle_white", "Ritual Candles", ""),
        ("occultism:spirit_attuned_gem", "Spirit-Attuned Gem", ""),
    ],
    "eidolon": [
        ("eidolon:codex", "The Codex", ""),
        ("eidolon:worktable", "The Worktable", ""),
        ("eidolon:pewter_ingot", "Pewter", ""),
    ],
    "malum": [
        ("malum:encyclopedia_arcana", "Encyclopaedia Arcana", ""),
        ("malum:processed_soulstone", "Soulstone", ""),
        ("malum:spirit_altar", "The Spirit Altar", ""),
    ],
    "irons_spellbooks": [
        ("irons_spellbooks:arcane_ingot", "Arcane Ingot", ""),
        ("irons_spellbooks:scroll", "Your First Scroll", ""),
        ("irons_spellbooks:arcane_anvil", "The Arcane Anvil", ""),
    ],

    # ---- tech ----------------------------------------------------------
    "create": [
        ("create:andesite_alloy", "Andesite Alloy", "Create's most important early material."),
        ("create:wrench", "The Wrench",
         "Rotates and dismantles every Create block. You will use it from your "
         "first cogwheel to your last machine - get it before anything else."),
        ("create:goggles", "Engineer's Goggles",
         "Wear them to read stress and speed on any block you look at. "
         "Debugging a contraption without these is guesswork."),
        ("create:cogwheel", "Cogwheels", "Rotation is the whole mod."),
        ("create:shaft", "Shafts", ""),
        ("create:water_wheel", "Water Wheel", "Your first source of rotational force."),
        ("create:andesite_casing", "Andesite Casing", ""),
        ("create:mechanical_press", "Mechanical Press", ""),
        ("create:millstone", "Millstone", ""),
        ("create:mechanical_drill", "Mechanical Drill", ""),
        ("create:brass_ingot", "Brass", "Zinc plus copper in a mixer. Opens the mid game."),
        ("create:brass_casing", "Brass Casing", ""),
        ("create:mechanical_crafter", "Mechanical Crafters", ""),
        ("create:precision_mechanism", "Precision Mechanism", "The late-game gate."),
    ],
    "mekanism": [
        ("mekanism:steel_ingot", "Steel", ""),
        ("mekanism:enrichment_chamber", "Enrichment Chamber", "Your first machine."),
        ("mekanism:metallurgic_infuser", "Metallurgic Infuser", ""),
        ("mekanism:basic_energy_cube", "Energy Cube", ""),
        ("mekanism:electrolytic_separator", "Electrolytic Separator", ""),
        ("mekanism:digital_miner", "Digital Miner", ""),
        ("mekanism:fusion_reactor_controller", "Fusion Reactor", "The endgame."),
    ],
    "thermal": [
        ("thermal:machine_frame", "Machine Frame", ""),
        ("thermal:dynamo_stirling", "Stirling Dynamo", "Your first power."),
        ("thermal:machine_pulverizer", "Pulverizer", "Double your ores."),
        ("thermal:machine_furnace", "Redstone Furnace", ""),
        ("thermal:machine_smelter", "Induction Smelter", ""),
    ],
    "immersiveengineering": [
        ("immersiveengineering:hammer", "Engineer's Hammer", "The tool you build everything with."),
        ("immersiveengineering:manual", "Engineer's Manual", ""),
        ("immersiveengineering:stick_treated", "Treated Wood", ""),
        ("immersiveengineering:windmill", "The Windmill", "First real power."),
        ("immersiveengineering:coke_oven", "Coke Oven", ""),
        ("immersiveengineering:blast_furnace", "Blast Furnace", "Steel."),
    ],
    "ae2": [
        ("ae2:certus_quartz_crystal", "Certus Quartz", ""),
        ("ae2:charger", "The Charger", ""),
        ("ae2:fluix_crystal", "Fluix", ""),
        ("ae2:controller", "ME Controller", "The heart of your network."),
        ("ae2:drive", "ME Drive", ""),
        ("ae2:terminal", "ME Terminal", ""),
        ("ae2:molecular_assembler", "Molecular Assembler", "Autocrafting."),
    ],
    "appliedenergistics2": [
        ("appliedenergistics2:certus_quartz_crystal", "Certus Quartz", ""),
        ("appliedenergistics2:fluix_crystal", "Fluix", ""),
        ("appliedenergistics2:controller", "ME Controller", ""),
        ("appliedenergistics2:molecular_assembler", "Molecular Assembler", ""),
    ],
    "refinedstorage": [
        ("refinedstorage:quartz_enriched_iron", "Quartz Enriched Iron", ""),
        ("refinedstorage:controller", "The Controller", ""),
        ("refinedstorage:disk_drive", "Disk Drive", ""),
        ("refinedstorage:grid", "The Grid", ""),
        ("refinedstorage:crafter", "Autocrafting", ""),
    ],
    "draconicevolution": [
        ("draconicevolution:draconium_dust", "Draconium", "Mined in the End."),
        ("draconicevolution:draconium_ingot", "Draconium Ingot", ""),
        ("draconicevolution:energy_core", "Energy Core", "Store absurd amounts of power."),
        ("draconicevolution:draconic_core", "Draconic Core", ""),
        ("draconicevolution:awakened_core", "Awakened Core",
         "Made by ritual with the Ender Dragon's help."),
        ("draconicevolution:draconic_chestplate", "Draconic Armour", ""),
        ("kill:draconicevolution:chaos_guardian", "Slay the Chaos Guardian",
         "Draconic Evolution's final boss. Chaos Shards are the reward."),
        ("draconicevolution:chaos_shard", "Chaos Shard", ""),
    ],
    "powah": [
        ("powah:steel_energized", "Energized Steel", ""),
        ("powah:furnator_starter", "Furnator", "Your first generator."),
        ("powah:energy_cell_starter", "Energy Cell", ""),
        ("powah:reactor_starter", "Uraninite Reactor", ""),
    ],
    "pneumaticcraft": [
        ("pneumaticcraft:pressure_chamber_wall", "Pressure Chamber", ""),
        ("pneumaticcraft:air_compressor", "Air Compressor", ""),
        ("pneumaticcraft:pressure_tube", "Pressure Tubes", ""),
    ],
    "tconstruct": [
        ("tconstruct:pattern", "Blank Pattern", ""),
        ("tconstruct:crafting_station", "Crafting Station", ""),
        ("tconstruct:seared_bricks", "Seared Bricks", ""),
        ("tconstruct:smeltery_controller", "The Smeltery", "Melt everything."),
    ],

    # ---- adventure / dimensions ----------------------------------------
    # Order follows the FTB wiki's "Twilight Forest Progression" page. Each boss
    # drops the key that opens the next biome. The entry step is a DIMENSION
    # task: the portal is built in the world, and the only "portal" item in the
    # jar is a decorative miniature, which made a trophy the chapter opener.
    "twilightforest": [
        ("dim:twilightforest:twilight_forest", "Into the Twilight Forest",
         "There is no portal item to craft. Dig a 2x2 pool of water, ring it "
         "with grass and flowers, then throw a DIAMOND into the water. "
         "Lightning strikes and the pool becomes the portal."),
        ("twilightforest:raven_feather", "Raven Feathers",
         "Dropped by ravens. You need one for the Magic Map Focus."),
        ("twilightforest:magic_map_focus", "Magic Map Focus",
         "Raven feather, glowstone and paper. Reveals the dungeons and bosses."),
        ("kill:twilightforest:naga", "Slay the Naga",
         "Found in the Naga Courtyard. The first boss."),
        ("twilightforest:naga_scale", "Take a Naga Scale",
         "Touching a scale is what unlocks the Lich Tower."),
        ("kill:twilightforest:lich", "Slay the Twilight Lich",
         "Top of the Lich Tower."),
        ("twilightforest:twilight_scepter|twilightforest:zombie_scepter",
         "Claim the Lich's Scepter",
         "The scepter opens the Swamp, Dark Forest and Snowy Forest."),
        ("twilightforest:ironwood_ingot", "Ironwood", ""),
        ("kill:twilightforest:minoshroom", "Slay the Minoshroom",
         "At the heart of a Labyrinth, under the Swamp."),
        ("twilightforest:meef_stroganoff", "Eat the Meef Stroganoff",
         "Eating it is what opens the Fire Swamp."),
        ("kill:twilightforest:hydra", "Slay the Hydra",
         "The Hydra Lair, in the middle of the Fire Swamp."),
        ("twilightforest:fiery_blood", "Fiery Blood",
         "One of the two keys to the Highlands."),
        ("twilightforest:fiery_ingot", "Fiery Ingot", ""),
        ("twilightforest:trophy_pedestal", "Find a Trophy Pedestal",
         "Place any trophy on it to drop the Goblin Knight Stronghold's shield."),
        ("kill:twilightforest:knight_phantom", "Defeat the Knight Phantoms",
         "Inside the stronghold. Killing them opens the Dark Tower."),
        ("twilightforest:knightmetal_ingot", "Knightmetal", ""),
        ("kill:twilightforest:ur_ghast", "Slay the Ur-Ghast",
         "Top of the Dark Tower."),
        ("twilightforest:fiery_tears", "Fiery Tears",
         "The second key to the Highlands."),
        ("kill:twilightforest:alpha_yeti", "Slay the Alpha Yeti",
         "In the Yeti Lair. Opens the Glacier."),
        ("twilightforest:arctic_fur", "Arctic Fur", ""),
        ("kill:twilightforest:snow_queen", "Slay the Snow Queen",
         "The Aurora Palace, high on the Glacier."),
        ("twilightforest:lamp_of_cinders", "The Lamp of Cinders",
         "Burns away the Thornlands so you can reach the Final Plateau."),
        ("twilightforest:carminite", "Carminite", ""),
        ("twilightforest:experiment_115", "Reach the Final Castle",
         "Past the Thornlands lies the Final Plateau - the end of the Twilight Forest."),
    ],
    "cataclysm": [
        ("cataclysm:bone_reptile_skull", "Trophies of the Cataclysm", ""),
        ("kill:cataclysm:ender_guardian", "The Ender Guardian", ""),
        ("kill:cataclysm:netherite_monstrosity", "The Netherite Monstrosity", ""),
        ("kill:cataclysm:ancient_remnant", "The Ancient Remnant", ""),
        ("kill:cataclysm:the_leviathan", "The Leviathan", ""),
        ("kill:cataclysm:ignis", "Ignis", "The Fire Titan."),
    ],
    "alexsmobs": [
        ("alexsmobs:animal_dictionary", "Animal Dictionary", "Your field guide."),
        ("alexsmobs:blood_sprayer", "", ""),
        ("alexsmobs:soul_heart", "Soul Heart", ""),
    ],
    # Getting to the Tropics is NOT a portal you build - you drink a Pina Colada
    # while sitting in a chair at sunset. Nothing in the mod's own data says so,
    # which is exactly why a generated chapter opened on a random sapling.
    # Item ids verified against Tropicraft-9.6.3.jar (no portal_enchanter here).
    "tropicraft": [
        ("tropicraft:bamboo_stick", "Bamboo Chutes",
         "Tropicraft starts with bamboo. Cut some chutes - everything else needs them."),
        ("tropicraft:bamboo_mug", "An Empty Mug",
         "Bamboo sticks make a mug. You are going to need a drink."),
        ("tropicraft:pineapple", "Pineapple", "Grows wild in tropical biomes."),
        ("tropicraft:coconut_chunk", "Coconut Chunks",
         "Break a coconut. Half a cocktail, right there."),
        ("tropicraft:pina_colada", "Pina Colada",
         "Mug + coconut chunks + pineapple. THIS IS YOUR TICKET IN."),
        ("tropicraft:white_chair|tropicraft:yellow_chair|tropicraft:orange_chair",
         "Pull Up a Chair",
         "Craft a beach chair - you cannot make the trip standing up."),
        ("dim:tropicraft:tropics", "Sunset Departure",
         "Place a beach chair, sit in it at SUNSET, and drink the Pina Colada. "
         "That is how you reach the Tropics - there is no portal to build. "
         "Drink another at the bottom of the portal water to come home."),
        ("tropicraft:azurite_gem", "Azurite", "Tropical ore. Start mining the islands."),
        ("tropicraft:eudialyte_gem", "Eudialyte", ""),
        ("tropicraft:zircon_gem", "Zircon", ""),
        ("tropicraft:scale", "Scales", "Dropped by tropical fish and lizards."),
        ("tropicraft:scale_chestplate", "Scale Armour", ""),
        ("tropicraft:eudialyte_pickaxe", "Eudialyte Tools", ""),
        ("tropicraft:zircon_axe", "Zircon Gear", "The best the islands offer."),
    ],
    "aether": [
        ("aether:golden_amber", "Golden Amber", ""),
        ("aether:skyroot_planks", "Skyroot", ""),
        ("aether:zanite_gemstone", "Zanite", ""),
        ("aether:gravitite_ingot", "Gravitite", ""),
    ],
    "undergarden": [
        ("undergarden:catalyst", "The Catalyst", "Opens the portal to the Undergarden."),
        ("undergarden:cloggrum_ingot", "Cloggrum", ""),
        ("undergarden:froststeel_ingot", "Froststeel", ""),
        ("undergarden:utherium_crystal", "Utherium", ""),
    ],
    "blue_skies": [
        ("blue_skies:everbright_stone", "The Everbright", ""),
        ("blue_skies:pyrope_gem", "Pyrope", ""),
    ],
    "iceandfire": [
        ("iceandfire:bestiary", "The Bestiary", ""),
        ("iceandfire:dragon_bone", "Dragon Bones", ""),
        ("iceandfire:dragonsteel_fire_ingot", "Dragonsteel", ""),
    ],
    "mowziesmobs": [
        ("mowziesmobs:wrought_axe", "Wrought Axe", ""),
        ("mowziesmobs:ice_crystal", "Ice Crystal", ""),
    ],

    # ---- collect-a-thons / misc ----------------------------------------
    "inventorypets": [
        ("inventorypets:pet_cloud", "Find a Cloud Dungeon",
         "Cloud and Tree dungeons hide the first pets."),
        ("inventorypets:pet_black_hole", "The Black Hole Pet", ""),
        ("inventorypets:pet_ender", "The Ender Pet", ""),
        ("inventorypets:pet_dragon", "The Dragon Pet",
         "One of the hardest pets to obtain. Collect them all."),
    ],
    "farmersdelight": [
        ("farmersdelight:cooking_pot", "The Cooking Pot", "Where every recipe starts."),
        ("farmersdelight:skillet", "The Skillet", ""),
        ("farmersdelight:cutting_board", "Cutting Board", ""),
        ("farmersdelight:stove", "The Stove", ""),
        ("farmersdelight:rich_soil", "Rich Soil", ""),
    ],
    "quark": [
        ("quark:crate", "Crates", ""),
        ("quark:backpack", "The Backpack", ""),
        ("quark:pipe", "Pipes", ""),
        ("quark:diamond_heart", "Diamond Heart", ""),
    ],
    "supplementaries": [
        ("supplementaries:sack", "The Sack", ""),
        ("supplementaries:safe", "The Safe", ""),
        ("supplementaries:soap", "Soap", ""),
    ],
    "securitycraft": [
        ("securitycraft:universal_block_remover", "Universal Block Remover", ""),
        ("securitycraft:keypad", "The Keypad", ""),
        ("securitycraft:reinforced_stone", "Reinforced Blocks", ""),
        ("securitycraft:security_camera", "Security Cameras", ""),
        ("securitycraft:sentry", "The Sentry", ""),
    ],
    "sophisticatedbackpacks": [
        ("sophisticatedbackpacks:backpack", "Your First Backpack", ""),
        ("sophisticatedbackpacks:iron_backpack", "Iron Backpack", ""),
        ("sophisticatedbackpacks:netherite_backpack", "Netherite Backpack", ""),
    ],
    "waystones": [
        ("waystones:waystone", "Your First Waystone", ""),
        ("waystones:warp_stone", "Warp Stone", ""),
    ],
}


def chain_for(mod_id: str):
    return MOD_STARTS.get(mod_id.lower(), [])
