"""Racing aliases: a keyed hash of the GitHub username, never reversible
without the salt, rendered as adjective + noun + two digits."""
import hashlib
import hmac

ADJECTIVES = [
    "Turbo", "Crimson", "Cobalt", "Neon", "Silent", "Rapid", "Blazing", "Frosty",
    "Golden", "Silver", "Scarlet", "Violet", "Amber", "Emerald", "Sapphire", "Ruby",
    "Onyx", "Ivory", "Copper", "Bronze", "Electric", "Sonic", "Hyper", "Nitro",
    "Quantum", "Rogue", "Stealth", "Nimble", "Bold", "Brave", "Clever", "Daring",
    "Eager", "Fierce", "Gentle", "Jolly", "Keen", "Lucky", "Mighty", "Noble",
    "Plucky", "Quick", "Radiant", "Sly", "Steady", "Tidy", "Vivid", "Witty",
    "Zesty", "Arctic", "Solar", "Lunar", "Cosmic", "Stellar", "Astral", "Polar",
    "Tropic", "Desert", "Alpine", "Coastal", "Urban", "Midnight", "Dawn", "Dusk",
    "Twilight", "Thunder", "Lightning", "Stormy", "Breezy", "Misty", "Foggy", "Sunny",
    "Rainy", "Snowy", "Windy", "Velvet", "Denim", "Marble", "Granite", "Crystal",
    "Laser", "Radar", "Sonar", "Orbital", "Zigzag", "Apex", "Drifting", "Humble",
    "Mellow", "Peppy", "Snappy", "Spiky", "Wobbly", "Zippy", "Dusty", "Mossy",
]

NOUNS = [
    "Falcon", "Hawk", "Eagle", "Raven", "Sparrow", "Owl", "Heron", "Crane",
    "Puffin", "Penguin", "Otter", "Badger", "Fox", "Wolf", "Lynx", "Panther",
    "Jaguar", "Cheetah", "Leopard", "Tiger", "Lion", "Cougar", "Bobcat", "Coyote",
    "Dingo", "Hound", "Beagle", "Collie", "Husky", "Mustang", "Stallion", "Pony",
    "Bison", "Moose", "Elk", "Ibex", "Gazelle", "Antelope", "Impala", "Rhino",
    "Hippo", "Walrus", "Dolphin", "Orca", "Marlin", "Shark", "Manta", "Turtle",
    "Gecko", "Iguana", "Cobra", "Viper", "Mamba", "Dragon", "Phoenix", "Griffin",
    "Kraken", "Comet", "Meteor", "Rocket", "Shuttle", "Glider", "Kite", "Arrow",
    "Dart", "Bolt", "Spark", "Ember", "Flame", "Torch", "Beacon", "Lantern",
    "Compass", "Anchor", "Rudder", "Piston", "Turbine", "Rotor", "Sprocket", "Wrench",
    "Magnet", "Circuit", "Diode", "Photon", "Proton", "Quark", "Neutrino", "Pixel",
    "Voxel", "Cipher", "Vertex", "Tensor", "Wombat", "Koala", "Yak", "Llama",
]


def _digest(salt: str, message: str) -> bytes:
    return hmac.new(salt.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()


def normalize(username: str) -> str:
    return username.strip().lower()


def alias(salt: str, username: str, variant: int = 1) -> str:
    """The alias for a username. `variant` > 1 resolves the rare collision
    between two usernames that hash to the same words."""
    suffix = "" if variant == 1 else f"#{variant}"
    d = _digest(salt, "alias:" + normalize(username) + suffix)
    adjective = ADJECTIVES[int.from_bytes(d[0:4], "big") % len(ADJECTIVES)]
    noun = NOUNS[int.from_bytes(d[4:8], "big") % len(NOUNS)]
    number = int.from_bytes(d[8:12], "big") % 90 + 10
    return f"{adjective} {noun} {number}"


def owner_fingerprint(salt: str, username: str) -> str:
    """A second keyed hash of the username, stored next to each alias so a
    later run can tell whether an alias already belongs to this user. Not
    reversible without the salt."""
    return _digest(salt, "owner:" + normalize(username)).hex()[:16]


def resolve_alias(salt: str, username: str, players: dict) -> str:
    """The alias this username owns in `players` (alias -> record with an
    `owner` fingerprint), assigning the first free variant when the natural
    alias is already taken by someone else."""
    fp = owner_fingerprint(salt, username)
    for variant in range(1, 50):
        candidate = alias(salt, username, variant)
        record = players.get(candidate)
        if record is None or record.get("owner") == fp:
            return candidate
    raise RuntimeError("could not find a free alias variant")
