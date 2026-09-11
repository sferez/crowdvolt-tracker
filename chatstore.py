"""Chat threads: validation, moderation, and compare-and-swap writes.

No HTTP here. `api/chat.py` is a thin adapter over this module, and
`track.py --serve` mounts the same functions locally, so the logic can be
exercised with `python3 -c` and no Vercel.

Threads live in their own R2 bucket (`env="CHAT_"`), for two reasons:

  * R2 tokens are scoped per bucket, not per prefix. The chat endpoint is the
    only part of this project that takes writes from the open internet, and a
    bug in it must not be able to reach the price history.
  * `snapshot.py` lists everything under the tracker's own prefix and the
    daily workflow commits it to a public git branch. A message moderated away
    at 14:00 would otherwise survive in that history for good.

One JSON object per thread, not one per message: the browser reads threads
straight from R2 and cannot list a bucket, so per-message objects would make
every poll a server call. The cost of that choice is a read-modify-write
cycle, which is why every write goes through `_swap`.
"""

import hmac
import json
import os
import random
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

import r2

# At import, not on first use. Without it the admin token and the Turnstile
# secret are absent until something happens to construct a Remote, which made
# authentication depend on the order requests arrived in -- the auth probe
# failed while a post that built the store first succeeded. On Vercel there is
# no .env.local so this is a no-op, and load_env uses setdefault, so real
# environment variables always win.
r2.load_env()

PREFIX = "v1/"
MAX_TEXT = 500            # characters, after normalising
MAX_BODY = 4096           # bytes on the wire, checked before parsing
KEEP = 200                # messages retained per thread
ATTEMPTS = 8              # compare-and-swap tries before giving up
COOLDOWN = 20             # seconds before the same tag may post again
RUN = 3                   # ... or more than this many of the last 5 messages
SLUG_TTL = 60             # seconds to cache the set of real event slugs
UA = "crowdvolt-tracker/1.0"

# The vocabulary a browser names itself from. dashboard.py bakes these same
# lists into the page, so the two cannot drift -- if they did, every message
# from a browser on the newer list would be rejected as a bad name, which is
# the kind of bug nobody finds until a stranger complains.
#
# Adapted from unique-names-generator (MIT, Copyright (c) 2018-2022
# AndreaSonny <andreasonny83@gmail.com>), filtered for tone: the originals
# were written for naming build artefacts, and a public site should not be
# able to call a stranger "Filthy Otter". ~395,007 combinations, which puts two
# Two people sharing a name stays well under one percent for any crowd
# this site will ever see.
ADJ = ["Able", "Above", "Absent", "Absolute", "Abstract", "Abundant",
       "Academic", "Acceptable", "Accepted", "Accessible", "Accurate",
       "Accused", "Active", "Actual", "Acute", "Added", "Additional",
       "Adequate", "Adjacent", "Administrative", "Adorable", "Advanced",
       "Adverse", "Advisory", "Aesthetic", "Afraid", "Aggregate",
       "Aggressive", "Agreeable", "Agreed", "Agricultural", "Alert",
       "Alleged", "Allied", "Alone", "Alright", "Alternative", "Amateur",
       "Amazing", "Ambitious", "Amused", "Annoyed", "Annual", "Anonymous",
       "Anxious", "Appalling", "Apparent", "Applicable", "Appropriate",
       "Arbitrary", "Architectural", "Armed", "Arrogant", "Artificial",
       "Artistic", "Ashamed", "Asleep", "Assistant", "Associated", "Atomic",
       "Automatic", "Autonomous", "Available", "Average", "Awake", "Aware",
       "Awkward", "Back", "Balanced", "Bare", "Basic", "Beneficial",
       "Better", "Bewildered", "Big", "Binding", "Biological", "Bizarre",
       "Blank", "Blonde", "Bloody", "Blushing", "Boiling", "Bold", "Bored",
       "Bottom", "Brainy", "Brave", "Breakable", "Breezy", "Brief", "Bright",
       "Brilliant", "Broad", "Broken", "Bumpy", "Burning", "Busy", "Calm",
       "Capable", "Capitalist", "Careful", "Casual", "Causal", "Cautious",
       "Central", "Changing", "Characteristic", "Charming", "Cheap",
       "Cheerful", "Chemical", "Chief", "Chilly", "Chosen", "Chronic",
       "Chubby", "Circular", "Civic", "Civil", "Civilian", "Classic",
       "Classical", "Clean", "Clear", "Clever", "Clinical", "Close",
       "Closed", "Cloudy", "Coastal", "Cognitive", "Coherent", "Cold",
       "Collective", "Colonial", "Colorful", "Colossal", "Coloured",
       "Colourful", "Combative", "Combined", "Comfortable", "Coming",
       "Commercial", "Common", "Communist", "Compact", "Comparable",
       "Comparative", "Compatible", "Competent", "Competitive", "Complete",
       "Complex", "Complicated", "Comprehensive", "Compulsory", "Conceptual",
       "Concerned", "Concrete", "Condemned", "Confident", "Confidential",
       "Confused", "Conscious", "Conservation", "Conservative",
       "Considerable", "Consistent", "Constant", "Constitutional",
       "Contemporary", "Content", "Continental", "Continued", "Continuing",
       "Continuous", "Controlled", "Controversial", "Convenient",
       "Conventional", "Convinced", "Convincing", "Cooing", "Cool",
       "Cooperative", "Corporate", "Correct", "Corresponding", "Costly",
       "Courageous", "Creative", "Critical", "Crooked", "Crucial", "Crude",
       "Cuddly", "Cultural", "Curious", "Curly", "Current", "Curved", "Cute",
       "Daily", "Damp", "Dangerous", "Dark", "Deafening", "Dear", "Decent",
       "Decisive", "Deep", "Defensive", "Defiant", "Definite", "Deliberate",
       "Delicate", "Delicious", "Delighted", "Delightful", "Democratic",
       "Dependent", "Depressed", "Desirable", "Desperate", "Detailed",
       "Determined", "Developed", "Developing", "Devoted", "Different",
       "Difficult", "Digital", "Diplomatic", "Direct", "Disappointed",
       "Disastrous", "Disciplinary", "Disgusted", "Distant", "Distinct",
       "Distinctive", "Distinguished", "Disturbed", "Disturbing", "Diverse",
       "Divine", "Dizzy", "Domestic", "Dominant", "Double", "Doubtful",
       "Drab", "Dramatic", "Dreadful", "Driving", "Dual", "Dusty", "Dutch",
       "Dying", "Dynamic", "Eager", "Early", "Eastern", "Easy", "Economic",
       "Educational", "Eerie", "Effective", "Efficient", "Elaborate",
       "Elated", "Eldest", "Electoral", "Electric", "Electrical",
       "Electronic", "Elegant", "Eligible", "Embarrassed", "Embarrassing",
       "Emotional", "Empirical", "Empty", "Enchanting", "Encouraging",
       "Endless", "Energetic", "Enormous", "Enthusiastic", "Entire",
       "Entitled", "Envious", "Environmental", "Equal", "Equivalent",
       "Essential", "Established", "Estimated", "Ethical", "Eventual",
       "Everyday", "Evident", "Evolutionary", "Exact", "Excellent",
       "Exceptional", "Excess", "Excessive", "Excited", "Exciting",
       "Exclusive", "Existing", "Exotic", "Expected", "Expensive",
       "Experienced", "Experimental", "Explicit", "Extended", "Extensive",
       "External", "Extra", "Extraordinary", "Extreme", "Exuberant", "Faint",
       "Fair", "Faithful", "Familiar", "Famous", "Fancy", "Fantastic",
       "Fascinating", "Fashionable", "Fast", "Favourable", "Favourite",
       "Federal", "Fellow", "Feminist", "Fierce", "Final", "Financial",
       "Fine", "Firm", "Fiscal", "Fit", "Fixed", "Flaky", "Flat", "Flexible",
       "Fluffy", "Fluttering", "Flying", "Following", "Fond", "Foolish",
       "Formal", "Formidable", "Forthcoming", "Fortunate", "Forward",
       "Fragile", "Frantic", "Free", "Frequent", "Fresh", "Friendly",
       "Frightened", "Front", "Frozen", "Full", "Fun", "Functional",
       "Fundamental", "Funny", "Furious", "Future", "Fuzzy", "Gastric",
       "General", "Generous", "Genetic", "Gentle", "Genuine", "Geographical",
       "Giant", "Gigantic", "Given", "Glad", "Glamorous", "Gleaming",
       "Global", "Glorious", "Golden", "Good", "Gothic", "Governing",
       "Gradual", "Grand", "Grateful", "Greasy", "Great", "Grieving", "Grim",
       "Grotesque", "Growing", "Grubby", "Grumpy", "Handicapped", "Happy",
       "Hard", "Head", "Healthy", "Heavy", "Helpful", "Hidden", "High",
       "Hilarious", "Hissing", "Historic", "Historical", "Hollow", "Holy",
       "Homely", "Honest", "Horizontal", "Horrible", "Huge", "Human",
       "Hungry", "Hurt", "Hushed", "Husky", "Icy", "Ideal", "Identical",
       "Ideological", "Illegal", "Imaginative", "Immediate", "Immense",
       "Imperial", "Implicit", "Important", "Impossible", "Impressed",
       "Impressive", "Improved", "Inadequate", "Inappropriate", "Inclined",
       "Increased", "Increasing", "Incredible", "Independent", "Indirect",
       "Individual", "Industrial", "Inevitable", "Influential", "Informal",
       "Inherent", "Initial", "Injured", "Inland", "Inner", "Innocent",
       "Innovative", "Inquisitive", "Instant", "Institutional",
       "Insufficient", "Intact", "Integral", "Integrated", "Intellectual",
       "Intelligent", "Intense", "Intensive", "Interested", "Interesting",
       "Interim", "Interior", "Intermediate", "Internal", "International",
       "Intimate", "Invisible", "Involved", "Irrelevant", "Isolated",
       "Itchy", "Jittery", "Joint", "Jolly", "Joyous", "Judicial", "Juicy",
       "Junior", "Just", "Keen", "Kind", "Known", "Labour", "Large", "Late",
       "Leading", "Left", "Legal", "Legislative", "Legitimate", "Lengthy",
       "Lesser", "Level", "Lexical", "Liable", "Liberal", "Light", "Like",
       "Likely", "Limited", "Linear", "Linguistic", "Liquid", "Literary",
       "Little", "Live", "Lively", "Living", "Local", "Logical", "Long",
       "Loose", "Lost", "Loud", "Lovely", "Loyal", "Lucky", "Magic",
       "Magnetic", "Magnificent", "Main", "Major", "Mammoth", "Managerial",
       "Managing", "Manual", "Many", "Marginal", "Marine", "Marked",
       "Marvellous", "Marxist", "Mass", "Massive", "Mathematical", "Mature",
       "Maximum", "Meaningful", "Mechanical", "Medical", "Medieval",
       "Melodic", "Melted", "Mental", "Metropolitan", "Middle", "Mighty",
       "Mild", "Military", "Miniature", "Minimal", "Minimum", "Ministerial",
       "Minor", "Misleading", "Missing", "Misty", "Mixed", "Moaning",
       "Mobile", "Moderate", "Modern", "Modest", "Molecular", "Monetary",
       "Monthly", "Moral", "Motionless", "Muddy", "Multiple", "Mushy",
       "Musical", "Mute", "Mutual", "Mysterious", "Narrow", "National",
       "Natural", "Naval", "Near", "Nearby", "Neat", "Necessary", "Negative",
       "Neighbouring", "Neutral", "New", "Nice", "Noble", "Noisy", "Normal",
       "Northern", "Nosy", "Notable", "Novel", "Nuclear", "Numerous",
       "Nursing", "Nutritious", "Nutty", "Obedient", "Objective", "Obliged",
       "Obvious", "Occasional", "Occupational", "Official", "Okay",
       "Olympic", "Open", "Operational", "Opposite", "Optimistic", "Oral",
       "Ordinary", "Organic", "Organisational", "Original", "Orthodox",
       "Other", "Outdoor", "Outer", "Outrageous", "Outside", "Outstanding",
       "Overseas", "Overwhelming", "Painful", "Panicky", "Parallel",
       "Parental", "Parliamentary", "Partial", "Particular", "Passing",
       "Passive", "Past", "Patient", "Payable", "Peaceful", "Peculiar",
       "Perfect", "Permanent", "Persistent", "Personal", "Petite",
       "Philosophical", "Physical", "Plain", "Planned", "Plastic",
       "Pleasant", "Pleased", "Poised", "Polite", "Popular", "Positive",
       "Possible", "Potential", "Powerful", "Practical", "Precious",
       "Precise", "Preferred", "Preliminary", "Premier", "Prepared",
       "Present", "Presidential", "Previous", "Prickly", "Primary", "Prime",
       "Principal", "Printed", "Prior", "Private", "Probable", "Productive",
       "Professional", "Profitable", "Profound", "Progressive", "Prominent",
       "Promising", "Proper", "Proposed", "Prospective", "Protective",
       "Protestant", "Proud", "Provincial", "Psychiatric", "Psychological",
       "Public", "Puny", "Pure", "Purring", "Puzzled", "Quaint", "Qualified",
       "Quarrelsome", "Querulous", "Quick", "Quickest", "Quiet",
       "Quintessential", "Quixotic", "Radical", "Rainy", "Random", "Rapid",
       "Rare", "Raspy", "Rational", "Ratty", "Ready", "Real", "Realistic",
       "Rear", "Reasonable", "Recent", "Reduced", "Redundant", "Regional",
       "Registered", "Regular", "Regulatory", "Related", "Relative",
       "Relaxed", "Relevant", "Reliable", "Relieved", "Reluctant",
       "Remaining", "Remarkable", "Remote", "Renewed", "Representative",
       "Repulsive", "Required", "Resident", "Residential", "Resonant",
       "Respectable", "Respective", "Responsible", "Resulting", "Retail",
       "Retired", "Revolutionary", "Ridiculous", "Right", "Rigid", "Ripe",
       "Rising", "Rival", "Roasted", "Robust", "Rolling", "Romantic",
       "Rough", "Round", "Royal", "Rubber", "Ruling", "Running", "Rural",
       "Sacred", "Safe", "Salty", "Satisfactory", "Satisfied", "Scary",
       "Scattered", "Scientific", "Scornful", "Scrawny", "Screeching",
       "Secondary", "Secret", "Secure", "Select", "Selected", "Selective",
       "Semantic", "Senior", "Sensible", "Sensitive", "Separate", "Serious",
       "Severe", "Shaggy", "Shaky", "Shared", "Sharp", "Sheer", "Shiny",
       "Shivering", "Shocked", "Shrill", "Significant", "Silent", "Silky",
       "Silly", "Similar", "Simple", "Sleepy", "Slight", "Slimy", "Slippery",
       "Slow", "Small", "Smart", "Smiling", "Smoggy", "Smooth", "Social",
       "Socialist", "Soft", "Solar", "Solid", "Sophisticated", "Sorry",
       "Sound", "Southern", "Soviet", "Spare", "Sparkling", "Spatial",
       "Special", "Specific", "Specified", "Spectacular", "Spicy",
       "Spiritual", "Splendid", "Spontaneous", "Sporting", "Spotless",
       "Spotty", "Square", "Squealing", "Stable", "Stale", "Standard",
       "Static", "Statistical", "Statutory", "Steady", "Steep", "Sticky",
       "Stiff", "Still", "Stingy", "Stormy", "Straightforward", "Strategic",
       "Strict", "Striking", "Striped", "Strong", "Structural", "Stuck",
       "Subjective", "Subsequent", "Substantial", "Subtle", "Successful",
       "Successive", "Sudden", "Sufficient", "Suitable", "Sunny", "Super",
       "Superb", "Superior", "Supporting", "Supposed", "Supreme", "Sure",
       "Surprised", "Surprising", "Surrounding", "Surviving", "Suspicious",
       "Sweet", "Swift", "Symbolic", "Sympathetic", "Systematic", "Tame",
       "Tart", "Tasteless", "Tasty", "Technical", "Technological", "Teenage",
       "Temporary", "Tender", "Terrible", "Territorial", "Testy", "Then",
       "Theoretical", "Thick", "Thirsty", "Thorough", "Thoughtful",
       "Thoughtless", "Thundering", "Tight", "Tiny", "Top", "Tory", "Total",
       "Tough", "Traditional", "Tragic", "Tremendous", "Tricky", "Tropical",
       "Troubled", "Typical", "Ugliest", "Ultimate", "Unacceptable",
       "Unaware", "Uncertain", "Unchanged", "Uncomfortable", "Unconscious",
       "Underground", "Underlying", "Uneven", "Unexpected", "Unfair",
       "Unfortunate", "Uniform", "Uninterested", "Unique", "United",
       "Universal", "Unknown", "Unlikely", "Unnecessary", "Unpleasant",
       "Unwilling", "Upper", "Uptight", "Urban", "Urgent", "Used", "Useful",
       "Usual", "Vague", "Valid", "Valuable", "Variable", "Varied",
       "Varying", "Vast", "Verbal", "Vertical", "Vicarious", "Vicious",
       "Victorious", "Visible", "Visiting", "Visual", "Vital", "Vitreous",
       "Vivacious", "Vivid", "Vocal", "Vocational", "Voiceless",
       "Voluminous", "Voluntary", "Vulnerable", "Wandering", "Warm",
       "Wasteful", "Watery", "Weekly", "Weird", "Welcome", "Well", "Western",
       "Whispering", "Wide", "Widespread", "Wild", "Wilful", "Willing",
       "Willowy", "Wily", "Wise", "Wispy", "Wittering", "Witty", "Wonderful",
       "Wooden", "Working", "Worldwide", "Worrying", "Worthwhile", "Worthy",
       "Written", "Wrong", "Xenacious", "Xenial", "Xenogeneic", "Xenophobic",
       "Xeric", "Xerothermic", "Yabbering", "Yammering", "Yappiest", "Yappy",
       "Yawning", "Yearling", "Yearning", "Yeasty", "Yelling", "Yelping",
       "Yielding", "Yodelling", "Youngest", "Youthful", "Ytterbic", "Yucky",
       "Yummy", "Zany", "Zealous", "Zeroth", "Zestful", "Zesty", "Zippy",
       "Zonal", "Zoophagous", "Zygomorphic", "Zygotic"]
NOUN = ["Aardvark", "Aardwolf", "Albatross", "Alligator", "Alpaca",
        "Amphibian", "Anaconda", "Angelfish", "Anglerfish", "Ant",
        "Anteater", "Antelope", "Antlion", "Ape", "Aphid", "Armadillo",
        "Asp", "Baboon", "Badger", "Bandicoot", "Barnacle", "Barracuda",
        "Basilisk", "Bass", "Bat", "Bear", "Beaver", "Bedbug", "Bee",
        "Beetle", "Bird", "Bison", "Blackbird", "Boa", "Boar", "Bobcat",
        "Bobolink", "Bonobo", "Booby", "Bovid", "Bug", "Butterfly",
        "Buzzard", "Camel", "Canid", "Canidae", "Capybara", "Cardinal",
        "Caribou", "Carp", "Cat", "Caterpillar", "Catfish", "Catshark",
        "Cattle", "Centipede", "Cephalopod", "Chameleon", "Cheetah",
        "Chickadee", "Chicken", "Chimpanzee", "Chinchilla", "Chipmunk",
        "Cicada", "Clam", "Clownfish", "Cobra", "Cockroach", "Cod", "Condor",
        "Constrictor", "Coral", "Cougar", "Cow", "Coyote", "Crab", "Crane",
        "Crawdad", "Crayfish", "Cricket", "Crocodile", "Crow", "Cuckoo",
        "Damselfly", "Deer", "Dingo", "Dinosaur", "Dog", "Dolphin", "Donkey",
        "Dormouse", "Dove", "Dragon", "Dragonfly", "Duck", "Eagle",
        "Earthworm", "Earwig", "Echidna", "Eel", "Egret", "Elephant", "Elk",
        "Emu", "Ermine", "Falcon", "Felidae", "Ferret", "Finch", "Firefly",
        "Fish", "Flamingo", "Flea", "Fly", "Flyingfish", "Fowl", "Fox",
        "Frog", "Galliform", "Gamefowl", "Gayal", "Gazelle", "Gecko",
        "Gerbil", "Gibbon", "Giraffe", "Goat", "Goldfish", "Goose", "Gopher",
        "Gorilla", "Grasshopper", "Grouse", "Guan", "Guanaco", "Guineafowl",
        "Gull", "Guppy", "Haddock", "Halibut", "Hamster", "Hare", "Harrier",
        "Hawk", "Hedgehog", "Heron", "Herring", "Hippopotamus", "Hookworm",
        "Hornet", "Horse", "Hoverfly", "Hummingbird", "Hyena", "Iguana",
        "Impala", "Jackal", "Jaguar", "Jay", "Jellyfish", "Junglefowl",
        "Kangaroo", "Kingfisher", "Kite", "Kiwi", "Koala", "Koi", "Krill",
        "Ladybug", "Lamprey", "Landfowl", "Lark", "Leech", "Lemming",
        "Lemur", "Leopard", "Leopon", "Limpet", "Lion", "Lizard", "Llama",
        "Lobster", "Locust", "Loon", "Louse", "Lungfish", "Lynx", "Macaw",
        "Mackerel", "Magpie", "Mammal", "Manatee", "Mandrill", "Marlin",
        "Marmoset", "Marmot", "Marsupial", "Marten", "Mastodon",
        "Meadowlark", "Meerkat", "Mink", "Minnow", "Mite", "Mockingbird",
        "Mole", "Mollusk", "Mongoose", "Monkey", "Moose", "Mosquito", "Moth",
        "Mouse", "Mule", "Muskox", "Narwhal", "Newt", "Nightingale",
        "Ocelot", "Octopus", "Opossum", "Orangutan", "Orca", "Ostrich",
        "Otter", "Owl", "Ox", "Panda", "Panther", "Parakeet", "Parrot",
        "Parrotfish", "Partridge", "Peacock", "Peafowl", "Pelican",
        "Penguin", "Perch", "Pheasant", "Pig", "Pigeon", "Pike", "Pinniped",
        "Piranha", "Planarian", "Platypus", "Pony", "Porcupine", "Porpoise",
        "Possum", "Prawn", "Primate", "Ptarmigan", "Puffin", "Puma",
        "Python", "Quail", "Quelea", "Quokka", "Rabbit", "Raccoon", "Rat",
        "Rattlesnake", "Raven", "Reindeer", "Reptile", "Rhinoceros",
        "Roadrunner", "Rodent", "Rook", "Rooster", "Roundworm", "Sailfish",
        "Salamander", "Salmon", "Sawfish", "Scallop", "Scorpion", "Seahorse",
        "Shark", "Sheep", "Shrew", "Shrimp", "Silkworm", "Silverfish",
        "Skink", "Skunk", "Sloth", "Slug", "Smelt", "Snail", "Snake",
        "Snipe", "Sparrow", "Spider", "Spoonbill", "Squid", "Squirrel",
        "Starfish", "Stingray", "Stoat", "Stork", "Sturgeon", "Swallow",
        "Swan", "Swordfish", "Swordtail", "Tahr", "Takin", "Tapir",
        "Tarantula", "Tarsier", "Termite", "Tern", "Thrush", "Tick", "Tiger",
        "Tiglon", "Toad", "Tortoise", "Toucan", "Trout", "Tuna", "Turkey",
        "Turtle", "Tyrannosaurus", "Unicorn", "Urial", "Vicuna", "Viper",
        "Vole", "Vulture", "Wallaby", "Walrus", "Warbler", "Wasp", "Weasel",
        "Whale", "Whippet", "Whitefish", "Wildcat", "Wildebeest", "Wildfowl",
        "Wolf", "Wolverine", "Wombat", "Woodpecker", "Worm", "Wren",
        "Xerinae", "Yak", "Zebra"]
NAMES = frozenset(f"{a} {n}" for a in ADJ for n in NOUN)

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
TAG_RE = re.compile(r"^[0-9A-Z]{4}$")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
# C0/C1 controls except tab and newline, plus the bidi overrides that let a
# message render as something other than what it says
JUNK_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f‪-‮⁦-⁩]")


class Rejected(Exception):
    """Bad input. The caller answers 400 and shows the message to the user."""


class Busy(Exception):
    """Too much contention on one thread. The caller answers 503."""


class NotConfigured(Exception):
    """Credentials missing. The caller answers 500 -- loudly, because a
    silent no-op here would return 200 and eat the message."""


# ------------------------------------------------------------------ store

_remote = None


def remote():
    global _remote
    if _remote is None:
        _remote = r2.Remote(prefix=PREFIX, env="CHAT_")
    if not _remote.enabled:
        raise NotConfigured("R2_CHAT_* credentials are not set")
    return _remote


def rel_of(thread):
    return thread + ".json"


def new_thread(thread):
    return {"v": 1, "thread": thread, "total": 0, "removed": 0,
            "updated": 0, "msgs": []}


def _swap(thread, apply):
    """Read, apply, conditionally write; retry when someone got there first.

    `apply(doc)` mutates the thread in place and may raise Rejected -- the
    per-thread rate limits live inside it, because by then the last messages
    have already been read and the check costs nothing extra.
    """
    R, rel = remote(), rel_of(thread)
    for attempt in range(ATTEMPTS):
        etag, doc = R.get_signed(rel)
        if doc is None:
            doc = new_thread(thread)
            etag = None               # create-only: If-None-Match
        apply(doc)
        doc["updated"] = int(time.time())
        doc["msgs"] = doc["msgs"][-KEEP:]
        if R.put_cond(rel, doc, etag=etag, cache=5):
            return doc
        # Lost the race. Re-reading picks up the other writer's message and
        # re-applies ours on top, so nothing is dropped. A failed create just
        # finds the thread present on the next pass and takes the update path.
        # Backoff has to clear a whole round trip, not a token pause: each
        # attempt costs a signed GET plus a PUT (~300ms), so contenders that
        # wake too early simply collide again. Capped, and heavily jittered
        # so a burst spreads out instead of re-colliding in lockstep.
        time.sleep(min(0.25 * (2 ** attempt), 2.0) * (0.5 + random.random()))
    raise Busy(f"{thread} is busy")


# ------------------------------------------------------------- validation

def tag_for(n):
    """A 4-character discriminator derived from the browser's stored id.

    Hashed rather than sliced: 36**4 is divisible by 12*12, so the low base-36
    digits of the id would be determined by the same arithmetic that picks the
    adjective and noun, leaving far less independent entropy than it looks.
    FNV-1a over the whole id gives ~1.68M name+tag combinations."""
    h = 2166136261
    for ch in str(n):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    out, v = "", h % (36 ** 4)
    for _ in range(4):
        out, v = "0123456789abcdefghijklmnopqrstuvwxyz"[v % 36] + out, v // 36
    return out.upper()


def clean(text):
    """Normalise and bound a message, or refuse it."""
    if not isinstance(text, str):
        raise Rejected("message must be text")
    t = unicodedata.normalize("NFC", text)
    t = JUNK_RE.sub("", t).replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if not t:
        raise Rejected("message is empty")
    # refused rather than truncated: silently publishing half of what someone
    # wrote is worse than telling them it was too long
    if len(t) > MAX_TEXT:
        raise Rejected(f"message is over {MAX_TEXT} characters")
    return t


def check_identity(name, tag):
    """Shape only. Without accounts a name cannot be *verified* -- anyone can
    post under any of them, which the UI says outright. What this does stop is
    the name field being used as a second, unmoderated message body."""
    if name not in NAMES:
        raise Rejected("unrecognised name")
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        raise Rejected("unrecognised tag")
    return name, tag


_slugs = {"at": 0, "set": None}


def data_base():
    """Where the tracker's own index.json is published.

    Derived from R2_PUBLIC_BASE by default, because the two differ only by
    the data prefix and asking for both invites setting DATA_PUBLIC_BASE to
    the bucket root -- which 404s, and since slug checking fails closed would
    quietly refuse every event post. R2_PUBLIC_BASE is a public URL, not a
    credential, so it is safe to give the chat function."""
    base = (os.environ.get("DATA_PUBLIC_BASE") or "").strip()
    if not base:
        pub = (os.environ.get("R2_PUBLIC_BASE") or "").strip()
        if not pub:
            return None
        base = pub.rstrip("/") + "/" + r2.PREFIX
    # the Cloudflare UI shows a custom domain without a scheme, and Remote
    # already tolerates that, so a value copied from there must work here too
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base.rstrip("/")


def slugs():
    """The event slugs that actually exist, from the tracker's public
    index.json. Read unsigned over plain HTTP from a non-secret URL, so the
    chat function never holds credentials for the data bucket.

    `None` means "cannot say" -- unconfigured, or the read failed and nothing
    has ever been cached. The difference matters: an empty set would read as
    "no events exist" and, handled carelessly, turn the check that stops
    arbitrary object creation into a no-op."""
    base = data_base()
    if not base:
        return None
    now = time.time()
    if _slugs["set"] is not None and now - _slugs["at"] < SLUG_TTL:
        return _slugs["set"]
    try:
        req = urllib.request.Request(base + "/index.json",
                                     headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            doc = json.loads(r.read().decode())
        _slugs.update(at=now, set=frozenset(doc.get("events") or ()))
    except Exception:
        pass                                    # keep serving the last set
    return _slugs["set"]


def check_thread(scope, slug=None):
    """`general`, or `event/<slug>` for a slug the tracker actually has.

    The existence check is what keeps this a chat rather than an unbounded
    object store that anyone on the internet can write into."""
    if scope == "general":
        return "general"
    if scope != "event":
        raise Rejected("unknown scope")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise Rejected("bad event")
    known = slugs()
    if known is None:
        if data_base():
            # configured but unreachable: refuse rather than wave it through.
            # Failing open here would let anyone create unlimited objects in
            # the bucket, which is the one thing this check exists to stop.
            raise Busy("cannot check the event right now")
        return "event/" + slug          # unconfigured: local development
    if slug not in known:
        raise Rejected("no such event")
    return "event/" + slug


# -------------------------------------------------------------- turnstile

def deployed():
    """True on any Vercel deployment, preview included. One rule, so there is
    no knob that can accidentally ship an endpoint with the challenge off."""
    return bool(os.environ.get("VERCEL"))


def verify_turnstile(token, ip=None):
    # Never locally: the page uses Cloudflare's dummy sitekey there, which the
    # real secret would reject, and a development machine should not need to
    # be online to post. One rule keyed on VERCEL, so the challenge is
    # enforced on every deployment and there is no switch to get wrong.
    if not deployed():
        return
    secret = os.environ.get("TURNSTILE_SECRET_KEY")
    if not secret:
        raise NotConfigured("TURNSTILE_SECRET_KEY is not set")
    if not token:
        raise Rejected("challenge missing")
    body = {"secret": secret, "response": token,
            "idempotency_key": uuid.uuid4().hex}
    if ip:
        body["remoteip"] = ip                   # sent to Cloudflare, never stored
    data = urllib.parse.urlencode(body).encode()
    try:
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify", data=data)
        with urllib.request.urlopen(req, timeout=8) as r:
            out = json.loads(r.read().decode())
    except Exception:
        # fail closed: minutes of "posting unavailable" beat an open relay
        raise Busy("could not reach the challenge service")
    if not out.get("success"):
        raise Rejected("challenge failed")
    if out.get("action") not in (None, "chat"):
        raise Rejected("challenge failed")
    allowed = [h for h in (os.environ.get("TURNSTILE_HOSTS") or "").split(",") if h.strip()]
    host = out.get("hostname")
    # without this, anyone can put your public site key on their own page and
    # farm valid tokens for this endpoint
    if allowed and host and host not in [h.strip() for h in allowed]:
        raise Rejected("challenge failed")


def is_admin(token):
    want = os.environ.get("CHAT_ADMIN_TOKEN") or ""
    # a short or unset token must fail closed rather than authenticate everyone
    return len(want) >= 24 and hmac.compare_digest(str(token or ""), want)


# ----------------------------------------------------------------- writes

def post(thread, name, tag, text):
    name, tag = check_identity(name, tag)
    text = clean(text)
    msg = {"id": uuid.uuid4().hex[:12], "t": int(time.time()),
           "n": name, "g": tag, "m": text}

    def apply(doc):
        recent = doc["msgs"]
        # The limits live here because the thread has just been read anyway.
        # They target the realistic abuse -- one person flooding -- without a
        # counter store, which a stateless function has nowhere to keep.
        if recent:
            last = recent[-1]
            if last.get("g") == tag and msg["t"] - last.get("t", 0) < COOLDOWN:
                raise Rejected("wait a moment before posting again")
            if last.get("g") == tag and last.get("m") == text:
                raise Rejected("that is the same as your last message")
        if sum(1 for m in recent[-5:] if m.get("g") == tag) >= RUN:
            raise Rejected("you are posting too quickly")
        recent.append(msg)
        doc["total"] = doc.get("total", 0) + 1

    _swap(thread, apply)
    return msg


def remove(thread, msg_id):
    if not ID_RE.match(str(msg_id or "")):
        raise Rejected("bad id")
    gone = []

    def apply(doc):
        before = len(doc["msgs"])
        # Hard removal, not a tombstone: "[removed]" still has to be rendered
        # and only invites asking what it said. It leaves R2 at once, and it
        # was never in the git snapshot.
        doc["msgs"] = [m for m in doc["msgs"] if m.get("id") != msg_id]
        gone.append(before - len(doc["msgs"]))
        doc["removed"] = doc.get("removed", 0) + gone[-1]

    _swap(thread, apply)
    return gone[-1] if gone else 0


def clear(thread):
    """Empty a thread. One repeat flooder is the realistic case, and deleting
    forty messages one at a time is forty round trips and a bad five minutes."""
    n = []

    def apply(doc):
        n.append(len(doc["msgs"]))
        doc["removed"] = doc.get("removed", 0) + n[-1]
        doc["msgs"] = []

    _swap(thread, apply)
    return n[-1] if n else 0


def read(thread):
    """Server-side read, for the response to a write. Browsers read the
    object straight from R2 instead."""
    _, doc = remote().get_signed(rel_of(thread))
    return doc or new_thread(thread)
