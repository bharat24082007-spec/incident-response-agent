"""Load the curated incident history into the Hindsight incidents bank."""

from memory import ensure_bank, retain_incident
from seed_data import INCIDENTS


def main():
    ensure_bank()
    for index, incident in enumerate(INCIDENTS, start=1):
        # retain_incident uses the stable incident ID as document_id, so reruns replace these records.
        retain_incident(incident)
        print(f"Loaded {incident['id']} ({index}/{len(INCIDENTS)})")
    print(f"Loaded {len(INCIDENTS)} incidents into the Hindsight bank.")


if __name__ == "__main__":
    main()
