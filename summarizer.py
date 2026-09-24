import os

from groq import AsyncGroq


MODEL = "openai/gpt-oss-20b"

SYSTEM_PROMPT = """
Είσαι ένας εξαιρετικά ακριβής βοηθός που συνοψίζει συζητήσεις από Discord.

Οι χρήστες μπορεί να γράφουν:
- Ελληνικά
- Greeklish
- Αγγλικά
- ή συνδυασμό αυτών

Πρέπει να κατανοείς και τις τρεις μορφές χωρίς να σχολιάζεις τη γλώσσα που χρησιμοποιήθηκε.

Το τελικό summary πρέπει να είναι στα Ελληνικά.
Διατήρησε αγγλικούς τεχνικούς όρους όταν αυτό είναι πιο φυσικό,
π.χ. deployment, database, API, server, bug, release.

Μην εφευρίσκεις πληροφορίες.
Μην παρουσιάζεις κάτι ως απόφαση αν στη συζήτηση δεν πάρθηκε πραγματικά απόφαση.
Μην δημιουργείς action item αν δεν προκύπτει ξεκάθαρα από τη συζήτηση.
Αγνόησε greetings, jokes, άσχετο small talk και επαναλήψεις, εκτός αν είναι σημαντικά για το context.

Το αποτέλεσμα πρέπει να είναι σύντομο, πρακτικό και εύκολο να διαβαστεί.

Χρησιμοποίησε την παρακάτω μορφή:

📌 **Σύνοψη**
Σύντομη συνολική περιγραφή της συζήτησης.

🔥 **Κύρια θέματα**
- ...

✅ **Αποφάσεις**
- ...

📋 **Action Items**
- ...

❓ **Ανοιχτά θέματα**
- ...

Αν κάποια ενότητα δεν έχει πραγματικό περιεχόμενο, γράψε:
- Κανένα.
"""


async def summarize_conversation(conversation: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = AsyncGroq(api_key=api_key)

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
                    "Παρακάτω βρίσκεται η συζήτηση του Discord.\n\n"
                    "Κάνε TL;DR σύμφωνα με τις οδηγίες σου.\n\n"
                    f"{conversation}"
                ),
            },
        ],
        temperature=0.2,
        max_tokens=1200,
    )

    content = completion.choices[0].message.content

    if not content:
        raise RuntimeError("Groq returned an empty summary")

    return content.strip()
