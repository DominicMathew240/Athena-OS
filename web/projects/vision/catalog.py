"""Ready-made inspection templates: plain-language questions with the instructions the vision model needs.

Each template is written for someone with no computer-vision background: a name, one question, what they get
back (the counts), and an example of where the pictures come from. ``focus`` is what the model is told.
"""

from __future__ import annotations

CUSTOM = "custom"

TEMPLATES: list[dict] = [
    {
        "key": "parking",
        "name": "Parking occupancy",
        "industry": "Facilities & smart city",
        "icon": "car",
        "tagline": "Count free and taken parking spaces.",
        "question": "How many parking spaces are free, and how many are taken?",
        "counts": ["Total spaces", "Occupied", "Free"],
        "source_hint": "A car park camera or a drone photo",
        "focus": (
            "Count every marked parking space you can see. A space is occupied when a vehicle is in it, free when it is "
            "empty. Also note accessible (disabled) and EV-charging bays if you can see their markings. Findings are "
            "problems: a vehicle across two bays, a vehicle parked outside a bay, a blocked lane, entrance or fire lane, "
            "debris or obstacles in a bay. Verdict: clear when there are free spaces and nothing is blocked; attention when "
            "fewer than about 10% of spaces are free or a vehicle is parked badly; critical when a fire lane, emergency "
            "access or exit is blocked."
        ),
    },
    {
        "key": "footfall",
        "name": "Store foot traffic",
        "industry": "Retail & hospitality",
        "icon": "users",
        "tagline": "See how many people come in and go out.",
        "question": "How many people are walking in, and how many are walking out?",
        "counts": ["People in view", "Walking in", "Walking out", "Waiting in a queue"],
        "source_hint": "A camera pointed at a shop door or a short video of it",
        "focus": (
            "Count the people you can see. Decide for each person whether they are walking into the store, walking out, "
            "or just passing or standing, using which way they face and move (across video frames when there are several). "
            "Note queues at tills or doors and crowding. Never try to identify anyone; describe people only by position. "
            "Findings: crowding near the door, a long queue, a blocked entrance, a person who looks like they need help. "
            "Verdict: clear for normal traffic; attention for a long queue or crowding; critical for a blocked exit or an "
            "unsafe crowd."
        ),
    },
    {
        "key": "corrosion",
        "name": "Corrosion check",
        "industry": "Oil & gas, utilities, marine",
        "icon": "factory",
        "tagline": "Spot rust, pitting and coating damage on pipes and steel.",
        "question": "Is there corrosion or coating damage, and how bad is it?",
        "counts": ["Corroded areas", "Severe areas"],
        "source_hint": "Photos of pipe racks, flanges, tanks, valves or steel structures",
        "focus": (
            "Inspect every metal surface for corrosion: surface rust, scaling, pitting, blistering or flaking paint, "
            "coating breakdown, corrosion under insulation signs (stains, wet or damaged cladding), and corrosion at "
            "flanges, bolts, clamps, supports and welds. Grade each area in plain words: light (surface staining only), "
            "moderate (rust with coating loss), severe (scaling, pitting or visible metal loss). Say which part of the "
            "equipment it is on. Recommend practical next steps such as wire-brush and recoat, ultrasonic thickness "
            "measurement, or a close visual inspection by a qualified inspector. Verdict: clear for no or light corrosion; "
            "attention for moderate corrosion; critical for severe corrosion, visible metal loss, or anything near a "
            "leak or a pressure-containing joint."
        ),
    },
    {
        "key": "defects",
        "name": "Surface defects",
        "industry": "Manufacturing & quality",
        "icon": "scan",
        "tagline": "Find cracks, dents, scratches and bad welds.",
        "question": "Are there any defects on this part or surface?",
        "counts": ["Defects found", "Serious defects"],
        "source_hint": "Close-up photos of parts, welds, panels or products",
        "focus": (
            "Look for manufacturing and wear defects: cracks, dents, scratches, chips, deformation, porosity, missing "
            "parts or fasteners, misalignment, contamination, and weld problems (undercut, porosity, spatter, incomplete "
            "fusion, cracks). Describe each defect's type, size relative to the part, and location. Verdict: clear when "
            "the part looks acceptable; attention for cosmetic defects; critical for cracks, structural damage or "
            "anything that would make the part unsafe or unusable."
        ),
    },
    {
        "key": "ppe",
        "name": "Safety gear (PPE)",
        "industry": "Construction, plants & warehouses",
        "icon": "hardhat",
        "tagline": "Check that people wear helmets, vests and other safety gear.",
        "question": "Is everyone wearing the right safety gear?",
        "counts": ["People in view", "Wearing full PPE", "Missing some PPE"],
        "source_hint": "Site, plant or warehouse camera photos",
        "focus": (
            "Count the people. For each, check the protective equipment you can see: hard hat, high-visibility vest or "
            "clothing, safety glasses, gloves, safety boots, harness when working at height, hearing protection where "
            "relevant. Never try to identify anyone; describe people only by position. Also note other hazards: people "
            "under suspended loads, near moving vehicles, at unprotected edges. Verdict: clear when everyone wears the "
            "needed gear; attention when some gear is missing; critical when someone faces a serious hazard without "
            "protection."
        ),
    },
    {
        "key": "leaks",
        "name": "Leaks & spills",
        "industry": "Oil & gas, process plants, facilities",
        "icon": "droplet",
        "tagline": "Look for drips, stains, pools and vapour.",
        "question": "Is anything leaking or spilled?",
        "counts": ["Possible leaks", "Spills on the ground"],
        "source_hint": "Photos of pumps, valves, flanges, tanks or floors",
        "focus": (
            "Look for signs of leaks and spills: wet patches, drips, pooling liquid, oil or chemical stains, sheen on "
            "water, discoloured ground, frost or ice on fittings, vapour or steam where none is expected, and damaged "
            "containment or drip trays. Say where each sign is (which flange, valve, pump seal, hose or tank) and what "
            "the liquid looks like. Verdict: clear when nothing suggests a leak; attention for old stains or small drips; "
            "critical for an active leak, a large spill, vapour, or anything near an ignition source or drain."
        ),
    },
]

CUSTOM_TEMPLATE = {
    "key": CUSTOM,
    "name": "Your own question",
    "industry": "Anything you can photograph",
    "icon": "sparkle",
    "tagline": "Describe what you want to know in your own words.",
    "question": "",
    "counts": [],
    "source_hint": "Any photo or short video",
    "focus": (
        "Answer the person's question about the pictures as directly as you can. Count things when they ask how many. "
        "Choose the verdict from what they would consider a problem: clear when everything looks as they would hope, "
        "attention when something deserves a look, critical when something is unsafe or urgent."
    ),
}

BY_KEY = {item["key"]: item for item in [*TEMPLATES, CUSTOM_TEMPLATE]}
PUBLIC_FIELDS = ("key", "name", "industry", "icon", "tagline", "question", "counts", "source_hint")


def public(template: dict) -> dict:
    return {field: template[field] for field in PUBLIC_FIELDS}


def all_public() -> list[dict]:
    return [public(item) for item in [*TEMPLATES, CUSTOM_TEMPLATE]]
