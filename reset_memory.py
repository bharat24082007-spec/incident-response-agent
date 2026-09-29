"""Replace all incident memory with exactly the curated seed incidents."""

from memory import ensure_bank, reset_bank, retain_incident
from seed_data import INCIDENTS


def main():
    reset_bank()
    ensure_bank()
    for index, incident in enumerate(INCIDENTS, start=1):
        retain_incident(incident)
        print(f"Loaded {incident['id']} ({index}/{len(INCIDENTS)})")
    print(f"Reset complete: loaded exactly {len(INCIDENTS)} seed incidents.")


if __name__ == "__main__":
    main()
