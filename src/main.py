import sys

from dotenv import load_dotenv

from src.orchestration.loop import SearchLoop


def main():
    load_dotenv()

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    if not query:
        query = input("Enter your search query: ").strip()
    if not query:
        print("No query provided.")
        sys.exit(1)

    loop = SearchLoop()
    state = loop.run(query)

    print(f"\n{'='*60}")
    print(" FINAL ANSWER")
    print(f"{'='*60}\n")
    print(state.final_answer or "No answer could be generated.")

    print(f"\n{'='*60}")
    print(f" Summary: {state.iteration} iteration(s), "
          f"{len(state.results)} total results, "
          f"{'sufficient' if state.is_sufficient else 'max iterations reached'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
