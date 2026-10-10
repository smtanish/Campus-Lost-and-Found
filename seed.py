"""Reset the database and fill it with realistic sample reports.
Run: python seed.py   (this deletes existing reports, claims and photos)"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import config
from db import db, reports, claims, setup
from matching import embed, report_text

IST = ZoneInfo(config.TIMEZONE)
TODAY = datetime.now(IST).replace(second=0, microsecond=0)


def at(days_ago, hh, mm=0):
    # a time in IST, N days ago, stored as UTC
    return (TODAY - timedelta(days=days_ago)).replace(hour=hh, minute=mm).astimezone(timezone.utc)


def person(name):
    return {"name": name, "email": name.lower().replace(" ", ".") + "@muj.manipal.edu"}


# (type, category, title, description, place, time, attributes, reporter, extra)
DATA = [
    # pair 1 - the example from the project idea
    ("lost", "Electronics", "Black AirPods Pro", "Black AirPods Pro in a black silicone case, lost while studying.", "Central Library", at(2, 16), {"device_type": "Earbuds", "brand": "Apple", "model": "AirPods Pro", "color": "Black", "has_case": "Yes"}, person("Aarav Mehta"), {}),
    ("found", "Electronics", "Wireless earbuds in a case", "White/black wireless earbuds with charging case, found on a table near the entrance.", "Central Library", at(2, 16, 30), {"device_type": "Earbuds", "color": "White/black", "has_case": "Yes"}, person("Neha Verma"), {"private_note": "Case has a small scratch shaped like an X on the back"}),
    # pair 2
    ("lost", "Water Bottle", "Blue Milton steel bottle", "1 litre blue steel bottle with a few stickers, left after football practice.", "Sports Complex", at(4, 18), {"color": "Blue", "brand": "Milton", "stickers": "NASA sticker and a cricket bat sticker"}, person("Kabir Singh"), {}),
    ("found", "Water Bottle", "Steel water bottle with stickers", "Blue metal bottle with space themed stickers, found near the gym benches.", "Gym", at(3, 9), {"color": "Blue", "stickers": "NASA logo"}, person("Ishita Rao"), {}),
    # pair 3
    ("lost", "ID / Cards", "Student ID card", "My MUJ student ID card, probably fell out of my pocket between classes.", "Academic Block 2", at(1, 11), {"card_type": "Student ID", "name_on_card": "Riya Sharma"}, person("Riya Sharma"), {}),
    ("found", "ID / Cards", "MUJ ID card found", "Student identity card lying near the food court entrance.", "Food Court", at(1, 13), {"card_type": "Student ID", "name_on_card": "Riya Sharma"}, person("Arjun Nair"), {}),
    # pair 4
    ("lost", "Wallet / Purse", "Brown leather wallet", "Brown leather bifold wallet with my debit card and around 500 rupees.", "Food Court", at(3, 13, 15), {"color": "Brown", "material": "Leather", "brand": "Woodland", "contents": "Debit card, cash, metro card"}, person("Dev Patel"), {}),
    ("found", "Wallet / Purse", "Wallet found under a table", "Brown wallet found under a table at the food court, has some cards and cash.", "Food Court", at(3, 14), {"color": "Brown", "material": "Leather"}, person("Sneha Iyer"), {"private_note": "Has a photo of a dog in the front slot"}),
    # pair 5
    ("lost", "Electronics", "Casio fx-991ES calculator", "Scientific calculator left in the exam hall after the maths quiz.", "Academic Block 1", at(5, 12), {"device_type": "Calculator", "brand": "Casio", "model": "fx-991ES Plus", "color": "Black"}, person("Manav Gupta"), {}),
    ("found", "Electronics", "Scientific calculator", "Casio calculator found on a desk in room 105.", "Academic Block 1", at(5, 14, 30), {"device_type": "Calculator", "brand": "Casio", "color": "Grey"}, person("Pooja Das"), {"private_note": "Name scratched on battery cover"}),
    # pair 6
    ("lost", "Keys", "Bike keys with Royal Enfield keychain", "Bunch of 3 keys including my bike key, metal Royal Enfield keychain.", "Parking Area", at(2, 9), {"key_count": "3", "keychain": "Royal Enfield metal keychain", "vehicle_key": "Yes"}, person("Rohan Joshi"), {}),
    ("found", "Keys", "Bunch of keys", "Three keys on a ring with a motorcycle keychain, found near the security cabin.", "Main Gate", at(2, 12), {"key_count": "3", "keychain": "Motorcycle logo keychain", "vehicle_key": "Yes"}, person("Security Desk"), {}),
    # pair 7
    ("lost", "Clothing", "Black H&M hoodie", "Plain black hoodie, size M, left on a seat after the fest rehearsal.", "Auditorium", at(6, 19), {"clothing_type": "Hoodie", "color": "Black", "size": "M", "brand": "H&M"}, person("Tanya Kapoor"), {}),
    ("found", "Clothing", "Dark hoodie left behind", "Dark grey/black hoodie found in row F of the auditorium.", "Auditorium", at(5, 10), {"clothing_type": "Hoodie", "color": "Dark grey", "size": "M"}, person("Aditya Kulkarni"), {}),
    # pair 8
    ("lost", "Jewellery / Accessories", "Silver Titan watch", "Silver analog Titan watch with a metal strap, took it off at the gym.", "Gym", at(1, 7), {"item_type": "Watch", "color": "Silver", "material": "Stainless steel"}, person("Yash Agarwal"), {}),
    ("found", "Jewellery / Accessories", "Wristwatch with metal strap", "Silver coloured watch found on the bench outside the badminton court.", "Sports Complex", at(1, 9, 30), {"item_type": "Watch", "color": "Silver"}, person("Meera Pillai"), {}),
    # pair 9
    ("lost", "Electronics", "HP laptop charger", "65W HP laptop charger with a blue pin, left in the lab.", "Academic Block 3", at(3, 15), {"device_type": "Charger", "brand": "HP", "color": "Black"}, person("Sahil Khan"), {}),
    ("found", "Electronics", "Laptop charger", "Black laptop adapter, round pin, found plugged in at the lab.", "Academic Block 3", at(3, 18), {"device_type": "Charger", "brand": "HP", "color": "Black"}, person("Lab Assistant"), {}),
    # unmatched lost reports
    ("lost", "Jewellery / Accessories", "Black frame spectacles", "Prescription glasses with a thick black frame in a brown case.", "Central Library", at(1, 17), {"item_type": "Spectacles", "color": "Black"}, person("Nikhil Bansal"), {}),
    ("lost", "Electronics", "Samsung Galaxy Buds", "Samsung Galaxy Buds 2 in a lavender case.", "Boys Hostel", at(2, 22), {"device_type": "Earbuds", "brand": "Samsung", "color": "Lavender", "has_case": "Yes"}, person("Vikram Rathore"), {}),
    ("lost", "Books / Stationery", "Data Structures notebook", "Red spiral notebook with all my DSA notes, name on the first page.", "Academic Block 2", at(4, 10), {"book_title": "DSA notes", "subject": "Data Structures", "name_written": "Ananya Jain"}, person("Ananya Jain"), {}),
    ("lost", "Bags", "Green Wildcraft backpack", "Green backpack with laptop sleeve, has a keychain of a panda.", "Dome Building", at(6, 14), {"bag_type": "Backpack", "color": "Green", "brand": "Wildcraft"}, person("Harsh Vardhan"), {}),
    # unmatched found reports
    ("found", "Bags", "Black backpack", "Black backpack with a few notebooks inside, found in the corridor.", "Dome Building", at(2, 15), {"bag_type": "Backpack", "color": "Black"}, person("Kavya Menon"), {}),
    ("found", "Other", "Blue umbrella", "Foldable navy blue umbrella left at the gate.", "Main Gate", at(3, 8), {}, person("Security Desk"), {}),
    ("found", "Jewellery / Accessories", "Pair of earrings", "Small gold coloured hoop earrings found near the hostel mess.", "Girls Hostel", at(1, 20), {"item_type": "Earrings", "color": "Gold", "material": "Metal"}, person("Priya Reddy"), {}),
    ("found", "Electronics", "OnePlus phone charger", "Red OnePlus fast charger with type-C cable.", "Food Court", at(2, 13), {"device_type": "Charger", "brand": "OnePlus", "color": "Red"}, person("Sanjay Mishra"), {}),
    ("found", "Books / Stationery", "DBMS textbook", "Database System Concepts by Korth, no name written.", "Central Library", at(4, 16), {"book_title": "Database System Concepts", "subject": "DBMS"}, person("Library Staff"), {}),
    ("found", "Clothing", "Grey cap", "Grey baseball cap with a small logo.", "Sports Complex", at(5, 18), {"clothing_type": "Cap", "color": "Grey"}, person("Kunal Shah"), {}),
]

# already returned items, so the dashboard has recovery numbers (hours until resolved)
RESOLVED = [
    ("lost", "Electronics", "Redmi phone with cracked screen", "Blue Redmi Note phone, screen cracked on the top left.", "Food Court", at(9, 12), {"device_type": "Phone", "brand": "Redmi", "color": "Blue"}, person("Simran Kaur"), 5),
    ("found", "Electronics", "Phone with cracked screen", "Blue android phone, cracked screen, found on a chair.", "Food Court", at(9, 12, 40), {"device_type": "Phone", "color": "Blue"}, person("Rahul Yadav"), 5),
    ("lost", "Clothing", "Navy blue jacket", "Navy puffer jacket, size L.", "Academic Block 3", at(8, 9), {"clothing_type": "Jacket", "color": "Navy blue", "size": "L"}, person("Aditi Sinha"), 26),
    ("found", "Clothing", "Blue puffer jacket", "Dark blue puffer jacket found in classroom 302.", "Academic Block 3", at(8, 16), {"clothing_type": "Jacket", "color": "Blue"}, person("Mohit Saini"), 26),
    ("lost", "Water Bottle", "Pink Cello bottle", "Pink plastic bottle with my name on tape.", "Girls Hostel", at(10, 8), {"color": "Pink", "brand": "Cello"}, person("Nisha Gupta"), 50),
]


def build(row, resolved_hours=None):
    kind, category, title, desc, place, when, attrs, who, extra = row
    doc = {
        "type": kind, "category": category, "title": title, "description": desc, "event_time": when,
        "location": {"name": place, "geo": {"type": "Point", "coordinates": list(config.LOCATIONS[place])}},
        "attributes": attrs, "reporter": who, "status": "open",
        "created_at": when + timedelta(minutes=40),
        **extra,
    }
    if resolved_hours:
        doc["status"] = "resolved"
        doc["resolved_at"] = when + timedelta(hours=resolved_hours)
    doc["embedding"] = embed(report_text(doc))
    return doc


def main():
    print("dropping old data...")
    for name in ("reports", "claims", "fs.files", "fs.chunks"):
        db.drop_collection(name)
    setup()
    print("embedding and inserting reports (first run downloads the model)...")
    docs = [build(r) for r in DATA] + [build(r[:8] + ({},), resolved_hours=r[8]) for r in RESOLVED]
    ids = reports.insert_many(docs).inserted_ids
    by_title = {d["title"]: i for d, i in zip(docs, ids)}
    # a couple of claims waiting for the admin
    now = datetime.now(timezone.utc)
    claims.insert_many([
        {"report_id": by_title["Wallet found under a table"], "lost_report_id": by_title["Brown leather wallet"],
         "claimant": person("Dev Patel"), "proof": "There is a photo of my dog Bruno in the front slot and my SBI card.",
         "status": "pending", "created_at": now - timedelta(hours=3)},
        {"report_id": by_title["Scientific calculator"], "lost_report_id": None,
         "claimant": person("Kriti Malhotra"), "proof": "It is a grey Casio, I think I left it there last week.",
         "status": "pending", "created_at": now - timedelta(hours=1)},
    ])
    print(f"inserted {len(ids)} reports and 2 claims")


if __name__ == "__main__":
    main()
