import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "lost_found")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
TIMEZONE = "Asia/Kolkata"

# embedding model used for semantic matching (384-dim vectors)
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = 384
VECTOR_INDEX = "report_vectors"

# campus landmarks as (longitude, latitude) - GeoJSON uses lng first
# coordinates are approximate points around the MUJ campus, edit them if needed
CAMPUS_CENTER = (75.5650, 26.8433)
LOCATIONS = {
    "Main Gate": (75.5628, 26.8402),
    "Dome Building": (75.5646, 26.8428),
    "Academic Block 1": (75.5638, 26.8441),
    "Academic Block 2": (75.5659, 26.8446),
    "Academic Block 3": (75.5671, 26.8436),
    "Central Library": (75.5652, 26.8437),
    "Food Court": (75.5662, 26.8421),
    "Auditorium": (75.5631, 26.8430),
    "Sports Complex": (75.5684, 26.8452),
    "Gym": (75.5679, 26.8459),
    "Boys Hostel": (75.5693, 26.8468),
    "Girls Hostel": (75.5612, 26.8458),
    "Parking Area": (75.5620, 26.8414),
    "Health Centre": (75.5667, 26.8409),
}

# each category has its own set of fields: (key, label, options or None for free text)
# this is the "different items have different attributes" part of the project
CATEGORIES = {
    "Electronics": [
        ("device_type", "Device type", ["Phone", "Laptop", "Earbuds", "Charger", "Smartwatch", "Calculator", "Other"]),
        ("brand", "Brand", None),
        ("model", "Model", None),
        ("color", "Colour", None),
        ("has_case", "Has a case/cover?", ["Yes", "No"]),
    ],
    "ID / Cards": [
        ("card_type", "Card type", ["Student ID", "ATM/Debit card", "Aadhaar", "Driving licence", "Other"]),
        ("name_on_card", "Name on card", None),
        ("issuer", "Bank / issuer", None),
    ],
    "Wallet / Purse": [
        ("color", "Colour", None),
        ("brand", "Brand", None),
        ("material", "Material", ["Leather", "Fabric", "Synthetic", "Other"]),
        ("contents", "Contents (cards, cash...)", None),
    ],
    "Bags": [
        ("bag_type", "Bag type", ["Backpack", "Laptop bag", "Handbag", "Tote", "Pouch", "Other"]),
        ("color", "Colour", None),
        ("brand", "Brand", None),
    ],
    "Keys": [
        ("key_count", "Number of keys", None),
        ("keychain", "Keychain description", None),
        ("vehicle_key", "Includes a vehicle key?", ["Yes", "No"]),
    ],
    "Books / Stationery": [
        ("book_title", "Book or notebook title", None),
        ("subject", "Subject", None),
        ("name_written", "Name written inside", None),
    ],
    "Clothing": [
        ("clothing_type", "Type", ["Jacket", "Hoodie", "Sweater", "Cap", "Shoes", "Other"]),
        ("color", "Colour", None),
        ("size", "Size", None),
        ("brand", "Brand", None),
    ],
    "Water Bottle": [
        ("color", "Colour", None),
        ("brand", "Brand", None),
        ("stickers", "Stickers / marks", None),
    ],
    "Jewellery / Accessories": [
        ("item_type", "Type", ["Watch", "Ring", "Chain", "Bracelet", "Earrings", "Spectacles", "Other"]),
        ("color", "Colour", None),
        ("material", "Material", None),
    ],
    "Other": [],
}

# how much each signal counts towards the final match score (adds up to 1)
WEIGHTS = {"text": 0.45, "location": 0.20, "time": 0.15, "category": 0.10, "attributes": 0.10}
MATCH_THRESHOLD = 0.70   # minimum score to show a suggestion
MAX_DISTANCE_M = 800     # beyond this the location score is 0
MAX_GAP_HOURS = 24 * 7   # beyond a week the time score is 0
SEARCH_WINDOW_DAYS = 30  # only compare reports within this many days of each other

# bge cosine scores mostly sit between ~0.5 (unrelated) and ~0.9 (same thing),
# so we stretch that range to 0..1 before weighting it
TEXT_SIM_LOW = 0.50
TEXT_SIM_HIGH = 0.90
