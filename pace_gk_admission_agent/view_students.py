"""View all students in the SQLite database in a readable format.

Run from the pace_gk_admission_agent folder:
    .\.venv\Scripts\python.exe view_students.py
"""
import sqlite3

db_path = "pace_gk_admission_agent.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Show all tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("TABLES:", tables)
print()

# Show all students with key columns
print("=" * 100)
print("STUDENTS")
print("=" * 100)
cur.execute(
    "SELECT id, phone_number, name, father_name, domicile, id_card_number, "
    "roll_number, student_pic_url, fee_paid_pic_url, batch_id, status, "
    "fee_category, fee_amount, fee_paid, submitted_at, approved_by, approved_at "
    "FROM students ORDER BY id"
)
rows = cur.fetchall()
if not rows:
    print("(no students in database yet)")
else:
    cols = [d[0] for d in cur.description]
    # Print header
    print(" | ".join(cols))
    print("-" * 100)
    for r in rows:
        values = [str(r[c]) if r[c] is not None else "" for c in cols]
        print(" | ".join(values))

print()
print("=" * 100)
print("BATCHES")
print("=" * 100)
cur.execute("SELECT id, batch_code, name, community_link, fee_amount FROM batches ORDER BY id")
rows = cur.fetchall()
if not rows:
    print("(no batches)")
else:
    cols = [d[0] for d in cur.description]
    print(" | ".join(cols))
    print("-" * 100)
    for r in rows:
        values = [str(r[c]) if r[c] is not None else "" for c in cols]
        print(" | ".join(values))

conn.close()
