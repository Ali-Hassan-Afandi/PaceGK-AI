from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def send(body: str, sender: str):
    return client.post(
        "/whatsapp/webhook",
        data={
            "Body": body,
            "From": f"whatsapp:{sender}",
            "To": "whatsapp:+14155238886",
            "MessageSid": "test-sid",
        },
    )


# 1. Student asks about admission / sends batch code NOV26-1A -> BATCH_CONFIRMED
#    (admission agent asks for admission details + fee paid picture).
r1 = send("NOV26-1A", "03333333333")
print("TEST1 student batch reply:", r1.status_code, r1.json())

# 2. Student submits admission details -> agent moves to fee submission
#    (asks the student to submit the fee and send the fee paid screenshot).
r2 = send("Please send me the admission details", "03333333333")
print("TEST2 student form reply:", r2.status_code, r2.json())

# 3. Admin approves the student (admin number 03367483185, test student 03333333333)
#    -> fee verified -> admission batch data sheet prepared.
r3 = send("APPROVE 03333333333 NEW", "03367483185")
print("TEST3 admin approve reply:", r3.status_code, r3.json())

# 4. Health check
r4 = client.get("/health")
print("TEST4 health:", r4.status_code, r4.json())
