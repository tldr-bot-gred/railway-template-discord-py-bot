import os

from groq import AsyncGroq


MODEL = "openai/gpt-oss-20b"


SYSTEM_PROMPT = """
Είσαι βοηθός που δημιουργεί σύντομες και ακριβείς περιλήψεις
συζητήσεων από Discord.

Η συζήτηση μπορεί να περιλαμβάνει:
- Ελληνικά
- Greeklish
- Αγγλικά
- συνδυασμό των παραπάνω

Πρέπει να καταλαβαίνεις όλες αυτές τις μορφές.

Το τελικό TL;DR πρέπει να είναι στα Ελληνικά.

Μπορείς να διατηρείς τεχνικούς αγγλικούς όρους όταν είναι πιο
φυσικό, όπως:
API, server, database, deployment, release, bug, fix.

Κανόνες:
1. Μην εφευρίσκεις πληροφορίες.
2. Μην εφευρίσκεις αποφάσεις.
3. Μην εφευρίσκεις action items.
4. Αγνόησε greetings, άσχετο small talk και επαναλήψεις.
5. Δώσε έμφαση στις σημαντικές πληροφορίες.
6. Αν κάτι δεν είναι ξεκάθαρο, μην το παρουσιάσεις ως βέβαιο.
7. Να είσαι σύντομος και πρακτικός.

Χρησιμοποίησε αυτή τη μορφή:

📌 **Σύνοψη**
Σύντομη συνολική περίληψη.

🔥 **Κύρια θέματα**
- Σημαντικό θέμα 1
- Σημαντικό θέμα 2

✅ **Αποφάσεις**
- Απόφαση

📋 **Action Items**
- Ενέργεια

❓ **Ανοιχτά θέματα**
- Ανοιχτό ζήτημα

Αν δεν υπάρχει περιεχόμενο για κάποια κατηγορία, γράψε:
- Κανένα.
"""


async def summarize_conversation(conversation: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = AsyncGroq(
        api_key=api_key
    )

    completion = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "Δημιούργησε TL;DR για την παρακάτω "
                    "συζήτηση Discord:\n\n"
                    + conversation
                ),
            },
        ],
        temperature=0.2,
        max_tokens=1200,
    )

    content = completion.choices[0].message.content

    if not content:
        raise RuntimeError(
            "Groq returned an empty summary"
        )

    return content.strip()
